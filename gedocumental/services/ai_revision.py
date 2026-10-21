"""
Revisión automática de documentos de facturación con IA.

Lee el PDF o imagen subida (texto si es digital, imagen si es escaneado/foto), lo compara
con los datos reales de la admisión en ZeusSalud (paciente, identificación,
EPS, CUPS) y lo envía a GPT-4o-mini para detectar inconsistencias según los
tipos de observación SIESA:
  OK | AUTORIZACION | SOPORTES | REPORTE | FARMACIA | TARIFA |
  CONTRATO | INFORMACION | RECAUDO

Si se detecta un problema el archivo queda en estado RECHAZADA y se crea
un registro en ObservacionesArchivos para que regrese al usuario que lo subió.
Si la API key no está configurada la función devuelve None sin lanzar error.
"""

import base64
import io
import json
import logging
import os

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)

# Tipos de observación válidos — deben coincidir exactamente con los que
# usa SIESA para que la descripción sea interpretable por Cuentas Médicas.
TIPOS_OBSERVACION = [
    'OK', 'AUTORIZACION', 'SOPORTES', 'REPORTE',
    'FARMACIA', 'TARIFA', 'CONTRATO', 'INFORMACION', 'RECAUDO',
]

# Por debajo de este número de caracteres el PDF se trata como escaneado
MIN_CARACTERES_TEXTO = 50
# Páginas máximas que se envían como imagen (controla costo y tiempo)
MAX_PAGINAS_IMAGEN = 3
# Formatos que se revisan: PDF (digital, escaneado o mixto) e imágenes sueltas
EXTENSIONES_IMAGEN = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')
EXTENSIONES_REVISABLES = ('.pdf',) + EXTENSIONES_IMAGEN
# Lado máximo en píxeles de una foto antes de enviarla (fotos de celular son enormes)
MAX_LADO_IMAGEN = 2000
# Tipos de documento en los que hay que VER la página aunque tenga texto (firma manuscrita)
TIPOS_CON_FIRMA = ('COMPROBANTE',)

PROMPT_SISTEMA = """Eres un auditor experto en facturación de servicios de salud en Colombia.
Revisas documentos de gestión documental (factura, comprobante, autorización, orden médica, resultado/informe, etc.)
que se cargan a una ADMISIÓN. Te doy los datos reales de la admisión tomados del sistema; el documento DEBE corresponder a ella.

VERIFICACIONES OBLIGATORIAS (en este orden):
1. PACIENTE: el nombre y/o número de identificación que aparece en el documento debe ser el mismo paciente de la admisión.
   Tolera tildes, mayúsculas, orden de nombres/apellidos y un segundo nombre faltante, pero NO un paciente distinto.
   Si es otra persona → tipo INFORMACION, indicando ambos nombres.
   Si el documento NO trae nombre ni identificación del paciente, eso NO es un error por sí solo
   (paciente_coincide = NO_APARECE): sigue con las demás verificaciones.
2. PROCEDIMIENTOS: en órdenes médicas, autorizaciones y resultados, los procedimientos/CUPS del documento deben
   corresponder a los CUPS de la admisión. Compara por código CUPS y, si no hay código, por la descripción.
   - Orden médica que no incluye los procedimientos de la admisión → SOPORTES
   - Autorización que no cubre los procedimientos de la admisión → AUTORIZACION
   - Resultado/informe de un procedimiento distinto al de la admisión → REPORTE
   Indica qué trae el documento y qué esperaba la admisión.
   Compara el ESTUDIO, no el texto literal: la descripción del CUPS casi nunca se escribe igual en los
   documentos. Son EQUIVALENTES (no es error):
     TAC = TC = tomografía computada = tomografía computarizada = escanografía = scanner;
     RM = RMN = resonancia magnética = resonancia nuclear magnética;
     cráneo ≈ cerebro ≈ encéfalo (para TAC/RM de cabeza); "simple" = sin contraste (puede no mencionarse);
     EMG = electromiografía; neuroconducción = velocidad de conducción nerviosa = VCN = estudio de neuroconducción;
     EEG = electroencefalograma; PSG = polisomnografía; ecografía = ultrasonido = ecografía doppler cuando el CUPS es doppler;
     RX = radiografía. También son normales abreviaturas, órdenes distintos de palabras y textos más cortos o más largos.
   SOLO es error de procedimiento cuando cambia algo de fondo:
     - otro tipo de estudio (resonancia vs tomografía, EMG vs EEG);
     - otra región anatómica (resonancia de abdomen vs resonancia de cerebro; columna lumbar vs cervical);
     - otra LATERALIDAD (estudio de miembro derecho y la orden pide el izquierdo);
     - con contraste explícito cuando el CUPS es simple, o al revés.
   Ante la duda razonable de que sea el mismo estudio, considéralo coincidente (procedimientos_coinciden = SI).
3. Solo si 1 y 2 están bien, revisa el resto según la tabla de convenciones (fechas, vigencia, cantidades,
   firmas, legibilidad, datos incompletos).
Si hay varios problemas, el tipo es el del primero según este orden, y en la descripción menciona también los demás de forma breve.
No marques como error que el documento no traiga un dato que normalmente no lleva ese tipo de documento.

Tipos de observación (convenciones del informe SIESA de auditoría de facturas; en mayúsculas sin tildes):
- OK: soportes correctamente cargados e información validada.
- AUTORIZACION: no cuenta con número de autorización; la autorización no coincide con la información del
  paciente y/o el procedimiento a facturar; inconsistencia en las cantidades autorizadas; autorización no
  legible; autorización vencida.
- SOPORTES: ausencia de soportes en su totalidad o parte de ellos (historia clínica si aplica, autorización,
  orden médica, comprobante); incoherencia en soportes (no corresponde la información del paciente);
  orden médica vencida; ausencia de firma en el comprobante de la atención (SOLO si el documento
  cargado es tipo COMPROBANTE).
- REPORTE: incoherencia en el reporte (no corresponde al procedimiento realizado); ausencia del reporte;
  error en lateralidad (ej.: tomado en derecho y la orden pide izquierdo).
- FARMACIA: ausencia de cargue o relación de insumos y/o medicamentos suministrados al paciente.
- TARIFA: incoherencia en el valor a cobrar según el anexo tarifario pactado.
- CONTRATO: incoherencia en el contrato en el que se clasifica el usuario.
- INFORMACION: información incorrecta del paciente (tipo de documento, número de documento, tipo de usuario,
  cobertura, régimen, nombre del usuario); ausencia de vale.
- RECAUDO: ausencia de cobro de cuota moderadora o copago; cobro incompleto de cuota moderadora o copago;
  error en el tipo de cobro generado.

Responde ÚNICAMENTE con un JSON válido sin texto adicional. Completa SIEMPRE las dos verificaciones,
aunque la primera ya haya fallado:
{
  "paciente_documento": "<nombre e identificación tal como aparecen en el documento, o 'no aparece'>",
  "nombre_documento": "<solo el nombre completo del paciente tal como aparece, o ''>",
  "identificacion_documento": "<solo el número de documento del paciente, sin tipo, o ''>",
  "paciente_coincide": "SI | NO | NO_APARECE",
  "procedimientos_documento": "<CUPS/procedimientos que trae el documento, o 'no aplica'>",
  "procedimientos_coinciden": "SI | NO | NO_APLICA",
  "firma_paciente": "SI | NO | NO_APLICA   (solo COMPROBANTE; en otros documentos NO_APLICA)",
  "estudio_documento": {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>"},
  "estudio_admision":  {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>"},
  "tipo": "<uno de los tipos de arriba>",
  "descripcion": "<máximo 220 caracteres, concreta, citando el dato del documento y el esperado, incluyendo todos los problemas encontrados; o 'Documento correcto' si es OK>"
}
"estudio_documento" = el estudio principal que trae el documento; "estudio_admision" = el CUPS de la admisión
que más se le parece. Usa SOLO estos valores (en mayúsculas):
  M (modalidad): TAC | RM | RX | ECOGRAFIA | EMG | NEUROCONDUCCION | EMG_NEUROCONDUCCION | EEG | PSG |
                 POTENCIALES_EVOCADOS | CONSULTA | LABORATORIO | PROCEDIMIENTO | OTRO | NO_APLICA
                 (TAC = tomografía computada/computarizada/escanografía; RM = resonancia magnética)
  R (región):    CABEZA (cráneo, cerebro, encéfalo, silla turca, órbitas) | CUELLO | COLUMNA_CERVICAL |
                 COLUMNA_DORSAL | COLUMNA_LUMBAR | COLUMNA_TOTAL | TORAX | ABDOMEN | PELVIS |
                 MIEMBRO_SUPERIOR | MIEMBRO_INFERIOR | CUERPO_ENTERO | OTRA | NO_APLICA
  L (lateralidad): DERECHA | IZQUIERDA | BILATERAL | NO_ESPECIFICA
  C (contraste):   CON | SIN | NO_ESPECIFICA   ("simple" = SIN)
Si el documento no trae procedimiento (comprobante, factura…), usa NO_APLICA en ambos."""


def obtener_contexto_admision(consecutivo) -> dict:
    """
    Consulta en ZeusSalud los datos de la admisión contra los que se valida el documento.
    Devuelve un dict con lo que encuentre; nunca lanza excepción.
    """
    datos = {"consecutivo": consecutivo}
    try:
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute(
                """
                SELECT TOP 1
                    sp.tipo_id, sp.num_id,
                    sp.primer_nom, sp.segundo_nom, sp.primer_ape, sp.segundo_ape,
                    se.nombre AS entidad, c.alias AS contrato, sm.fecha_ing
                FROM sis_maes sm
                LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
                LEFT JOIN sis_empre se ON LTRIM(RTRIM(sm.EPSPaciente)) = LTRIM(RTRIM(se.codigo))
                LEFT JOIN contratos c ON sm.contrato = c.codigo
                WHERE sm.con_estudio = %s
                """,
                [consecutivo],
            )
            fila = cursor.fetchone()
            if fila:
                nombre = ' '.join((p or '').strip() for p in fila[2:6] if (p or '').strip())
                datos.update({
                    "paciente": nombre,
                    "identificacion": f"{(fila[0] or '').strip()} {(fila[1] or '').strip()}".strip(),
                    "entidad": (fila[6] or '').strip(),
                    "contrato": (fila[7] or '').strip(),
                    "fecha": str(fila[8].date()) if fila[8] else '',
                })

            cursor.execute(
                """
                SELECT LTRIM(RTRIM(sd.cups)), LTRIM(RTRIM(sd.descripcion)), sd.cantidad
                FROM sis_deta sd
                WHERE sd.estudio = %s
                """,
                [consecutivo],
            )
            datos["cups"] = [
                f"{cups} - {desc} (cant. {int(cant) if cant is not None else 1})"
                for cups, desc, cant in cursor.fetchall()
            ]
    except Exception as exc:
        logger.warning("No se pudo obtener contexto de la admisión %s: %s", consecutivo, exc)
    return datos


def _leer_pdf(ruta_absoluta: str, siempre_imagen: bool = False) -> tuple[str, list[str]]:
    """
    Devuelve (texto, imagenes_base64), decidiendo página por página: las que
    tienen texto se leen como texto; las escaneadas o fotos guardadas en PDF
    se renderizan como PNG (hasta MAX_PAGINAS_IMAGEN). Así un PDF mixto
    (factura digital + autorización escaneada) se revisa completo.
    siempre_imagen: además del texto, envía la imagen de las páginas (para ver la firma).
    """
    try:
        import pdfplumber
        textos, imagenes = [], []
        with pdfplumber.open(ruta_absoluta) as pdf:
            for num, pagina in enumerate(pdf.pages, start=1):
                texto_pagina = (pagina.extract_text() or "").strip()
                tiene_texto = len(texto_pagina) >= MIN_CARACTERES_TEXTO
                if tiene_texto:
                    textos.append(f"[Página {num}]\n{texto_pagina}")
                if (not tiene_texto or siempre_imagen) and len(imagenes) < MAX_PAGINAS_IMAGEN:
                    buffer = io.BytesIO()
                    pagina.to_image(resolution=200).original.convert("RGB").save(buffer, format="PNG")
                    imagenes.append(base64.b64encode(buffer.getvalue()).decode())
        return "\n\n".join(textos), imagenes
    except Exception as exc:
        logger.warning("No se pudo leer el PDF %s: %s", ruta_absoluta, exc)
        return "", []


def _leer_imagen(ruta_absoluta: str) -> tuple[str, list[str]]:
    """Foto o imagen subida directamente (JPG, PNG...). Devuelve ("", [png_base64])."""
    try:
        from PIL import Image, ImageOps
        with Image.open(ruta_absoluta) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")  # respeta la rotación del celular
            img.thumbnail((MAX_LADO_IMAGEN, MAX_LADO_IMAGEN))
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
        return "", [base64.b64encode(buffer.getvalue()).decode()]
    except Exception as exc:
        logger.warning("No se pudo leer la imagen %s: %s", ruta_absoluta, exc)
        return "", []


def _leer_documento(ruta_absoluta: str, tipo_documento: str = '') -> tuple[str, list[str]]:
    if ruta_absoluta.lower().endswith(EXTENSIONES_IMAGEN):
        return _leer_imagen(ruta_absoluta)
    return _leer_pdf(ruta_absoluta, siempre_imagen=(tipo_documento or '').upper() in TIPOS_CON_FIRMA)


def _formatear_contexto(datos_admision: dict) -> str:
    cups = datos_admision.get('cups') or []
    lineas_cups = "\n".join(f"  - {c}" for c in cups) if cups else "  (no disponibles)"
    return (
        f"Admisión/estudio: {datos_admision.get('consecutivo', 'N/A')}\n"
        f"Paciente: {datos_admision.get('paciente') or 'N/A'}\n"
        f"Identificación: {datos_admision.get('identificacion') or 'N/A'}\n"
        f"EPS/Entidad: {datos_admision.get('entidad') or 'N/A'}\n"
        f"Contrato: {datos_admision.get('contrato') or 'N/A'}\n"
        f"Fecha de ingreso: {datos_admision.get('fecha') or 'N/A'}\n"
        f"CUPS de la admisión:\n{lineas_cups}"
    )


def _llamar_openai(texto: str, imagenes: list[str], tipo_documento: str, datos_admision: dict,
                   _reintento: bool = False, _contenido=None, firma_detectada: bool | None = None) -> dict:
    """
    Llama a GPT-4o-mini y devuelve {"tipo": ..., "descripcion": ...}.
    Puede lanzar excepciones si la API falla.
    _reintento/_contenido: uso interno para repetir la consulta con una nota del sistema.
    """
    from openai import OpenAI

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        raise ValueError("OPENAI_API_KEY no configurada en settings")

    client = OpenAI(api_key=api_key)

    encabezado = (
        f"TIPO DE DOCUMENTO CARGADO: {tipo_documento}\n\n"
        f"DATOS REALES DE LA ADMISIÓN (sistema):\n{_formatear_contexto(datos_admision)}\n\n"
    )
    if _es_comprobante(tipo_documento):
        encabezado += (
            "VERIFICACIÓN ADICIONAL (este documento es un COMPROBANTE): mira la imagen y verifica que haya una "
            "firma o trazo manuscrito del paciente (o su acompañante) en el espacio \"Firma del paciente\". "
            "El texto impreso \"Firma del paciente:\" NO es una firma. Si el espacio está vacío → SOPORTES "
            "\"Ausencia de firma en el comprobante de la atención\". "
            "En el comprobante la sección de procedimientos puede venir vacía: eso NO es error "
            "(procedimientos_coinciden = NO_APLICA); solo compáralos si el comprobante los trae.\n\n"
        )
    else:
        encabezado += (
            "Este documento NO es un comprobante: no verifiques firmas del paciente (firma_paciente = NO_APLICA).\n\n"
        )
    if imagenes:
        texto_digital = f"Páginas con texto:\n{texto[:6000]}\n\n" if texto else ""
        contenido = [{"type": "text", "text": (
            encabezado + "CONTENIDO DEL DOCUMENTO:\n" + texto_digital
            + "Imágenes de las páginas (escaneadas, o para ver firmas): ver imágenes adjuntas."
        )}]
        contenido += [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}", "detail": "high"}}
            for img in imagenes
        ]
    else:
        contenido = encabezado + f"CONTENIDO DEL DOCUMENTO:\n{texto[:6000]}"
    if _contenido is not None:
        contenido = _contenido

    respuesta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user",   "content": contenido},
        ],
        response_format={"type": "json_object"},
        max_tokens=500,
        temperature=0,
    )

    resultado = json.loads(respuesta.choices[0].message.content)

    # Normalizar el tipo por si la IA devuelve algo inesperado
    tipo = resultado.get("tipo", "").upper().strip()
    if tipo not in TIPOS_OBSERVACION:
        tipo = "INFORMACION"
    resultado["tipo"] = tipo

    if not _es_comprobante(tipo_documento) and resultado["tipo"] != "OK":
        desc = (resultado.get("descripcion") or "").lower()
        if "firma" in desc and "comprobante" in desc:
            if resultado.get("paciente_coincide") != "NO" and resultado.get("procedimientos_coinciden") != "NO":
                logger.info("Descartado hallazgo de firma en documento %s (no es comprobante)", tipo_documento)
                resultado.update(tipo="OK", descripcion="Documento correcto", ajuste_firma=True)

    # Verificaciones del sistema sobre lo que la IA marcó como error. La IA a veces falla por la
    # forma de escribir (nombre invertido o con guiones, "TAC de cráneo" vs "tomografía computada
    # de cráneo simple", comprobante sin procedimientos). Si el sistema confirma que está bien,
    # se repite la consulta UNA vez indicándolo, para que reporte solo otros problemas.
    notas, ajustes = [], {}
    if resultado.get("paciente_coincide") == "NO" and _mismo_paciente(resultado, datos_admision):
        notas.append("el PACIENTE del documento SÍ es el de la admisión (mismo número de documento o mismo "
                     "nombre escrito en otro orden, con guiones/comas o con un error menor de digitación): "
                     "paciente_coincide = SI")
        ajustes["paciente_coincide"] = "SI"
    if (resultado.get("procedimientos_coinciden") == "NO"
            and _mismo_estudio(resultado.get("estudio_documento"), resultado.get("estudio_admision"))):
        notas.append("el PROCEDIMIENTO del documento SÍ corresponde al de la admisión (mismo tipo de estudio, "
                     "región, lateralidad y contraste; solo cambia la forma de escribirlo): procedimientos_coinciden = SI")
        ajustes["procedimientos_coinciden"] = "SI"
    if (_es_comprobante(tipo_documento) and resultado.get("procedimientos_coinciden") == "NO"
            and _sin_dato(resultado.get("procedimientos_documento"))):
        notas.append("el COMPROBANTE no trae procedimientos y eso NO es un error: procedimientos_coinciden = NO_APLICA")
        ajustes["procedimientos_coinciden"] = "NO_APLICA"

    if (_es_comprobante(tipo_documento) and firma_detectada
            and str(resultado.get("firma_paciente", "")).upper().startswith("NO")
            and not str(resultado.get("firma_paciente", "")).upper().startswith("NO_APLICA")):
        notas.append("el sistema detectó en el PDF la imagen de la FIRMA manuscrita del paciente (puede estar al "
                     "final de la hoja, debajo de otro rótulo \"Firma del paciente\"): firma_paciente = SI")
        ajustes["firma_paciente"] = "SI"

    if notas and not _reintento and resultado["tipo"] != "OK":
        logger.info("Verificación del sistema corrige a la IA (%s); se repite la revisión", ", ".join(ajustes))
        nota = ("\n\nVERIFICADO POR EL SISTEMA: " + "; ".join(notas) + ". No reportes esto como error. "
                "Reporta únicamente otros problemas, si los hay; si no hay ninguno, el tipo es OK.")
        if isinstance(contenido, list):
            contenido_2 = [dict(contenido[0], text=contenido[0]["text"] + nota)] + contenido[1:]
        else:
            contenido_2 = contenido + nota
        resultado = _llamar_openai(texto, imagenes, tipo_documento, datos_admision,
                                   _reintento=True, _contenido=contenido_2, firma_detectada=firma_detectada)
        resultado.update(ajustes)
        resultado["ajuste_sistema"] = list(ajustes)

    return resultado


def _sin_dato(valor) -> bool:
    v = (str(valor or '')).strip().lower()
    return v in ('', 'no aplica', 'no aparece', 'ninguno', 'n/a', 'na', '-') or v.startswith('no ')


def _tokens_nombre(texto) -> list[str]:
    """'MARQUEZ_ACERO,PEDRO_JULIO' -> ['marquez', 'acero', 'pedro', 'julio'] (sin tildes ni signos)."""
    import re
    import unicodedata
    t = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'[^a-z\s]', ' ', t.replace('_', ' '))
    return [p for p in t.split() if len(p) >= 3 and p not in ('del', 'los', 'las', 'cc', 'ti', 'rc')]


def _mismo_paciente(resultado: dict, datos_admision: dict) -> bool:
    """
    True si el documento es del paciente de la admisión aunque la IA diga que no:
    mismo número de documento, o todos los nombres del documento están en el de la
    admisión (en cualquier orden, tolerando un error menor de digitación por palabra).
    """
    import re
    from difflib import SequenceMatcher

    id_adm = re.sub(r'\D', '', datos_admision.get('identificacion') or '')
    id_doc = re.sub(r'\D', '', str(resultado.get('identificacion_documento') or ''))
    if id_adm and id_doc and len(id_doc) >= 5:
        if id_doc == id_adm:
            return True
        return False  # número distinto: es otra persona aunque el nombre se parezca

    adm = _tokens_nombre(datos_admision.get('paciente'))
    doc = _tokens_nombre(resultado.get('nombre_documento') or resultado.get('paciente_documento'))
    if len(doc) < 2 or not adm:
        return False
    disponibles = list(adm)
    for palabra in doc:
        mejor = max(disponibles, key=lambda a: SequenceMatcher(None, palabra, a).ratio(), default=None)
        if mejor is None or SequenceMatcher(None, palabra, mejor).ratio() < 0.8:
            return False
        disponibles.remove(mejor)
    return True


def _detectar_firma_pdf(ruta_absoluta: str) -> bool | None:
    """
    Comprobantes digitales de Zeus: la firma del paciente va como imagen dentro del PDF.
    Devuelve True si alguna imagen pequeña (tamaño de firma) tiene trazo visible,
    False si las hay pero están en blanco, None si no se puede saber (escaneado, sin imágenes).
    """
    if not ruta_absoluta.lower().endswith('.pdf'):
        return None
    try:
        import pdfplumber
        with pdfplumber.open(ruta_absoluta) as pdf:
            vistas = 0
            for pagina in pdf.pages[:2]:
                for img in pagina.images:
                    ancho, alto = img['x1'] - img['x0'], img['bottom'] - img['top']
                    # Una página escaneada completa no es una firma
                    if ancho > pagina.width * 0.6 or alto > pagina.height * 0.4 or ancho < 15 or alto < 8:
                        continue
                    vistas += 1
                    region = pagina.crop((max(img['x0'], 0), max(img['top'], 0),
                                          min(img['x1'], pagina.width), min(img['bottom'], pagina.height)))
                    gris = region.to_image(resolution=100).original.convert('L')
                    pixeles = list(gris.getdata())
                    oscuros = sum(1 for v in pixeles if v < 128)
                    if pixeles and oscuros / len(pixeles) > 0.004:
                        return True
            return False if vistas else None
    except Exception as exc:
        logger.warning("No se pudo analizar la firma de %s: %s", ruta_absoluta, exc)
        return None


def _es_comprobante(tipo_documento) -> bool:
    return (tipo_documento or '').upper() in TIPOS_CON_FIRMA


def _mismo_estudio(doc, adm) -> bool:
    """True si la IA describió el estudio del documento y el de la admisión como el mismo."""
    if not isinstance(doc, dict) or not isinstance(adm, dict):
        return False

    def val(d, k):
        return str(d.get(k) or '').upper().strip()

    mod_d, mod_a = val(doc, 'modalidad'), val(adm, 'modalidad')
    if not mod_d or not mod_a or 'OTRO' in (mod_d, mod_a) or 'NO_APLICA' in (mod_d, mod_a):
        return False
    combinado = {'EMG', 'NEUROCONDUCCION', 'EMG_NEUROCONDUCCION'}
    if mod_d != mod_a and not (mod_d in combinado and mod_a in combinado):
        return False

    def compatibles(a, b, comodines):
        return a == b or a in comodines or b in comodines

    return (compatibles(val(doc, 'region'), val(adm, 'region'), {'', 'NO_APLICA'})
            and compatibles(val(doc, 'lateralidad'), val(adm, 'lateralidad'), {'', 'NO_ESPECIFICA'})
            and compatibles(val(doc, 'contraste'), val(adm, 'contraste'), {'', 'NO_ESPECIFICA'}))


def analizar_documento(ruta_absoluta: str, tipo_documento: str, datos_admision: dict) -> dict | None:
    """
    Lee el PDF o la imagen y lo envía a la IA. No toca la base de datos.
    Devuelve None si no se pudo leer. Puede lanzar excepciones de la API.
    """
    texto, imagenes = _leer_documento(ruta_absoluta, tipo_documento)
    if not texto and not imagenes:
        logger.info("PDF sin contenido legible: %s", ruta_absoluta)
        return None
    firma = _detectar_firma_pdf(ruta_absoluta) if _es_comprobante(tipo_documento) else None
    resultado = _llamar_openai(texto, imagenes, tipo_documento, datos_admision, firma_detectada=firma)
    resultado["modo"] = ("mixto" if texto else "imagen") if imagenes else "texto"
    return resultado


def revisar_archivo(archivo_obj, datos_admision: dict) -> dict | None:
    """
    Punto de entrada principal.

    Parámetros:
      archivo_obj   : instancia de ArchivoFacturacion ya guardada en DB
      datos_admision: dict de obtener_contexto_admision()

    Retorna:
      {"tipo": "AUTORIZACION", "descripcion": "...", "modo": "texto|imagen|mixto"} si hay observación
      {"tipo": "OK", "descripcion": "Documento correcto", ...}                si está bien
      None si la revisión no pudo ejecutarse (sin API key, PDF ilegible, error de red)

    Efectos secundarios:
      - Registra el resultado en RevisionIA (tabla revision_ia), incluso si es OK
      - Cierra las revisiones pendientes anteriores del mismo archivo
        (CORREGIDO si la nueva sale OK, REEMPLAZADO si no)
    No modifica EstadoRevision ni TipoHallazgo del archivo: la IA solo avisa,
    no bloquea el flujo de Cuentas Médicas.
    """

    # Interruptor de emergencia — si REVISAR_CON_IA = False en settings, no hace nada
    if not getattr(settings, 'REVISAR_CON_IA', True):
        return None

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        logger.info("Revisión IA omitida: OPENAI_API_KEY no configurada")
        return None

    # Obtener la ruta absoluta del archivo en disco
    try:
        ruta_absoluta = archivo_obj.RutaArchivo.path
    except Exception:
        logger.warning("No se pudo obtener la ruta del archivo %s", archivo_obj.IdArchivo)
        return None

    if not os.path.exists(ruta_absoluta):
        logger.warning("Archivo no encontrado en disco: %s", ruta_absoluta)
        return None

    # Solo revisamos PDFs e imágenes
    if not ruta_absoluta.lower().endswith(EXTENSIONES_REVISABLES):
        return None

    try:
        resultado = analizar_documento(ruta_absoluta, archivo_obj.Tipo, datos_admision)
    except Exception as exc:
        logger.error("Error al llamar a OpenAI para archivo %s: %s", archivo_obj.IdArchivo, exc)
        return None
    if resultado is None:
        return None

    _registrar_revision(archivo_obj, resultado)
    return resultado


def _registrar_revision(archivo_obj, resultado: dict) -> None:
    from django.utils import timezone
    from gedocumental.models import RevisionIA

    try:
        RevisionIA.objects.filter(IdArchivo=archivo_obj, Resuelta=False).update(
            Resuelta=True,
            Resolucion='CORREGIDO' if resultado["tipo"] == "OK" else 'REEMPLAZADO',
            FechaResolucion=timezone.now(),
        )
        # "Reemplazar" en GE Documental borra el archivo y sube uno nuevo del mismo tipo:
        # el hallazgo cerrado como ARCHIVO_ELIMINADO pasa a CORREGIDO (o REEMPLAZADO si el nuevo también falla)
        RevisionIA.objects.filter(
            AdmisionId=archivo_obj.Admision_id, TipoDocumento=archivo_obj.Tipo or '',
            Resolucion='ARCHIVO_ELIMINADO',
        ).exclude(TipoHallazgo='OK').update(
            Resolucion='CORREGIDO' if resultado["tipo"] == "OK" else 'REEMPLAZADO',
        )
        # Si llegó el RESULTADO, se cierra el hallazgo de "ausencia del reporte" de la admisión
        if (archivo_obj.Tipo or '').upper() == 'RESULTADO':
            RevisionIA.objects.filter(
                AdmisionId=archivo_obj.Admision_id, TipoDocumento='RESULTADO',
                IdArchivo__isnull=True, Resuelta=False,
            ).update(Resuelta=True, Resolucion='CORREGIDO', FechaResolucion=timezone.now())
        RevisionIA.objects.create(
            IdArchivo=archivo_obj,
            AdmisionId=archivo_obj.Admision_id,
            TipoDocumento=archivo_obj.Tipo or '',
            # Archivos cargados por el sistema (sin usuario) quedan a cargo de la coordinación médica
            UsuarioId=archivo_obj.Usuario_id or getattr(settings, 'USUARIO_COORDINACION_MEDICA_ID', None),
            TipoHallazgo=resultado["tipo"],
            Descripcion=resultado.get("descripcion", ""),
            Modo=resultado.get("modo", ""),
            # Un OK no deja nada pendiente
            Resuelta=resultado["tipo"] == "OK",
        )
    except Exception as exc:
        logger.error("Error guardando revisión IA del archivo %s: %s", archivo_obj.IdArchivo, exc)


def revisar_en_segundo_plano(archivo_ids, consecutivo) -> bool:
    """
    Lanza la revisión IA de los archivos en un hilo aparte, para que la subida responda
    de inmediato. El resultado queda en RevisionIA y el frontend lo consulta después.
    Devuelve True si se lanzó (IA activa y con API key).
    """
    import threading
    from django.db import close_old_connections, transaction

    if not getattr(settings, 'REVISAR_CON_IA', True):
        return False
    if not (getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')):
        return False
    ids = list(archivo_ids)
    if not ids:
        return False

    def _trabajo():
        from gedocumental.models import ArchivoFacturacion
        close_old_connections()
        try:
            datos = obtener_contexto_admision(consecutivo)
            for archivo in ArchivoFacturacion.objects.filter(IdArchivo__in=ids):
                revisar_archivo(archivo, datos)
        except Exception as exc:
            logger.error("Error en revisión IA en segundo plano (admisión %s): %s", consecutivo, exc)
        finally:
            close_old_connections()

    # Se lanza cuando el archivo ya está confirmado en la BD
    transaction.on_commit(lambda: threading.Thread(target=_trabajo, daemon=True).start())
    return True

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
# Lectura del PDF: a la IA solo van MAX_CARACTERES_TEXTO de texto, así que no se leen más páginas
# de las necesarias (los RESULTADOS de SIESA pueden tener muchas páginas con gráficas muy pesadas)
MAX_CARACTERES_TEXTO = 6000
MAX_PAGINAS_LEER = 6
MAX_SEGUNDOS_LECTURA = 45
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
   Tolera tildes, mayúsculas, orden de nombres/apellidos, guiones o comas y un segundo nombre faltante,
   pero NO un paciente distinto NI errores de ortografía en el nombre (ej. PAULA vs PAOLA).
   Si es OTRA PERSONA (nombres o apellidos distintos) → tipo INFORMACION: "Paciente no corresponde:
   documento 'X', admisión 'Y'".
   Si es el mismo paciente pero el nombre difiere solo en algunas letras (error de ortografía, ej. PAULA
   vs PAOLA) → tipo INFORMACION: "Nombre del usuario incorrecto: documento 'X', admisión 'Y'".
   Si el documento NO trae nombre ni identificación del paciente, eso NO es un error por sí solo
   (paciente_coincide = NO_APARECE): sigue con las demás verificaciones.
2. PROCEDIMIENTOS: en órdenes médicas, autorizaciones y resultados, los procedimientos/CUPS del documento deben
   corresponder a los CUPS de la admisión. Compara por código CUPS y, si no hay código, por la descripción.
   - Orden médica que no incluye los procedimientos de la admisión → SOPORTES
   - Autorización que no cubre los procedimientos de la admisión → AUTORIZACION
   - Resultado/informe de un procedimiento distinto al de la admisión → REPORTE
   Indica qué trae el documento y qué esperaba la admisión.
   CONSULTAS: una consulta de primera vez, de control o de seguimiento de la MISMA especialidad coincide
   (ej. "CONTROL MEDICINA FISICA Y REHABILITACION" vs CUPS "consulta de primera vez por especialista en
   medicina física y rehabilitación" → coincide). Solo es error si la especialidad es otra.
   PROCEDIMIENTO REALIZADO vs ORDENADO: compara solo lo que el documento muestra como REALIZADO (el tipo
   de informe o historia clínica, su encabezado, la especialidad que atendió y el estudio informado). Las
   secciones "Plan de tratamiento", "Procedimientos ordenados", "Órdenes", "Se solicita", "Se ordena",
   "Recomendaciones" o "Control con resultados" son ÓRDENES para después: NO se comparan con la admisión.
   Ej.: la historia clínica de una consulta de MEDICINA FÍSICA Y REHABILITACIÓN que ordena resonancia,
   electromiografía y terapias corresponde a un CUPS de "consulta por especialista en medicina física y
   rehabilitación" → coincide.
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
  "estudio_documento": {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>", "especialidad": "<E>"},
  "estudio_admision":  {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>", "especialidad": "<E>"},
  "tipo": "<uno de los tipos de arriba>",
  "descripcion": "<máximo 220 caracteres, concreta, citando el dato del documento y el esperado, incluyendo todos los problemas encontrados; o 'Documento correcto' si es OK>"
}
"estudio_documento" = el estudio o atención REALIZADA que documenta (nunca los procedimientos ordenados para después); "estudio_admision" = el CUPS de la admisión
que más se le parece. Usa SOLO estos valores (en mayúsculas):
  M (modalidad): TAC | RM | RX | ECOGRAFIA | EMG | NEUROCONDUCCION | EMG_NEUROCONDUCCION | EEG | PSG |
                 POTENCIALES_EVOCADOS | CONSULTA | LABORATORIO | PROCEDIMIENTO | OTRO | NO_APLICA
                 (TAC = tomografía computada/computarizada/escanografía; RM = resonancia magnética)
  R (región):    CABEZA (cráneo, cerebro, encéfalo, silla turca, órbitas) | CUELLO | COLUMNA_CERVICAL |
                 COLUMNA_DORSAL | COLUMNA_LUMBAR | COLUMNA_TOTAL | TORAX | ABDOMEN | PELVIS |
                 MIEMBRO_SUPERIOR | MIEMBRO_INFERIOR | CUERPO_ENTERO | OTRA | NO_APLICA
  L (lateralidad): DERECHA | IZQUIERDA | BILATERAL | NO_ESPECIFICA
  C (contraste):   CON | SIN | NO_ESPECIFICA   ("simple" = SIN)
  E (especialidad, solo si M = CONSULTA): nombre corto de la especialidad, ej. MEDICINA_FISICA_REHABILITACION,
                 NEUROLOGIA, NEUROCIRUGIA, PSIQUIATRIA; si no es consulta, NO_APLICA
Si el documento no trae procedimiento (comprobante, factura…), usa NO_APLICA en ambos."""


def _reparar_texto(texto) -> str:
    """
    Repara texto de Zeus guardado con doble codificación (UTF-8 leído como Windows-1252):
    "MEDICINA FÃSICA Y REHABILITACIÃ“N" -> "MEDICINA FÍSICA Y REHABILITACIÓN".
    Pasa en ~13 % de las descripciones de CUPS. Si no se puede reparar, lo deja igual.
    """
    if not texto or not any(c in texto for c in ('Ã', 'Â')):
        return texto or ''
    crudo = bytearray()
    for ch in texto:
        if ord(ch) < 256:
            crudo.append(ord(ch))
        else:
            try:
                crudo.extend(ch.encode('cp1252'))
            except UnicodeEncodeError:
                return texto
    try:
        return crudo.decode('utf-8')
    except UnicodeDecodeError:
        return texto


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
                nombre = _reparar_texto(' '.join((p or '').strip() for p in fila[2:6] if (p or '').strip()))
                datos.update({
                    "paciente": nombre,
                    "identificacion": f"{(fila[0] or '').strip()} {(fila[1] or '').strip()}".strip(),
                    "entidad": _reparar_texto((fila[6] or '').strip()),
                    "contrato": _reparar_texto((fila[7] or '').strip()),
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
                f"{cups} - {_reparar_texto(desc)} (cant. {int(cant) if cant is not None else 1})"
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
        import time
        import pdfplumber
        textos, imagenes = [], []
        caracteres = 0
        inicio = time.monotonic()
        with pdfplumber.open(ruta_absoluta) as pdf:
            for num, pagina in enumerate(pdf.pages, start=1):
                if num > MAX_PAGINAS_LEER or time.monotonic() - inicio > MAX_SEGUNDOS_LECTURA:
                    logger.info("Lectura de %s cortada en la página %s (límite de páginas/tiempo)", ruta_absoluta, num)
                    break
                texto_pagina = (pagina.extract_text() or "").strip()
                tiene_texto = len(texto_pagina) >= MIN_CARACTERES_TEXTO
                if tiene_texto:
                    textos.append(f"[Página {num}]\n{texto_pagina}")
                    caracteres += len(texto_pagina)
                if (not tiene_texto or siempre_imagen) and len(imagenes) < MAX_PAGINAS_IMAGEN:
                    buffer = io.BytesIO()
                    pagina.to_image(resolution=200).original.convert("RGB").save(buffer, format="PNG")
                    imagenes.append(base64.b64encode(buffer.getvalue()).decode())
                if hasattr(pagina, "close"):
                    pagina.close()  # libera la memoria de la página
                # Ya hay todo el texto que se va a enviar (y las imágenes, si se piden)
                if caracteres >= MAX_CARACTERES_TEXTO and (not siempre_imagen or imagenes):
                    break
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
            "En el COMPROBANTE solo se verifican el PACIENTE y la FIRMA. NO compares procedimientos ni CUPS "
            "(procedimientos_coinciden = NO_APLICA) aunque vengan vacíos o sean distintos.\n\n"
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
    if ((resultado.get("procedimientos_coinciden") == "NO" or resultado["tipo"] in ("REPORTE", "SOPORTES"))
            and "procedimientos_coinciden" not in ajustes
            and (_consulta_documentada(texto, datos_admision) or _procedimiento_documentado(texto, datos_admision))):
        notas.append("el documento SÍ documenta lo realizado según el CUPS de la admisión (historia clínica de la "
                     "MISMA especialidad de la consulta, o descripción del MISMO procedimiento aunque se escriba "
                     "distinto); lo que menciona como antecedentes u ordenado para después NO es lo realizado: "
                     "procedimientos_coinciden = SI")
        ajustes["procedimientos_coinciden"] = "SI"
    if (_es_comprobante(tipo_documento) and resultado["tipo"] != "OK"
            and resultado.get("paciente_coincide") != "NO"
            and str(resultado.get("firma_paciente", "")).upper() != "NO"
            and not (firma_detectada is False)):
        # En el comprobante solo importan el paciente y la firma: cualquier otro motivo se descarta
        logger.info("Comprobante con paciente y firma correctos: se descarta '%s'", resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="NO_APLICA",
                         ajuste_comprobante=True)
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

    # RESULTADOS: si el paciente coincide, un hallazgo solo se mantiene si hay una DIFERENCIA CLARA
    # (otro tipo de estudio, otra región/lateralidad/contraste en imágenes, otra especialidad).
    # Falta del código CUPS, redacción distinta o detalle insuficiente NO son hallazgo.
    if ((tipo_documento or '').upper() == 'RESULTADO' and resultado["tipo"] != "OK"
            and resultado.get("paciente_coincide") != "NO"
            and not _diferencia_clara(resultado.get("estudio_documento"), resultado.get("estudio_admision"))):
        logger.info("RESULTADO sin diferencia clara de estudio: se descarta '%s'", resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="SI",
                         ajuste_resultado=True)

    _coherencia_tipo_descripcion(resultado)

    return resultado


def _coherencia_tipo_descripcion(resultado: dict) -> None:
    """
    La IA a veces devuelve un tipo de hallazgo con la descripción "Documento correcto"
    (sobre todo tras la re-consulta). Si la descripción dice que está correcto y no hay
    problema de paciente, procedimiento ni firma, el resultado es OK.
    """
    import unicodedata

    def norm(t):
        return unicodedata.normalize('NFKD', str(t or '')).encode('ascii', 'ignore').decode().lower().strip(' .')

    desc = norm(resultado.get("descripcion"))
    sin_problemas = (
        resultado.get("paciente_coincide") != "NO"
        and resultado.get("procedimientos_coinciden") != "NO"
        and not str(resultado.get("firma_paciente", "")).upper() == "NO"
    )
    if resultado.get("tipo") != "OK" and desc in ("documento correcto", "ok", "correcto", "sin hallazgos") and sin_problemas:
        resultado["tipo"] = "OK"
        resultado["descripcion"] = "Documento correcto"
    elif resultado.get("tipo") == "OK":
        resultado["descripcion"] = "Documento correcto"


# Encabezados de secciones con procedimientos ORDENADOS para después (no realizados)
_INICIO_ORDENES = (
    r'plan de tratamiento', r'procedimientos ordenados', r'ordenes medicas', r'ordenes externas',
    r'solicitud(?:es)? de (?:procedimientos|ordenes|examenes)', r'se solicita[n]?', r'se ordena[n]?',
    r'examenes solicitados', r'paraclinicos solicitados', r'conducta',
)
# Encabezados que cierran esa sección
_FIN_ORDENES = (
    r'impresion (?:clinica|diagnostica)', r'diagnostico(?:s)?(?: principal)?', r'atendido por',
    r'firma', r'\[pagina \d+\]', r'revision por sistemas', r'examen fisico', r'conclusion(?:es)?',
)


def _quitar_secciones_ordenes(texto: str) -> str:
    """
    En RESULTADOS/historias clínicas, las secciones "Plan de tratamiento", "Procedimientos
    ordenados", "Se solicita"... son órdenes para después, no lo realizado. Se reemplazan por
    una nota para que la IA no las compare con los CUPS de la admisión.
    """
    import re
    import unicodedata

    def sin_tildes(t):
        return unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()

    lineas = texto.split('\n')
    salida, omitiendo = [], False
    inicio = re.compile(r'^\s*\W*\s*(?:' + '|'.join(_INICIO_ORDENES) + r')\b', re.I)
    fin = re.compile(r'^\s*\W*\s*(?:' + '|'.join(_FIN_ORDENES) + r')\b', re.I)
    for linea in lineas:
        plano = sin_tildes(linea)
        if not omitiendo and inicio.search(plano):
            omitiendo = True
            salida.append("[Sección de procedimientos ORDENADOS para después omitida por el sistema: "
                          "no es lo realizado y no se compara con la admisión]")
            continue
        if omitiendo and fin.search(plano):
            omitiendo = False
        if not omitiendo:
            salida.append(linea)
    return '\n'.join(salida)


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
    True si el documento es del paciente de la admisión aunque la IA diga que no, por
    diferencias solo de FORMATO: nombres en otro orden, guiones, comas, tildes, mayúsculas
    o un nombre/apellido faltante. Un error de ORTOGRAFÍA (PAULA vs PAOLA) NO se tolera:
    es un hallazgo INFORMACION (nombre del usuario incorrecto), aunque el documento coincida.
    """
    import re

    id_adm = re.sub(r'\D', '', datos_admision.get('identificacion') or '')
    id_doc = re.sub(r'\D', '', str(resultado.get('identificacion_documento') or ''))
    if id_adm and id_doc and len(id_doc) >= 5 and id_doc != id_adm:
        return False  # número distinto: es otra persona aunque el nombre se parezca

    adm = _tokens_nombre(datos_admision.get('paciente'))
    doc = _tokens_nombre(resultado.get('nombre_documento') or resultado.get('paciente_documento'))
    if not doc:
        # Sin nombre legible: solo el número de documento decide
        return bool(id_adm and id_doc == id_adm)
    if len(doc) < 2 or not adm:
        return False
    disponibles = list(adm)
    for palabra in doc:
        if palabra not in disponibles:
            return False  # palabra que no está en el nombre de la admisión (otra persona o mal escrita)
        disponibles.remove(palabra)
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


def _consulta_documentada(texto: str, datos_admision: dict) -> bool:
    """
    True si la admisión tiene una CONSULTA de especialista y el documento es la historia clínica
    de esa misma especialidad (ej. CUPS "consulta de primera vez por especialista en medicina física
    y rehabilitación" y documento "CONTROL MEDICINA FISICA Y REHABILITACION").
    """
    import re
    import unicodedata

    def plano(t):
        t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().upper()
        return ' '.join(re.sub(r'[^A-Z]+', ' ', t).split())

    doc = plano(texto)
    if not doc or not re.search(r'HISTORIA CLINICA|MOTIVO DE CONSULTA|CONSULTA|ENFERMEDAD ACTUAL', doc):
        return False
    for cups in datos_admision.get('cups') or []:
        c = plano(cups)
        if 'CONSULTA' not in c:
            continue
        m = (re.search(r'ESPECIALISTA EN (.+?)(?: CANT.*)?$', c)
             or re.search(r'CONSULTA .*? POR (.+?)(?: CANT.*)?$', c)
             or re.search(r'CONSULTA (.+?)(?: CANT.*)?$', c))
        if not m:
            continue
        especialidad = _norm_especialidad(m.group(1))
        if not especialidad:
            continue
        raices = dict(ESPECIALIDADES).get(especialidad)
        if raices:
            if any(re.search(r, doc) for r in raices):
                return True
        elif especialidad in doc:
            return True
    return False


# Raíces sin tildes: funcionan aunque Zeus entregue "FÃ?SICA" o "NEUROLOGÃ\x8dA".
# El orden importa (neurocirugía y neuropediatría antes que neurología).
ESPECIALIDADES = (
    ('FISIATRIA',        (r'REHABILIT', r'FISIATR')),
    ('NEUROCIRUGIA',     (r'NEUROCIRUG',)),
    ('NEUROPEDIATRIA',   (r'NEUROLOG\w*\W*\w*\W*PEDI', r'NEUROPEDIAT')),
    ('NEUROLOGIA',       (r'NEUROLOG',)),
    ('PSIQUIATRIA',      (r'PSIQUIAT',)),
    ('ANESTESIOLOGIA',   (r'ANESTESIOLOG',)),
    ('ORTOPEDIA',        (r'ORTOPED',)),
    ('MEDICINA_INTERNA', (r'MEDICINA\W+INTERNA',)),
    ('DOLOR',            (r'\bDOLOR\b.*\bPALIATIV', r'CLINICA\W+DEL\W+DOLOR')),
    ('MEDICINA_GENERAL', (r'MEDICINA\W+GENERAL',)),
)


# Procedimientos (no imágenes): palabras equivalentes con que se documentan
_GRUPOS_PROCEDIMIENTO = (
    ('INFILTRAC', 'INYECC', 'INFUSI', 'VISCOSUPLEMENT', 'APLICACION DE', 'BLOQUEO'),
    ('ELECTROMIOGRAF', 'EMG', 'NEUROCONDUC', 'CONDUCCION NERVIOSA'),
    ('ELECTROENCEFALOGRAF', 'EEG', 'VIDEOTELEMETR', 'VIDEO EEG'),
    ('POLISOMNOGRAF', 'ESTUDIO DEL SUENO', 'TITULACION'),
    ('POTENCIALES EVOCADOS', 'POTENCIAL EVOCADO', 'PEATC', 'BERA'),
    ('TERAPIA FISICA', 'FISIOTERAPIA', 'TERAPIA OCUPACIONAL'),
)
_IMAGENES = r'TOMOGRAF|RESONANCIA|RADIOGRAF|ECOGRAF|ULTRASONID|MAMOGRAF|DENSITOMETR|DOPPLER|\bRX\b|\bTAC\b'
_PALABRAS_VACIAS = {'CON', 'SIN', 'DE', 'DEL', 'LA', 'LAS', 'LOS', 'EL', 'EN', 'POR', 'PARA', 'CADA', 'UNO', 'MAS',
                    'O', 'Y', 'A', 'HASTA', 'OTRA', 'OTRO', 'MEDICAMENTO', 'LESIONES', 'SUSTANCIA', 'CANT'}


def _procedimiento_documentado(texto: str, datos_admision: dict) -> bool:
    """
    True si un CUPS de la admisión que NO es imagen ni consulta está documentado en el texto,
    por su palabra principal o un equivalente (ej. CUPS "INFILTRACIÓN INTRALESIONAL" y documento
    "VISCOSUPLEMENTACION (INFILTRACION INTRARTICULAR)"). Las imágenes no se validan así porque
    en ellas importan la región y la lateralidad (eso lo compara _mismo_estudio).
    """
    import re
    import unicodedata

    def plano(t):
        t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().upper()
        return ' '.join(re.sub(r'[^A-Z]+', ' ', t).split())

    doc = plano(texto)
    if not doc:
        return False
    for cups in datos_admision.get('cups') or []:
        c = plano(re.sub(r'^\s*\S+\s*-\s*', '', cups))  # quita el código
        if not c or 'CONSULTA' in c or re.search(_IMAGENES, c):
            continue
        for grupo in _GRUPOS_PROCEDIMIENTO:
            if any(r in c for r in grupo):
                if any(r in doc for r in grupo):
                    return True
                break
        else:
            palabras = [w for w in c.split() if len(w) >= 5 and w not in _PALABRAS_VACIAS]
            if palabras and palabras[0][:7] in doc:
                return True
    return False


def _norm_especialidad(texto: str) -> str:
    """'MEDICINA FÍSICA Y REHABILITACIÓN' / 'FISIATRIA' / 'MEDICINA FÃ?SICA Y REHABILITACIÃ?N' -> 'FISIATRIA'."""
    import re
    import unicodedata
    t = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().upper()
    if not t.strip() or t.strip() in ('NO_APLICA', 'NO APLICA', 'NO_ESPECIFICA', 'NO ESPECIFICA'):
        return ''
    for nombre, raices in ESPECIALIDADES:
        if any(re.search(r, t) for r in raices):
            return nombre
    t = re.sub(r'[^A-Z]+', ' ', t)
    for palabra in ('ESPECIALISTA', 'MEDICINA', 'EN', 'POR', 'DE', 'Y'):
        t = re.sub(rf'\b{palabra}\b', ' ', t)
    return ' '.join(t.split())


def _es_comprobante(tipo_documento) -> bool:
    return (tipo_documento or '').upper() in TIPOS_CON_FIRMA


def _diferencia_clara(doc, adm) -> bool:
    """
    True solo si el estudio del documento es CLARAMENTE otro que el de la admisión:
    otra modalidad (resonancia vs tomografía, EMG vs EEG...), y en imágenes otra región,
    lateralidad opuesta o contraste opuesto; en consultas, otra especialidad.
    Si falta información (NO_APLICA, OTRO, vacío), no hay diferencia clara.
    """
    if not isinstance(doc, dict) or not isinstance(adm, dict):
        return False

    def val(d, k):
        return str(d.get(k) or '').upper().strip()

    # Lateralidad opuesta (derecha vs izquierda) es diferencia clara en cualquier estudio o procedimiento
    if {val(doc, 'lateralidad'), val(adm, 'lateralidad')} == {'DERECHA', 'IZQUIERDA'}:
        return True

    desconocidos = {'', 'OTRO', 'NO_APLICA', 'NO_ESPECIFICA', 'OTRA', 'PROCEDIMIENTO'}
    mod_d, mod_a = val(doc, 'modalidad'), val(adm, 'modalidad')
    if mod_d in desconocidos or mod_a in desconocidos:
        return False
    combinado = {'EMG', 'NEUROCONDUCCION', 'EMG_NEUROCONDUCCION'}
    if mod_d != mod_a and not (mod_d in combinado and mod_a in combinado):
        return True

    if mod_d == 'CONSULTA':
        esp_d, esp_a = _norm_especialidad(val(doc, 'especialidad')), _norm_especialidad(val(adm, 'especialidad'))
        return bool(esp_d and esp_a and esp_d != esp_a)

    if mod_d in ('TAC', 'RM', 'RX', 'ECOGRAFIA'):
        reg_d, reg_a = val(doc, 'region'), val(adm, 'region')
        if reg_d not in desconocidos and reg_a not in desconocidos and reg_d != reg_a:
            return True
        lat = {val(doc, 'lateralidad'), val(adm, 'lateralidad')}
        if lat == {'DERECHA', 'IZQUIERDA'}:
            return True
        if {val(doc, 'contraste'), val(adm, 'contraste')} == {'CON', 'SIN'}:
            return True
    return False


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

    if mod_d == 'CONSULTA':
        # Primera vez / control / seguimiento da igual: cuenta la especialidad
        esp_d, esp_a = _norm_especialidad(val(doc, 'especialidad')), _norm_especialidad(val(adm, 'especialidad'))
        return not esp_d or not esp_a or esp_d == esp_a

    return (compatibles(val(doc, 'region'), val(adm, 'region'), {'', 'NO_APLICA'})
            and compatibles(val(doc, 'lateralidad'), val(adm, 'lateralidad'), {'', 'NO_ESPECIFICA'})
            and compatibles(val(doc, 'contraste'), val(adm, 'contraste'), {'', 'NO_ESPECIFICA'}))


def analizar_documento(ruta_absoluta: str, tipo_documento: str, datos_admision: dict) -> dict | None:
    """
    Lee el PDF o la imagen y lo envía a la IA. No toca la base de datos.
    Devuelve None si no se pudo leer. Puede lanzar excepciones de la API.
    """
    texto, imagenes = _leer_documento(ruta_absoluta, tipo_documento)
    if (tipo_documento or '').upper() == 'RESULTADO' and texto:
        texto = _quitar_secciones_ordenes(texto)
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

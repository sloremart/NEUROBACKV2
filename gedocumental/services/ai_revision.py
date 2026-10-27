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
# Tipos de documento que NO se revisan con IA (Programación de Pagos: facturas de proveedor)
TIPOS_SIN_REVISION_IA = ('FACTURAPROVEEDOR',)
# Soportes que trae el paciente (historias clínicas, adicionales): solo se valida que sean del paciente
TIPOS_SOLO_PACIENTE = ('HCLINICA', 'HCNEURO', 'ADICIONALES')

PROMPT_SISTEMA = """Eres un auditor experto en facturación de servicios de salud en Colombia.
Revisas documentos de gestión documental (factura, comprobante, autorización, orden médica, resultado/informe, etc.)
que se cargan a una ADMISIÓN. Te doy los datos reales de la admisión tomados del sistema; el documento DEBE corresponder a ella.

VERIFICACIONES OBLIGATORIAS (en este orden):
1. PACIENTE: el nombre y/o número de identificación que aparece en el documento debe ser el mismo paciente de la admisión.
   El paciente es quien aparece como "Paciente", "Nombre del paciente" o en los DATOS DEL PACIENTE. NUNCA tomes
   como paciente al médico, especialista, tecnólogo, transcriptor, profesional que firma o usuario que imprime.
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
   - Orden médica que no incluye los procedimientos de la admisión → SOPORTES. Si la orden o la
     autorización trae los procedimientos de la admisión y ADEMÁS otros, es correcta (no es error).
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
  "nombre_documento": "<solo el nombre completo del paciente TRANSCRITO LETRA POR LETRA tal como está escrito en el documento, sin corregir ortografía ni completarlo con el de la admisión, o ''>",
  "identificacion_documento": "<solo el número de documento del paciente (el que va junto a IDENTIFICACIÓN, CC, TI, RC, CE, documento), sin tipo ni separadores, o ''. NUNCA el número de fórmula, folio, factura, autorización, orden o código de barras>",
  "paciente_coincide": "SI | NO | NO_APARECE",
  "procedimientos_documento": "<CUPS/procedimientos que trae el documento, o 'no aplica'>",
  "procedimientos_coinciden": "SI | NO | NO_APLICA",
  "firma_paciente": "SI | NO | NO_APLICA   (solo COMPROBANTE; en otros documentos NO_APLICA)",
  "estudio_documento": {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>", "especialidad": "<E>"},
  "estudio_admision":  {"modalidad": "<M>", "region": "<R>", "lateralidad": "<L>", "contraste": "<C>", "especialidad": "<E>"},
  "recaudo": {"requiere_pago": "SI | NO | NO_INDICA", "tipo_cobro": "CUOTA_MODERADORA | COPAGO | NO_INDICA",
              "valor": "<pesos o ''>", "porcentaje": "<% o ''>", "categoria": "A | B | C | Z | NO_INDICA",
              "tipo_afiliado": "COTIZANTE | BENEFICIARIO | NO_INDICA", "exoneracion": "SI | NO | NO_INDICA"},
  "tipo": "<uno de los tipos de arriba>",
  "descripcion": "<máximo 220 caracteres, concreta, citando el dato del documento y el esperado, incluyendo todos los problemas encontrados; o 'Documento correcto' si es OK>"
}
"estudio_documento" = el estudio o atención REALIZADA que documenta (nunca los procedimientos ordenados para después); "estudio_admision" = el CUPS de la admisión
que más se le parece. Usa SOLO estos valores (en mayúsculas):
  M (modalidad): TAC | RM | RX | ECOGRAFIA | EMG | NEUROCONDUCCION | EMG_NEUROCONDUCCION | EEG | PSG |
                 POTENCIALES_EVOCADOS | CONSULTA | LABORATORIO | PROCEDIMIENTO | OTRO | NO_APLICA
                 (TAC = tomografía computada/computarizada/escanografía y también PET/CT o PET-TC =
                 tomografía por emisión de positrones; RM = resonancia magnética)
  R (región):    CABEZA (cráneo, cerebro, encéfalo, silla turca, órbitas) | CUELLO | COLUMNA_CERVICAL |
                 COLUMNA_DORSAL | COLUMNA_LUMBAR | COLUMNA_TOTAL | TORAX | ABDOMEN | PELVIS |
                 MIEMBRO_SUPERIOR | MIEMBRO_INFERIOR | CUERPO_ENTERO | OTRA | NO_APLICA
  L (lateralidad): DERECHA | IZQUIERDA | BILATERAL | NO_ESPECIFICA
  C (contraste):   CON | SIN | NO_ESPECIFICA   ("simple" = SIN)
  E (especialidad, solo si M = CONSULTA): nombre corto de la especialidad, ej. MEDICINA_FISICA_REHABILITACION,
                 NEUROLOGIA, NEUROCIRUGIA, PSIQUIATRIA; si no es consulta, NO_APLICA
Si el documento no trae procedimiento (comprobante, factura…), usa NO_APLICA en ambos.
"recaudo" = condiciones de pago SOLO si el documento es una AUTORIZACION o el validador de derechos de la EPS
(en cualquier otro documento, todo NO_INDICA y ''). Transcribe lo que dice el documento, sin calcular:
  requiere_pago: NO si dice "no requiere pago", "exento", o el recaudo/valor a pagar es 0 y el porcentaje de
                 copago es 0; SI si indica un valor o porcentaje mayor a 0; si no dice nada, NO_INDICA.
  tipo_cobro: lo que diga "condiciones de pago y recaudo" o similar (cuota moderadora o copago).
  valor: el valor en pesos a recaudar (solo dígitos); porcentaje: el porcentaje de copago (ej. 11.5).
  categoria: categoría o rango salarial (A, B, C) o nivel (Z para subsidiado nivel Z).
  tipo_afiliado: si el usuario es cotizante o beneficiario.
  exoneracion: SI solo si alguna alerta o marca médica dice "EXONERACION PRESTACION"; alertas como NORMAL,
               "usuario con peticiones recurrentes" o "usuario tutela" NO exoneran (NO); sin alertas, NO_INDICA.
No uses el tipo RECAUDO: lo recaudado lo verifica el sistema contra SIESA."""


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
        max_tokens=700,
        temperature=0,
    )

    resultado = json.loads(respuesta.choices[0].message.content)

    # Normalizar el tipo por si la IA devuelve algo inesperado
    tipo = resultado.get("tipo", "").upper().strip()
    if tipo not in TIPOS_OBSERVACION:
        tipo = "INFORMACION"
    if tipo == "RECAUDO":
        # La IA no sabe qué se cobró en SIESA: el recaudo lo decide verificar_recaudo
        tipo = "OK"
        resultado["descripcion"] = "Documento correcto"
    resultado["tipo"] = tipo
    _ajustar_lateralidad_admision(resultado, datos_admision)

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

    # Firma confirmada por el sistema: el hallazgo de "ausencia de firma" se descarta aunque la IA insista
    if (_es_comprobante(tipo_documento) and firma_detectada and resultado["tipo"] != "OK"
            and "firma" in (resultado.get("descripcion") or "").lower()):
        if resultado.get("paciente_coincide") != "NO":
            logger.info("Comprobante con firma detectada por el sistema: se descarta '%s'", resultado.get("descripcion"))
            resultado.update(tipo="OK", descripcion="Documento correcto", firma_paciente="SI", ajuste_firma=True)

    # RESULTADOS: si el paciente coincide, un hallazgo solo se mantiene si hay una DIFERENCIA CLARA
    # (otro tipo de estudio, otra región/lateralidad/contraste en imágenes, otra especialidad).
    # Falta del código CUPS, redacción distinta o detalle insuficiente NO son hallazgo.
    if ((tipo_documento or '').upper() == 'RESULTADO' and resultado["tipo"] != "OK"
            and resultado.get("paciente_coincide") != "NO"
            and (not _diferencia_clara(resultado.get("estudio_documento"), resultado.get("estudio_admision"))
                 # Historia clínica de la MISMA especialidad de la consulta: los estudios que menciona
                 # (PSG, resonancias...) son antecedentes, no lo realizado
                 or _consulta_documentada(texto, datos_admision)
                 or (_imagen_documentada(texto, datos_admision)
                     and not _lateralidad_opuesta(resultado.get("estudio_documento"),
                                                  resultado.get("estudio_admision"))))):
        logger.info("RESULTADO sin diferencia clara de estudio: se descarta '%s'", resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="SI",
                         ajuste_resultado=True)

    # Paciente: el sistema decide entre "otra persona", "misma persona con el nombre mal escrito"
    # y "no se leyó el nombre" (la IA a veces toma "RESULTADO" o "PACIENTE" como si fuera el nombre).
    # Si la IA aceptó el paciente pero el nombre del documento (PDF digital o escaneado) tiene letras
    # distintas a las de la admisión, es "nombre mal escrito".
    if (resultado.get("paciente_coincide") == "SI" and (tipo_documento or '').upper() != 'RESULTADO'
            and (_nombre_en_texto(resultado.get("nombre_documento"), texto)
                 or (imagenes and len([t for t in _tokens_nombre(resultado.get("nombre_documento"))
                                       if t not in _NO_SON_NOMBRE]) >= 2))
            and not _mismo_paciente(resultado, datos_admision)
            and _clasificar_paciente(resultado, texto, datos_admision) == "nombre_mal_escrito"):
        resultado["paciente_coincide"] = "NO"

    if resultado.get("paciente_coincide") == "NO":
        clase = _clasificar_paciente(resultado, texto, datos_admision)
        # En el RESULTADO (lo genera el equipo/software del estudio) y en la historia clínica o los soportes
        # (los escribe otra institución) una letra o un dígito distinto en el nombre o el documento no es
        # error que corrija facturación: solo importa que no sea OTRA persona.
        if (((tipo_documento or '').upper() == 'RESULTADO' or (tipo_documento or '').upper() in TIPOS_SOLO_PACIENTE)
                and clase in ("nombre_mal_escrito", "documento_incorrecto")):
            clase = "misma_persona"
        if clase == "misma_persona":
            resultado["paciente_coincide"] = "SI"
            if resultado["tipo"] == "INFORMACION" and _solo_motivo_paciente(resultado.get("descripcion")):
                resultado.update(tipo="OK", descripcion="Documento correcto")
        elif clase == "otra_persona":
            nombre_doc = resultado.get("nombre_documento") or ""
            if len([t for t in _tokens_nombre(nombre_doc) if t not in _NO_SON_NOMBRE]) >= 2:
                resultado.update(
                    tipo="INFORMACION",
                    descripcion=(f"Paciente no corresponde: documento '{nombre_doc}', "
                                 f"admisión '{datos_admision.get('paciente')}'."),
                )
        elif clase == "sin_datos_paciente":
            resultado.update(
                tipo="INFORMACION",
                descripcion=("Paciente no corresponde: el documento no contiene el nombre ni el documento de "
                             f"identidad del paciente de la admisión ({datos_admision.get('paciente')}, "
                             f"{datos_admision.get('identificacion')})."),
            )
        elif clase == "documento_incorrecto":
            resultado.update(
                tipo="INFORMACION",
                descripcion=(f"Número de documento incorrecto: documento '{resultado.get('identificacion_documento')}', "
                             f"admisión '{datos_admision.get('identificacion')}' "
                             f"(paciente {datos_admision.get('paciente')})."),
                paciente_coincide="DOCUMENTO_INCORRECTO",
            )
        elif clase == "nombre_mal_escrito":
            resultado.update(
                tipo="INFORMACION",
                descripcion=(f"Nombre del usuario incorrecto: documento '{resultado.get('nombre_documento')}', "
                             f"admisión '{datos_admision.get('paciente')}'."),
                paciente_coincide="NOMBRE_MAL_ESCRITO",
            )

    # Mismo código CUPS en el documento y en la admisión: el procedimiento coincide, aunque la
    # descripción esté escrita distinto ("...TITULACION DE DISPOSITIVO MEDICO" vs "...DE CPAP NASAL").
    if (resultado["tipo"] != "OK" and resultado.get("paciente_coincide") != "NO"
            and _solo_motivo_procedimiento(resultado.get("descripcion"))
            and _mismo_codigo_cups(texto, resultado.get("procedimientos_documento"), datos_admision)
            and not _lateralidad_opuesta(resultado.get("estudio_documento"), resultado.get("estudio_admision"))):
        logger.info("Mismo código CUPS en documento y admisión: se descarta '%s'", resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="SI",
                         ajuste_codigo_cups=True)

    # ÓRDENES y AUTORIZACIONES: un hallazgo cuyo ÚNICO motivo es el procedimiento ("no se especifican
    # procedimientos", "no aparece el CUPS"...) se descarta si no hay diferencia clara de estudio.
    # Otros motivos (vencida, sin número, ilegible, cantidades, firma) se mantienen.
    if (not _es_comprobante(tipo_documento) and resultado["tipo"] != "OK"
            and resultado.get("paciente_coincide") != "NO"
            and _solo_motivo_procedimiento(resultado.get("descripcion"))
            and not _diferencia_clara(resultado.get("estudio_documento"), resultado.get("estudio_admision"))):
        logger.info("%s: motivo solo de procedimiento sin diferencia clara, se descarta '%s'",
                    tipo_documento, resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="SI",
                         ajuste_procedimiento_orden=True)

    # Orden/autorización con un procedimiento de más (además de los de la admisión): no es error
    if (not _es_comprobante(tipo_documento) and (tipo_documento or '').upper() != 'RESULTADO'
            and resultado["tipo"] != "OK" and resultado.get("paciente_coincide") != "NO"
            and _solo_procedimiento_adicional(resultado.get("descripcion"))):
        logger.info("%s: solo trae un procedimiento adicional, se descarta '%s'",
                    tipo_documento, resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", procedimientos_coinciden="SI",
                         ajuste_procedimiento_adicional=True)

    # Soportes del paciente (HC, adicionales): si es del paciente correcto, cualquier otro motivo se descarta
    if ((tipo_documento or '').upper() in TIPOS_SOLO_PACIENTE and resultado["tipo"] != "OK"
            and resultado.get("paciente_coincide") not in ("NO", "NOMBRE_MAL_ESCRITO", "DOCUMENTO_INCORRECTO")
            and _solo_motivo_paciente(resultado.get("descripcion")) is False):
        logger.info("%s: soporte del paciente, se descarta '%s'", tipo_documento, resultado.get("descripcion"))
        resultado.update(tipo="OK", descripcion="Documento correcto", ajuste_soporte=True)

    _coherencia_tipo_descripcion(resultado)
    return resultado


def _lateralidad_cups(datos_admision: dict) -> str:
    """DERECHA / IZQUIERDA solo si el texto de los CUPS lo dice; 'AP. LATERAL' es proyección, no lado."""
    import re
    import unicodedata
    t = ' '.join(str(c) for c in (datos_admision.get('cups') or []))
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().upper()
    der, izq = bool(re.search(r'\bDERECH', t)), bool(re.search(r'\bIZQUIERD', t))
    if der and not izq:
        return 'DERECHA'
    if izq and not der:
        return 'IZQUIERDA'
    return 'NO_ESPECIFICA'


def _ajustar_lateralidad_admision(resultado: dict, datos_admision: dict) -> None:
    adm = resultado.get("estudio_admision")
    if isinstance(adm, dict):
        adm["lateralidad"] = _lateralidad_cups(datos_admision)


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
    t = _reparar_texto(str(texto or ''))
    # Vocal tildada dañada por la codificación de Zeus ("HERNÃ?NDEZ" = HERNÁNDEZ): una sola letra ilegible
    t = re.sub(r'Ã[\?�\u0081\u008d\u008f\u0090\u009d]', '#', t)
    # Letras ilegibles por la codificación ("CASTA?EDA", "CASTA\ufffdEDA") quedan como comodín '#'
    t = re.sub(r'(?<=[A-Za-z\u00C0-\u017F])[\?\ufffd](?=[A-Za-z\u00C0-\u017F])', '#', t)
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'[^a-z#\s]', ' ', t.replace('_', ' '))
    return [p for p in t.split() if len(p) >= 3 and p not in ('del', 'los', 'las', 'cc', 'ti', 'rc')]


def _misma_palabra(a: str, b: str) -> bool:
    """Igualdad de palabras de un nombre; '#' (letra ilegible) vale por cualquier letra."""
    import re
    if a == b:
        return True
    if '#' not in a and '#' not in b:
        return False
    patron, otra = (a, b) if '#' in a else (b, a)
    return bool(re.fullmatch('.'.join(re.escape(parte) for parte in patron.split('#')), otra))


def _buscar_palabra(palabra: str, lista: list) -> str | None:
    return next((x for x in lista if _misma_palabra(palabra, x)), None)


# Palabras que la IA a veces devuelve como "nombre" y no lo son
_NO_SON_NOMBRE = {'resultado', 'documento', 'informe', 'reporte', 'paciente', 'nombre', 'aparece', 'examen',
                  'estudio', 'historia', 'clinica', 'usuario', 'aplica', 'identificacion', 'datos',
                  # Rótulos de columnas que se pegan al nombre en autorizaciones ("MARTI-TR EDAD")
                  'edad', 'sexo', 'genero', 'fecha', 'nacimiento', 'tipo', 'telefono', 'direccion',
                  'regimen', 'afiliado', 'afiliacion', 'anos', 'meses', 'ciudad', 'municipio'}


def _clasificar_paciente(resultado: dict, texto: str, datos_admision: dict) -> str:
    """
    Cuando la IA dice que el paciente NO coincide, decide con reglas fijas:
      "misma_persona"      -> mismo nombre en otro formato, o el nombre no se leyó pero el número de
                              documento / el nombre de la admisión aparecen en el texto
      "documento_incorrecto" -> mismo nombre pero otro número de documento
      "nombre_mal_escrito" -> misma persona (mismo número de documento o nombre casi igual) pero con
                              letras distintas en el nombre (PAULA vs PAOLA, RICON vs RINCON)
      "otra_persona"       -> no hay evidencia de que sea el mismo paciente
    """
    import re
    from difflib import SequenceMatcher

    if _mismo_paciente(resultado, datos_admision):
        return "misma_persona"

    # El nombre COMPLETO del paciente de la admisión está escrito en el documento: es su documento,
    # aunque la IA haya tomado otro nombre (el médico que firma, el tecnólogo, quien imprime...).
    adm_completo = _tokens_nombre(datos_admision.get('paciente'))
    if len(adm_completo) >= 2:
        del_texto = _tokens_nombre(texto)
        if all(_buscar_palabra(t, del_texto) for t in adm_completo):
            return "misma_persona"

    adm = _tokens_nombre(datos_admision.get('paciente'))
    doc = [t for t in _tokens_nombre(resultado.get('nombre_documento') or resultado.get('paciente_documento'))
           if t not in _NO_SON_NOMBRE]
    id_adm = re.sub(r'\D', '', datos_admision.get('identificacion') or '')
    id_doc = _id_documento(resultado, id_adm)
    numeros_texto = set(re.findall(r'\d{5,}', re.sub(r'[.,](?=\d{3})', '', texto or '')))
    mismo_id = bool(id_adm) and (id_doc == id_adm or id_adm in numeros_texto)
    if id_adm and id_doc and len(id_doc) >= 5 and id_doc != id_adm and id_adm not in numeros_texto:
        # Mismo nombre (o casi: una letra distinta) con otro número: es el paciente, pero con el
        # número de documento errado
        if len(doc) >= 2 and (_mismo_paciente(dict(resultado, identificacion_documento=''), datos_admision)
                              or _nombre_casi_igual(doc, adm)):
            return "documento_incorrecto"
        return "otra_persona"

    if len(doc) < 2:
        # El nombre no se leyó (ej. la IA devolvió "RESULTADO").
        # - Escaneado/imagen (sin texto): no hay forma de verificar → no es hallazgo.
        # - PDF digital: el nombre o el documento del paciente deben estar en el texto; si no están,
        #   el documento es de otra persona (ej. PDF de enfermería generado con el paciente equivocado).
        if len((texto or '').strip()) < 50 or mismo_id:
            return "misma_persona"
        return "sin_datos_paciente"

    if _nombre_casi_igual(doc, adm) or mismo_id:
        return "nombre_mal_escrito"
    return "otra_persona"


def _separar_pegadas(doc: list, adm: list) -> list:
    """
    Apellidos o nombres escritos pegados en el documento ('alvarezrodriguez') se separan en las dos palabras
    de la admisión ('alvarez', 'rodriguez'): es una diferencia de formato, no de ortografía.
    """
    salida = []
    for palabra in doc:
        partes = None
        if _buscar_palabra(palabra, adm) is None:
            partes = next(((adm[i], adm[j]) for i in range(len(adm)) for j in range(len(adm))
                           if i != j and adm[i] + adm[j] == palabra), None)
        salida.extend(partes or [palabra])
    return salida


def _nombre_casi_igual(doc: list, adm: list) -> bool:
    """Cada palabra del documento se parece (≥ 0.8) a una distinta de la admisión (URAN / HURAN)."""
    from difflib import SequenceMatcher
    doc = _separar_pegadas(doc, adm)
    disponibles = list(adm)
    if len(doc) < 2:
        return False
    for palabra in doc:
        mejor = max(disponibles, key=lambda a: SequenceMatcher(None, palabra, a).ratio(), default=None)
        if mejor is None or SequenceMatcher(None, palabra, mejor).ratio() < 0.8:
            return False
        disponibles.remove(mejor)
    return True


def _nombre_en_texto(nombre, texto) -> bool:
    """True si todas las palabras del nombre (≥ 2) aparecen tal cual en el texto del PDF."""
    palabras = [t for t in _tokens_nombre(nombre) if t not in _NO_SON_NOMBRE]
    del_texto = _tokens_nombre(texto)
    return len(palabras) >= 2 and all(_buscar_palabra(p, del_texto) for p in palabras)


def _solo_motivo_paciente(descripcion) -> bool:
    """True si el hallazgo solo habla del paciente (para poder anularlo cuando el sistema confirma que es el mismo)."""
    import re
    import unicodedata
    d = unicodedata.normalize('NFKD', str(descripcion or '')).encode('ascii', 'ignore').decode().lower()
    sobre_paciente = re.search(r'paciente|nombre|usuario|identificacion|documento de identidad', d)
    otros = re.search(r'procedimiento|cups|estudio|firma|vencid|autorizacion|cantidad|legib|lateralidad', d)
    return bool(sobre_paciente and not otros)


def _id_documento(resultado: dict, id_adm: str) -> str:
    """
    Número de documento que leyó la IA, solo dígitos. Más de 12 dígitos no es una cédula, TI ni registro
    civil (máximo 11): es un número de fórmula, orden o código de barras tomado por error → se ignora
    (salvo que la admisión también tenga un número así de largo, ej. PEP).
    """
    import re
    id_doc = re.sub(r'\D', '', str(resultado.get('identificacion_documento') or ''))
    if len(id_doc) > 12 and len(id_adm) <= 12:
        return ''
    return id_doc


def _mismo_paciente(resultado: dict, datos_admision: dict) -> bool:
    """
    True si el documento es del paciente de la admisión aunque la IA diga que no, por
    diferencias solo de FORMATO: nombres en otro orden, guiones, comas, tildes, mayúsculas
    o un nombre/apellido faltante. Un error de ORTOGRAFÍA (PAULA vs PAOLA) NO se tolera:
    es un hallazgo INFORMACION (nombre del usuario incorrecto), aunque el documento coincida.
    """
    import re

    id_adm = re.sub(r'\D', '', datos_admision.get('identificacion') or '')
    id_doc = _id_documento(resultado, id_adm)
    if id_adm and id_doc and len(id_doc) >= 5 and id_doc != id_adm:
        return False  # número distinto: es otra persona aunque el nombre se parezca

    adm = _tokens_nombre(datos_admision.get('paciente'))
    doc = [t for t in _tokens_nombre(resultado.get('nombre_documento') or resultado.get('paciente_documento'))
           if t not in _NO_SON_NOMBRE]
    if not doc:
        # Sin nombre legible: solo el número de documento decide
        return bool(id_adm and id_doc == id_adm)
    doc = _separar_pegadas(doc, adm)
    if len(doc) < 2 or not adm:
        return False
    disponibles = list(adm)
    for i, palabra in enumerate(doc):
        igual = _buscar_palabra(palabra, disponibles)
        if igual is None and i == len(doc) - 1 and len(palabra) >= 3:
            # Última palabra cortada por el ancho del campo del formulario ("MARTI" de MARTINEZ)
            igual = next((a for a in disponibles if a.startswith(palabra)), None)
        if igual is None:
            return False  # palabra que no está en el nombre de la admisión (otra persona o mal escrita)
        disponibles.remove(igual)
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
            for pagina in pdf.pages[:3]:
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
                # Firma dibujada como trazos (vectores) cerca del rótulo "Firma del paciente"
                rotulos = [w for w in pagina.extract_words() if w['text'].lower().startswith('firma')]
                for w in rotulos:
                    trazos = [c for c in (pagina.curves or [])
                              if w['top'] - 120 <= c['top'] <= w['bottom'] + 120
                              and (c['x1'] - c['x0']) < pagina.width * 0.6
                              and len(c.get('pts') or []) >= 4]
                    if trazos:
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


def _codigos_cups(texto) -> set:
    """Códigos CUPS (6 caracteres que empiezan por dígito, ej. 891703, 89070A) presentes en un texto."""
    import re
    return set(re.findall(r'(?<![0-9A-Z])(\d{5}[0-9A-Z])(?![0-9A-Z])', str(texto or '').upper()))


def _mismo_codigo_cups(texto, procedimientos_documento, datos_admision) -> bool:
    """True si el documento trae al menos un código CUPS que también está en la admisión."""
    admision = set()
    for cups in datos_admision.get('cups') or []:
        admision |= _codigos_cups(str(cups).split(' - ')[0])
    return bool(admision & (_codigos_cups(texto) | _codigos_cups(procedimientos_documento)))


def _lateralidad_opuesta(doc, adm) -> bool:
    if not isinstance(doc, dict) or not isinstance(adm, dict):
        return False
    lados = {str(doc.get('lateralidad') or '').upper(), str(adm.get('lateralidad') or '').upper()}
    return lados == {'DERECHA', 'IZQUIERDA'}


def _solo_motivo_procedimiento(descripcion) -> bool:
    """True si la descripción del hallazgo solo habla del procedimiento/CUPS y de ningún otro motivo."""
    import re
    import unicodedata
    d = unicodedata.normalize('NFKD', str(descripcion or '')).encode('ascii', 'ignore').decode().lower()
    sobre_procedimiento = re.search(r'procedimiento|cups|estudio|servicio|no se especifica|no incluye|no aparece', d)
    return bool(sobre_procedimiento and not re.search(_OTROS_MOTIVOS, d))


_OTROS_MOTIVOS = (r'vencid|vigencia|caducad|numero de autorizacion|sin numero|no cuenta con numero|'
                  r'legib|cantidad|firma|paciente no|nombre|documento de identidad|identificacion|'
                  r'fecha|tachon|enmendad')


def _solo_procedimiento_adicional(descripcion) -> bool:
    """
    True si el único motivo es que el documento trae un procedimiento DE MÁS ("incluye ECOCARDIOGRAMA, no
    esperado en admisión que solo tiene TOMOGRAFÍA"). Una orden o autorización puede traer varios
    procedimientos: lo que importa es que traiga los de la admisión, y el hallazgo no dice que falten.
    """
    import re
    import unicodedata
    d = unicodedata.normalize('NFKD', str(descripcion or '')).encode('ascii', 'ignore').decode().lower()
    de_mas = re.search(r'(?<!no )\bincluye\b.*\b(no esperad|no solicitad|no ordenad|adicional)'
                       r'|\badicional(es)?\b|\bademas de\b', d)
    falta = re.search(r'no incluye|no aparece|no se especifica|no cubre|no corresponde|no coincide|en lugar de|'
                      r'en vez de|distint|diferente|falta|lateralidad|derech|izquierd', d)
    return bool(de_mas and not falta and not re.search(_OTROS_MOTIVOS, d))


def _modalidad_base(m: str) -> str:
    """Agrupa modalidades equivalentes: PET/PET-CT con TAC; EMG con neuroconducción."""
    m = (m or '').upper().replace('-', '_').replace(' ', '_')
    if m in ('PET', 'PET_CT', 'PET_TC', 'PETCT', 'MEDICINA_NUCLEAR', 'TOMOGRAFIA', 'TC'):
        return 'TAC'
    if m in ('EMG', 'NEUROCONDUCCION', 'EMG_NEUROCONDUCCION'):
        return 'EMG'
    if m in ('RMN', 'RESONANCIA'):
        return 'RM'
    if m in ('RADIOGRAFIA', 'RAYOS_X'):
        return 'RX'
    return m


def _region_base(r: str) -> str:
    """Regiones vecinas que la IA etiqueta indistintamente (cadera = pelvis o miembro inferior)."""
    r = (r or '').upper()
    if r in ('PELVIS', 'MIEMBRO_INFERIOR', 'CADERA'):
        return 'PELVIS_MIEMBRO_INFERIOR'
    if r.startswith('COLUMNA'):
        return 'COLUMNA'
    if r in ('TORAX', 'ABDOMEN', 'CUERPO_ENTERO'):
        return r if r != 'CUERPO_ENTERO' else ''  # cuerpo entero (PET) no se compara por región
    return r


# Imágenes: palabras con que se nombra cada modalidad y zonas anatómicas (sin tildes)
_MODALIDADES_IMAGEN = {
    'RX':  ('RADIOGRAF', 'RAYOS X', ' RX '),
    'TAC': ('TOMOGRAF', 'ESCANOGRAF', ' TAC ', ' TC ', 'PET', 'POSITRONES'),
    'RM':  ('RESONANCIA', ' RM ', ' RMN '),
    'ECO': ('ECOGRAF', 'ULTRASONID', 'DOPPLER', 'ECOCARDIOGRAF'),
}
_ZONAS = ('CRANEO', 'CEREBRO', 'ENCEFALO', 'CARA', 'SENOS PARANASALES', 'ORBITA', 'SILLA TURCA', 'CUELLO',
          'TORAX', 'ABDOMEN', 'PELVIS', 'COLUMNA', 'CERVICAL', 'DORSAL', 'LUMBAR', 'LUMBOSACRA', 'SACRO',
          'CADERA', 'COXO', 'RODILLA', 'TOBILLO', 'PIE', 'HOMBRO', 'CODO', 'MUNECA', 'MANO', 'FEMUR', 'TIBIA',
          'HUMERO', 'ANTEBRAZO', 'PIERNA', 'MAMA', 'TIROIDES', 'RENAL', 'RINON', 'HIGADO', 'PROSTATA',
          'OBSTETRIC', 'ARTICULACION', 'VESICULA', 'TESTICUL', 'CAROTID')


def _imagen_documentada(texto: str, datos_admision: dict) -> bool:
    """
    True si un CUPS de imagen de la admisión está documentado: el texto menciona su tipo de estudio
    (radiografía / tomografía o PET / resonancia / ecografía) y, si el CUPS nombra una zona, al
    menos una de esas zonas. Ej. "PET/CT" para "tomografía por emisión de positrones", o
    "RX de cadera y rodillas" para CUPS de radiografía de cadera y de rodilla.
    """
    import re
    import unicodedata

    def plano(t):
        t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode().upper()
        return ' ' + ' '.join(re.sub(r'[^A-Z]+', ' ', t).split()) + ' '

    doc = plano(texto)
    if len(doc) < 10:
        return False
    for cups in datos_admision.get('cups') or []:
        c = plano(re.sub(r'^\s*\S+\s*-\s*', '', str(cups)))
        for raices in _MODALIDADES_IMAGEN.values():
            if any(r in c for r in raices):
                if not any(r in doc for r in raices):
                    break
                zonas = [z for z in _ZONAS if z in c]
                if not zonas or any(z in doc for z in zonas):
                    return True
                break
    return False


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
    mod_d, mod_a = _modalidad_base(val(doc, 'modalidad')), _modalidad_base(val(adm, 'modalidad'))
    if mod_d in desconocidos or mod_a in desconocidos:
        return False
    if mod_d != mod_a:
        return True

    if mod_d == 'CONSULTA':
        esp_d, esp_a = _norm_especialidad(val(doc, 'especialidad')), _norm_especialidad(val(adm, 'especialidad'))
        return bool(esp_d and esp_a and esp_d != esp_a)

    if mod_d in ('TAC', 'RM', 'RX', 'ECOGRAFIA'):
        reg_d, reg_a = _region_base(val(doc, 'region')), _region_base(val(adm, 'region'))
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
    if (tipo_documento or '').upper() == 'AUTORIZACION':
        _agregar_hallazgo_recaudo(resultado, datos_admision)
    return resultado


def _agregar_hallazgo_recaudo(resultado: dict, datos_admision: dict) -> None:
    """Compara las condiciones de pago de la autorización con lo recaudado en SIESA (sin otra llamada a la IA)."""
    from gedocumental.services.recaudo import verificar_recaudo
    try:
        hallazgo = verificar_recaudo(resultado.get("recaudo"), datos_admision.get("consecutivo"))
    except Exception as exc:
        logger.warning("No se pudo verificar el recaudo de %s: %s", datos_admision.get("consecutivo"), exc)
        return
    if not hallazgo:
        return
    tipo, descripcion = hallazgo
    resultado["hallazgo_recaudo"] = descripcion
    if resultado["tipo"] == "OK":
        resultado.update(tipo=tipo, descripcion=descripcion)
    else:
        resultado["descripcion"] = f"{resultado.get('descripcion', '')} | {descripcion}"


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

    # Documentos de otros módulos (facturas de proveedor) no se revisan
    if (archivo_obj.Tipo or '').upper() in TIPOS_SIN_REVISION_IA:
        return None

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
            DatosRecaudo=(json.dumps(resultado.get("recaudo"), ensure_ascii=False)
                          if resultado.get("hallazgo_recaudo") else ''),
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


def reverificar_recaudo_con_zeus() -> int:
    """
    Hallazgos de recaudo pendientes: vuelve a comparar con SIESA lo que la IA leyó de la autorización
    (sin llamar a la IA). Si ya cuadra (se corrigió el cobro o el tipo de afiliado en Zeus):
      - hallazgo solo de recaudo → se cierra como CORREGIDO;
      - hallazgo con otros motivos → se quita la parte del recaudo de la descripción.
    """
    from django.utils import timezone
    from gedocumental.models import RevisionIA
    from gedocumental.services.recaudo import verificar_recaudo

    cerrados = 0
    for h in RevisionIA.objects.filter(Resuelta=False).exclude(DatosRecaudo=''):
        try:
            nuevo = verificar_recaudo(json.loads(h.DatosRecaudo), h.AdmisionId)
        except Exception as exc:
            logger.warning("No se pudo reverificar el recaudo del hallazgo %s: %s", h.IdRevision, exc)
            continue
        if nuevo:
            continue
        partes = (h.Descripcion or '').split(' | ')
        if h.TipoHallazgo in ('RECAUDO', 'INFORMACION') and len(partes) == 1:
            h.Resuelta = True
            h.Resolucion = 'CORREGIDO'
            h.FechaResolucion = timezone.now()
            h.save(update_fields=['Resuelta', 'Resolucion', 'FechaResolucion'])
        else:
            h.Descripcion = partes[0]
            h.DatosRecaudo = ''
            h.save(update_fields=['Descripcion', 'DatosRecaudo'])
        cerrados += 1
        logger.info("Hallazgo %s: el recaudo ya cuadra con SIESA", h.IdRevision)
    return cerrados


"""
Revisión automática de documentos de facturación con IA.

Extrae el texto del PDF subido y lo envía a GPT-4o-mini para detectar
inconsistencias según los tipos de observación SIESA:
  OK | AUTORIZACION | SOPORTES | REPORTE | FARMACIA | TARIFA |
  CONTRATO | INFORMACION | RECAUDO

Si se detecta un problema el archivo queda en estado RECHAZADA y se crea
un registro en ObservacionesArchivos para que regrese al usuario que lo subió.
Si la API key no está configurada la función devuelve None sin lanzar error.
"""

import json
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Tipos de observación válidos — deben coincidir exactamente con los que
# usa SIESA para que la descripción sea interpretable por Cuentas Médicas.
TIPOS_OBSERVACION = [
    'OK', 'AUTORIZACION', 'SOPORTES', 'REPORTE',
    'FARMACIA', 'TARIFA', 'CONTRATO', 'INFORMACION', 'RECAUDO',
]

PROMPT_SISTEMA = """Eres un auditor experto en facturación de servicios de salud en Colombia.
Revisas documentos de gestión documental (Factura, Comprobante de pago, Autorización del seguro y Resultado/informe médico).

Tu tarea es detectar problemas que generarían una devolución por parte de Cuentas Médicas.
Los tipos de observación posibles son exactamente estos (en mayúsculas sin tildes):

- OK: el documento está completo y no tiene errores
- AUTORIZACION: la autorización del seguro falta, está vencida o no corresponde al procedimiento
- SOPORTES: faltan soportes obligatorios (historia clínica, orden médica, fórmula, etc.)
- REPORTE: el informe o resultado médico tiene inconsistencias con el procedimiento facturado
- FARMACIA: hay errores en medicamentos (nombre, concentración, cantidad, lote)
- TARIFA: el valor cobrado no corresponde al contrato o a la tarifa acordada
- CONTRATO: el servicio prestado no está cubierto por el contrato de la EPS
- INFORMACION: datos del paciente, EPS, fecha o procedimiento están incorrectos o incompletos
- RECAUDO: hay inconsistencias en valores pagados, copagos o cuotas moderadoras

Responde ÚNICAMENTE con un JSON válido sin texto adicional:
{
  "tipo": "<uno de los tipos de arriba en mayúsculas>",
  "descripcion": "<máximo 200 caracteres explicando el problema concreto, o 'Documento correcto' si tipo es OK>"
}"""


def _extraer_texto_pdf(ruta_absoluta: str) -> str:
    """Extrae texto de un PDF digital. Devuelve cadena vacía si falla o es escaneado."""
    try:
        import pdfplumber
        texto = ""
        with pdfplumber.open(ruta_absoluta) as pdf:
            for pagina in pdf.pages:
                texto += pagina.extract_text() or ""
        return texto.strip()
    except Exception as exc:
        logger.warning("pdfplumber no pudo leer %s: %s", ruta_absoluta, exc)
        return ""


def _llamar_openai(texto: str, tipo_documento: str, datos_admision: dict) -> dict:
    """
    Llama a GPT-4o-mini y devuelve {"tipo": ..., "descripcion": ...}.
    Puede lanzar excepciones si la API falla.
    """
    from openai import OpenAI

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
    if not api_key:
        raise ValueError("OPENAI_API_KEY no configurada en settings")

    client = OpenAI(api_key=api_key)

    contexto_admision = (
        f"Admisión: {datos_admision.get('consecutivo', 'N/A')} | "
        f"Paciente: {datos_admision.get('paciente', 'N/A')} | "
        f"EPS/Entidad: {datos_admision.get('entidad', 'N/A')} | "
        f"Procedimiento: {datos_admision.get('procedimiento', 'N/A')} | "
        f"Fecha: {datos_admision.get('fecha', 'N/A')}"
    )

    mensaje_usuario = (
        f"TIPO DE DOCUMENTO: {tipo_documento}\n"
        f"DATOS DE LA ADMISIÓN:\n{contexto_admision}\n\n"
        f"CONTENIDO DEL DOCUMENTO:\n{texto[:5000]}"
    )

    respuesta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user",   "content": mensaje_usuario},
        ],
        response_format={"type": "json_object"},
        max_tokens=200,
        temperature=0,
    )

    resultado = json.loads(respuesta.choices[0].message.content)

    # Normalizar el tipo por si la IA devuelve algo inesperado
    tipo = resultado.get("tipo", "").upper().strip()
    if tipo not in TIPOS_OBSERVACION:
        tipo = "INFORMACION"
    resultado["tipo"] = tipo

    return resultado


def revisar_archivo(archivo_obj, datos_admision: dict) -> dict | None:
    """
    Punto de entrada principal.

    Parámetros:
      archivo_obj   : instancia de ArchivoFacturacion ya guardada en DB
      datos_admision: dict con claves consecutivo, paciente, entidad, procedimiento, fecha

    Retorna:
      {"tipo": "AUTORIZACION", "descripcion": "..."} si hay observación
      {"tipo": "OK", "descripcion": "Documento correcto"}       si está bien
      None si la revisión no pudo ejecutarse (sin API key, PDF escaneado, error de red)

    Efectos secundarios al detectar un problema:
      - Crea ObservacionesArchivos con el texto generado por la IA
      - Actualiza EstadoRevision a 'RECHAZADA' en archivo_obj
    """
    from gedocumental.models import ObservacionesArchivos

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

    # Solo revisamos PDFs
    if not ruta_absoluta.lower().endswith('.pdf'):
        return None

    # Extraer texto
    texto = _extraer_texto_pdf(ruta_absoluta)
    if not texto:
        # PDF escaneado o vacío — no podemos revisarlo por texto
        logger.info("PDF sin texto extraíble (posiblemente escaneado): %s", ruta_absoluta)
        return None

    # Llamar a la IA
    try:
        resultado = _llamar_openai(texto, archivo_obj.Tipo, datos_admision)
    except Exception as exc:
        logger.error("Error al llamar a OpenAI para archivo %s: %s", archivo_obj.IdArchivo, exc)
        return None

    # Si hay problema → crear observación y marcar RECHAZADA
    if resultado["tipo"] != "OK":
        try:
            descripcion_completa = f"[IA] {resultado['tipo']}: {resultado['descripcion']}"
            ObservacionesArchivos.objects.create(
                IdArchivo=archivo_obj,
                Descripcion=descripcion_completa[:255],
            )
            archivo_obj.EstadoRevision = 'RECHAZADA'
            archivo_obj.TipoHallazgo = resultado["tipo"]
            archivo_obj.save(update_fields=['EstadoRevision', 'TipoHallazgo'])
        except Exception as exc:
            logger.error("Error guardando observación IA: %s", exc)

    return resultado

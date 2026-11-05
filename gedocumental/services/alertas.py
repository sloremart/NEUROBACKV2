"""
Alertas por Telegram cuando la revisión IA deja de funcionar (apagada, sin clave, clave inválida o sin
saldo en OpenAI).

Configuración en el .env del servidor:
  TELEGRAM_BOT_TOKEN=123456:ABC...   (token que da @BotFather al crear el bot)
  TELEGRAM_CHAT_ID=-1001234567890    (chat o grupo que recibe las alertas; varios separados por coma)
Sin esas variables no se envía nada (solo queda en el log).

Cada tipo de alerta se envía como máximo una vez cada HORAS_ENTRE_ALERTAS, aunque falle cada archivo: el
registro del último envío se guarda en un archivo compartido por el servidor web y el cron.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

HORAS_ENTRE_ALERTAS = 6
REGISTRO_ENVIOS = os.environ.get('ALERTAS_REGISTRO', '/tmp/gedocumental_alertas.json')

IA_APAGADA = 'ia_apagada'
IA_SIN_CLAVE = 'ia_sin_clave'
IA_CLAVE_INVALIDA = 'ia_clave_invalida'
IA_SIN_SALDO = 'ia_sin_saldo'
IA_ERROR = 'ia_error'


def _ultimos_envios() -> dict:
    try:
        with open(REGISTRO_ENVIOS, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def enviar_telegram(mensaje: str) -> bool:
    """Envía el mensaje a cada TELEGRAM_CHAT_ID. Nunca lanza excepción."""
    import requests
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chats = [c.strip() for c in os.environ.get('TELEGRAM_CHAT_ID', '').split(',') if c.strip()]
    if not token or not chats:
        logger.warning("Alerta sin Telegram configurado: %s", mensaje)
        return False
    enviado = False
    for chat in chats:
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={'chat_id': chat, 'text': mensaje}, timeout=10)
            enviado = enviado or r.ok
            if not r.ok:
                logger.warning("Telegram respondió %s: %s", r.status_code, r.text[:200])
        except Exception as exc:
            logger.warning("No se pudo enviar la alerta a Telegram: %s", exc)
    return enviado


def alertar(clave: str, mensaje: str) -> bool:
    """Envía la alerta si no se envió la misma (clave) en las últimas HORAS_ENTRE_ALERTAS."""
    envios = _ultimos_envios()
    if time.time() - envios.get(clave, 0) < HORAS_ENTRE_ALERTAS * 3600:
        return False
    logger.error("ALERTA %s: %s", clave, mensaje)
    if not enviar_telegram(f"⚠️ GE Documental — {mensaje}"):
        return False
    envios[clave] = time.time()
    try:
        with open(REGISTRO_ENVIOS, 'w', encoding='utf-8') as f:
            json.dump(envios, f)
    except OSError as exc:
        logger.warning("No se pudo guardar el registro de alertas: %s", exc)
    return True


def alertar_error_openai(exc: Exception) -> None:
    """Clasifica el error de OpenAI: clave inválida (401) o sin saldo (429 insufficient_quota)."""
    texto = str(exc).lower()
    nombre = type(exc).__name__
    if nombre == 'AuthenticationError' or 'invalid_api_key' in texto or 'incorrect api key' in texto:
        alertar(IA_CLAVE_INVALIDA, "La revisión IA falla: la clave de OpenAI es inválida o fue revocada (401). "
                                   "Los documentos no se están revisando. Actualice OPENAI_API_KEY en el .env.")
    elif 'insufficient_quota' in texto or 'exceeded your current quota' in texto or 'billing' in texto:
        alertar(IA_SIN_SALDO, "La revisión IA falla: la cuenta de OpenAI no tiene saldo (insufficient_quota). "
                              "Recargue créditos en platform.openai.com; los documentos no se están revisando.")
    elif nombre in ('APIConnectionError', 'APITimeoutError', 'InternalServerError'):
        alertar(IA_ERROR, f"La revisión IA no puede conectarse con OpenAI ({nombre}). Si persiste, los "
                          "documentos quedan sin revisar; el cron los revisará cuando vuelva la conexión.")

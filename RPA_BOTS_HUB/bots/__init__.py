from .base_bot import BaseEPSBot
from .bot_colsanitas import BotColsanitas
from .bot_salud_total import BotSaludTotal
from .bot_fomag import BotFomag
from .bot_capital_salud import BotCapitalSalud

# Registro central de bots disponibles por código o alias
REGISTRO_BOTS = {
    "colsanitas": BotColsanitas,
    "sanitas": BotColsanitas,
    "eps_sanitas": BotColsanitas,
    "salud_total": BotSaludTotal,
    "saludtotal": BotSaludTotal,
    "eps_salud_total": BotSaludTotal,
    "fomag": BotFomag,
    "horus": BotFomag,
    "eps_fomag": BotFomag,
    "capital_salud": BotCapitalSalud,
    "capitalsalud": BotCapitalSalud,
    "eps_capital_salud": BotCapitalSalud
}

def obtener_bot(codigo_eps: str) -> BaseEPSBot:
    """Retorna una instancia del bot correspondiente a la EPS solicitada."""
    clave = str(codigo_eps).strip().lower()
    bot_class = REGISTRO_BOTS.get(clave)
    if not bot_class:
        raise ValueError(
            f"EPS '{codigo_eps}' no soportada actualmente. Bots disponibles: {list(REGISTRO_BOTS.keys())}"
        )
    return bot_class()

def listar_eps_disponibles() -> list:
    """Retorna la lista de EPS configuradas en el HUB."""
    vistos = set()
    lista = []
    for codigo, cls in REGISTRO_BOTS.items():
        if cls.NOMBRE_EPS not in vistos:
            vistos.add(cls.NOMBRE_EPS)
            lista.append({
                "codigo": cls.CODIGO_EPS,
                "nombre": cls.NOMBRE_EPS,
                "alias": [k for k, v in REGISTRO_BOTS.items() if v == cls]
            })
    return lista

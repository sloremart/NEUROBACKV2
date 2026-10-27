import os
import sys
from dotenv import load_dotenv

# Cargar variables de entorno del archivo .env si existe
load_dotenv()

from bots import obtener_bot, listar_eps_disponibles, REGISTRO_BOTS

class BotHub:
    """
    Fachada central para invocar cualquier bot del HUB desde proyectos externos.
    """
    
    @classmethod
    def validar(
        cls,
        eps: str,
        doc_tipo: str,
        doc_num: str,
        headless: bool = True,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        """
        Consulta síncrona universal:
        Ejemplo:
            resultado = BotHub.validar("colsanitas", "CC", "17337293", headless=False)
        """
        bot = obtener_bot(eps)
        return bot.consultar_sync(
            doc_tipo=doc_tipo,
            doc_num=doc_num,
            headless=headless,
            evidencias_dir=evidencias_dir,
            **kwargs
        )

    @classmethod
    async def validar_async(
        cls,
        eps: str,
        doc_tipo: str,
        doc_num: str,
        headless: bool = True,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        """
        Consulta asíncrona para aplicaciones basadas en asyncio (FastAPI, Quart, etc.)
        """
        bot = obtener_bot(eps)
        return await bot.consultar(
            doc_tipo=doc_tipo,
            doc_num=doc_num,
            headless=headless,
            evidencias_dir=evidencias_dir,
            **kwargs
        )

    @classmethod
    def eps_disponibles(cls) -> list:
        """Retorna lista de todas las EPS soportadas."""
        return listar_eps_disponibles()

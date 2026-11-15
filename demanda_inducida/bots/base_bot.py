import os
import re
import asyncio
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth

class BaseEPSBot(ABC):
    """
    Clase base para todos los bots de validación de EPS.
    Define el contrato que cada bot debe implementar para mantener
    una estructura uniforme e intercambiable.
    """
    NOMBRE_EPS = "BASE"
    CODIGO_EPS = "base"

    def __init__(self, default_headless: bool = True):
        self.default_headless = default_headless
        self.stealth = Stealth()

    def limpiar_documento(self, doc_num: str) -> str:
        """Elimina puntos, comas, guiones o espacios del documento."""
        return re.sub(r'[^0-9A-Za-z]', '', str(doc_num).strip())

    def obtener_evidencias_dir(self, override_dir: str = None) -> str:
        """Determina la ruta absoluta donde se guardarán las capturas de auditoría."""
        if override_dir:
            evidencias_dir = override_dir
        else:
            base_hub = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            evidencias_dir = os.path.join(base_hub, "data", "evidencias")
        os.makedirs(evidencias_dir, exist_ok=True)
        return evidencias_dir

    @staticmethod
    def traer_navegador_al_frente():
        """Fuerza la ventana de Google Chrome for Testing / Chromium a mostrarse en primer plano en Windows."""
        if os.name != "nt":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value
                        if (
                            "Chrome for Testing" in title
                            or "Chromium" in title
                            or "about:blank" in title
                            or "Capital Salud" in title
                            or "Horus" in title
                            or "Oficina Virtual" in title
                            or "Sanitas" in title
                            or "Colsanitas" in title
                        ):
                            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
                            user32.ShowWindow(hwnd, 3)   # SW_SHOWMAXIMIZED
                            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # HWND_TOPMOST
                            user32.SetForegroundWindow(hwnd)
                return True

            user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass

    async def activar_visor_en_vivo(self, page, use_headless: bool, ev_dir: str):
        """
        1) Si use_headless=False, trae la ventana física del navegador al frente de la pantalla.
        2) Inicia una tarea asíncrona que guarda capturas periódicas en `live_preview.jpg`
           para que el Laboratorio Web Visual muestre en vivo el paso a paso dentro de la página.
        """
        if not use_headless:
            try:
                await page.bring_to_front()
            except Exception:
                pass
            self.traer_navegador_al_frente()

        live_path = os.path.join(ev_dir, "live_preview.jpg")

        async def _loop_capturas():
            while not page.is_closed():
                try:
                    if not use_headless:
                        self.traer_navegador_al_frente()
                    await page.screenshot(path=live_path, type="jpeg", quality=65, full_page=False)
                except Exception:
                    pass
                await asyncio.sleep(0.8)

        asyncio.create_task(_loop_capturas())

    def crear_estructura_resultado(self, doc_tipo: str, doc_num: str, evidencia_filename: str) -> dict:
        """Estructura estándar de respuesta garantizada para cualquier EPS."""
        return {
            "status": "ERROR",                      # AUTORIZADO | SIN_VOLANTE | SIN_CONTRATO | ERROR | etc.
            "eps": self.CODIGO_EPS,
            "eps_nombre": self.NOMBRE_EPS,
            "doc_tipo": doc_tipo,
            "doc_num": self.limpiar_documento(doc_num),
            "paciente_nombre": None,
            "estado_eps": "NO_DETERMINADO",
            "num_autorizacion": None,
            "cups": None,
            "servicio": None,
            "fecha_aprobacion": None,
            "fecha_vigencia": None,
            "prestador": None,
            "procedimientos": [],
            "soporte_evidencia": evidencia_filename,
            "mensaje": "",
            "fecha_consulta": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    @abstractmethod
    async def consultar(
        self,
        doc_tipo: str,
        doc_num: str,
        headless: bool = None,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        """
        Método asíncrono que cada bot específico debe implementar.
        Debe devolver el diccionario con la estructura estándar.
        """
        pass

    def consultar_sync(
        self,
        doc_tipo: str,
        doc_num: str,
        headless: bool = None,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        """Wrapper síncrono para ser consumido fácilmente sin necesidad de async/await."""
        return asyncio.run(
            self.consultar(
                doc_tipo=doc_tipo,
                doc_num=doc_num,
                headless=headless if headless is not None else self.default_headless,
                evidencias_dir=evidencias_dir,
                **kwargs
            )
        )

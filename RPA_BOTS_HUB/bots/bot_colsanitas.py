import os
import shutil
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from .base_bot import BaseEPSBot

class BotColsanitas(BaseEPSBot):
    """
    Bot RPA para el Validador de Derechos de Colsanitas / EPS Sanitas.
    Tecnología destino: JBoss Seam / JavaServer Faces con protección Radware.
    """
    NOMBRE_EPS = "EPS Sanitas / Colsanitas"
    CODIGO_EPS = "colsanitas"

    def __init__(self, eps_user: str = None, eps_pass: str = None, default_headless: bool = True):
        super().__init__(default_headless=default_headless)
        self.eps_user = eps_user or os.getenv("EPS_SAN_USER", "")
        self.eps_pass = eps_pass or os.getenv("EPS_SAN_PASS", "")

    async def consultar(
        self,
        doc_tipo: str,
        doc_num: str,
        headless: bool = None,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        is_headless = headless if headless is not None else self.default_headless
        dir_evidencias = self.obtener_evidencias_dir(evidencias_dir)

        doc_limpio = self.limpiar_documento(doc_num)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        evidencia_filename = f"evidencia_{self.CODIGO_EPS}_{doc_tipo}_{doc_limpio}_{timestamp}.png"
        evidencia_path = os.path.join(dir_evidencias, evidencia_filename)

        resultado = self.crear_estructura_resultado(doc_tipo, doc_limpio, evidencia_filename)
        fecha_minima = str(kwargs.get("fecha_minima") or "").strip()[:10]

        def _norm_fecha(val: str) -> str:
            import re as _re
            s = str(val or "").strip()
            m_iso = _re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
            if m_iso:
                return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
            m_lat = _re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
            if m_lat:
                return f"{int(m_lat.group(3)):04d}-{int(m_lat.group(2)):02d}-{int(m_lat.group(1)):02d}"
            return ""

        base_hub = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        user_data_dir = os.path.join(base_hub, "data", ".session_colsanitas")
        os.makedirs(user_data_dir, exist_ok=True)

        login_url = "https://portal.colsanitas.com/sso/login?service=https%3A%2F%2Fappcore.colsanitas.com%2FValidadorDerechos%2Fpages%2Fgestion%2FValidacionDerechos.seam%3FflagConsulta%3D1"
        form_url  = "https://appcore.colsanitas.com/ValidadorDerechos/pages/gestion/ValidacionDerechos.seam?flagConsulta=1"

        async with async_playwright() as p:
            context = None
            page = None
            try:
                launch_kwargs = {
                    "headless": is_headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--start-maximized"
                    ],
                    "no_viewport": True
                }
                if os.name == 'nt':
                    launch_kwargs["channel"] = "chrome"

                context = await p.chromium.launch_persistent_context(user_data_dir, **launch_kwargs)
                page = context.pages[0] if context.pages else await context.new_page()
                await self.stealth.apply_stealth_async(page)
                await self.activar_visor_en_vivo(page, is_headless, dir_evidencias)

                # 1. Ir al login (redirige al validador si ya hay sesión)
                await page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(2)

                # 2. Si aparece formulario de login, ingresar credenciales
                if await page.query_selector("#username"):
                    await page.fill("#username", self.eps_user)
                    await page.fill("#password", self.eps_pass)
                    await page.click("input[value='Ingresar']")
                    await asyncio.sleep(6)

                # 3. Navegar directamente al formulario de validación (limpia estado anterior)
                await page.goto(form_url, wait_until="domcontentloaded")
                await asyncio.sleep(3)

                # 4. Verificar que el formulario cargó; si no, limpiar sesión y reintentar
                try:
                    await page.wait_for_selector("select[id='formaVDGeneral:selectOneTipoDoc']", timeout=25000)
                except Exception:
                    await context.close()
                    if os.path.exists(user_data_dir):
                        shutil.rmtree(user_data_dir, ignore_errors=True)
                    os.makedirs(user_data_dir, exist_ok=True)
                    context = await p.chromium.launch_persistent_context(user_data_dir, **launch_kwargs)
                    page = context.pages[0] if context.pages else await context.new_page()
                    await self.stealth.apply_stealth_async(page)
                    await page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                    if await page.query_selector("#username"):
                        await page.fill("#username", self.eps_user)
                        await page.fill("#password", self.eps_pass)
                        await page.click("input[value='Ingresar']")
                        await asyncio.sleep(6)
                    await page.goto(form_url, wait_until="domcontentloaded")
                    await asyncio.sleep(3)
                    await page.wait_for_selector("select[id='formaVDGeneral:selectOneTipoDoc']", timeout=40000)

                # 5. Seleccionar tipo de consulta por Documento
                await page.evaluate("""() => {
                    const radio = document.getElementById('formaVDGeneral:radioTipoConsultaTipoDoc:0');
                    if (radio) { radio.checked = true; radio.click(); if (radio.onchange) radio.onchange(); }
                }""")
                await asyncio.sleep(2)

                # 6. Llenar tipo de doc y número (limpiar campo antes)
                await page.select_option("select[id='formaVDGeneral:selectOneTipoDoc']", value=doc_tipo)
                doc_sel = "input[id='formaVDGeneral:numDocumento']"
                await page.fill(doc_sel, "")
                await page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (el) { el.value = ''; }
                }""", doc_sel)
                await page.click(doc_sel)
                await page.fill(doc_sel, doc_limpio)
                await asyncio.sleep(1)

                # 7. Clic en la lupa de búsqueda
                await page.evaluate("""() => {
                    const btn = document.querySelector("a[title='Buscar por documento']") || document.querySelector("a.icono-buscar");
                    if (btn) btn.click();
                }""")
                await asyncio.sleep(4)

                # 8. Seleccionar Compañía 'Todas'
                cia_sel = "select[id='formaVDGeneral:selectOneCiaId']"
                await page.select_option(cia_sel, value="Todas")
                await page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (el && el.onchange) el.onchange();
                }""", cia_sel)
                await asyncio.sleep(4)

                # 9. Seleccionar Contrato EPS
                selected_contract = await page.evaluate("""() => {
                    const labels = document.querySelectorAll("label");
                    for (let lbl of labels) {
                        if (lbl.innerText && lbl.innerText.includes("EPS") && lbl.innerText.includes("REGIMEN CONTRIBUTIVO")) {
                            lbl.click();
                            const inp = document.getElementById(lbl.getAttribute('for')) || lbl.querySelector('input');
                            if (inp) { inp.checked = true; inp.click(); }
                            return lbl.innerText.trim();
                        }
                    }
                    if (labels.length > 0) {
                        for (let lbl of labels) {
                            if (lbl.getAttribute('for') && lbl.getAttribute('for').includes('selectOnePlanFam')) {
                                lbl.click();
                                return lbl.innerText.trim();
                            }
                        }
                    }
                    return null;
                }""")
                await asyncio.sleep(2)

                if not selected_contract:
                    resultado["status"] = "SIN_CONTRATO"
                    resultado["mensaje"] = "No se encontraron contratos activos en EPS para este documento."
                    await page.screenshot(path=evidencia_path, full_page=True)
                    return resultado

                # 10. Clic en Continuar (hacia vista de paciente)
                await page.click("input[id='formaVDGeneral:btnConsultarUsuario']")
                await asyncio.sleep(8)

                # 11. Clic en 'Servicios con Autorización'
                lbl_selector = "label[for='formaVDGeneral:servicios:includeViasIngreso:formViaIngreso:j_id165:1']"
                lbl_locator = page.locator(lbl_selector)
                box = await lbl_locator.bounding_box()
                if box:
                    await page.mouse.click(box['x'] + 10, box['y'] + 10)
                await asyncio.sleep(3)

                # 12. Clic en Continuar Paso 0 -> Paso 1
                btn_paso0_id = "formaVDGeneral:servicios:includeSeleccionUsuario:formaSeleccionUsuario:continuarPaso0"
                await page.evaluate(f"""() => {{
                    const btn = document.getElementById('{btn_paso0_id}');
                    if (btn) btn.click();
                }}""")
                await asyncio.sleep(8)

                await page.screenshot(path=evidencia_path, full_page=True)

                # 13. Extraer datos de la tabla de autorizaciones
                body_text = await page.evaluate("() => document.body.innerText")

                volante_data = await page.evaluate("""() => {
                    let res = null;
                    const rows = Array.from(document.querySelectorAll("tr"));
                    for (let r of rows) {
                        const cells = Array.from(r.querySelectorAll("td")).map(c => c.innerText.trim());
                        for (let i = 0; i < cells.length; i++) {
                            if (/^[0-9]{7,12}$/.test(cells[i])) {
                                res = {
                                    num_autorizacion: cells[i],
                                    tipo: cells[i+1] || '',
                                    fecha_aprobacion: cells[i+2] || '',
                                    fecha_vigencia: cells[i+3] || '',
                                    estado: cells[i+4] || 'APROBADA',
                                    prestador: cells[i+5] || '',
                                    cups: '',
                                    servicio: ''
                                };
                                break;
                            }
                        }
                        if (res) break;
                    }
                    if (res) {
                        for (let r of rows) {
                            const cells = Array.from(r.querySelectorAll("td")).map(c => c.innerText.trim());
                            for (let i = 0; i < cells.length; i++) {
                                if (/^[0-9]{6}$/.test(cells[i])) {
                                    res.cups = cells[i];
                                    res.servicio = cells[i+1] || '';
                                    break;
                                }
                            }
                            if (res.cups) break;
                        }
                    }
                    return res;
                }""")

                if volante_data and volante_data.get("num_autorizacion"):
                    f_aprob_iso = _norm_fecha(volante_data.get("fecha_aprobacion"))
                    if fecha_minima and f_aprob_iso and f_aprob_iso < fecha_minima:
                        resultado["status"] = "SIN_VOLANTE"
                        resultado["estado_eps"] = "VOLANTE_ANTERIOR"
                        resultado["mensaje"] = f"El volante #{volante_data['num_autorizacion']} tiene fecha {f_aprob_iso}, anterior a la orden ({fecha_minima})."
                        return resultado
                    resultado["status"] = "AUTORIZADO"
                    resultado["estado_eps"] = volante_data.get("estado", "APROBADA")
                    resultado["num_autorizacion"] = volante_data["num_autorizacion"]
                    resultado["cups"] = volante_data.get("cups")
                    resultado["servicio"] = volante_data.get("servicio")
                    resultado["fecha_aprobacion"] = volante_data.get("fecha_aprobacion")
                    resultado["fecha_vigencia"] = volante_data.get("fecha_vigencia")
                    resultado["prestador"] = volante_data.get("prestador")
                    resultado["procedimientos"] = [{
                        "cups": volante_data.get("cups") or "",
                        "codigo_servicio": volante_data.get("cups") or "",
                        "servicio": volante_data.get("servicio") or "",
                        "cantidad": "1",
                        "num_autorizacion": volante_data["num_autorizacion"],
                        "estado": volante_data.get("estado", "APROBADA"),
                        "fecha_aprobacion": volante_data.get("fecha_aprobacion") or "",
                        "fecha_vigencia": volante_data.get("fecha_vigencia") or "",
                        "prestador": volante_data.get("prestador") or ""
                    }]
                    resultado["mensaje"] = f"Volante #{volante_data['num_autorizacion']} vigente hasta {volante_data.get('fecha_vigencia')}."
                elif "no tiene volantes expedidos" in body_text:
                    resultado["status"] = "SIN_VOLANTE"
                    resultado["estado_eps"] = "SIN_VOLANTES"
                    resultado["mensaje"] = "El usuario no tiene volantes expedidos para el prestador."
                else:
                    resultado["status"] = "SIN_VOLANTE"
                    resultado["estado_eps"] = "SIN_VOLANTES"
                    resultado["mensaje"] = "No se encontraron volantes activos expedidos en el portal."

            except Exception as e:
                resultado["status"] = "ERROR"
                resultado["mensaje"] = str(e)
                if page:
                    try:
                        await page.screenshot(path=evidencia_path, full_page=True)
                    except:
                        pass
            finally:
                if context:
                    try:
                        await context.close()
                    except:
                        pass

            return resultado

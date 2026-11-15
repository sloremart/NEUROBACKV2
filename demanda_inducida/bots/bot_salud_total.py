import os
import re
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from .base_bot import BaseEPSBot

class BotSaludTotal(BaseEPSBot):
    """
    Bot RPA para Oficina Virtual IPS de Salud Total EPS.
    Flujo:
      1. Login en https://transaccional.saludtotal.com.co/OficinaVirtual/#/ (pestaña IPS)
      2. Clic en DIRECCIONAMIENTOS
      3. Clic en REGISTRAR DIRECCIONAMIENTO
      4. Modal INFORMACIÓN SEDE -> Sucursal META, Sede NEUROELECTRODIAGNOSTICO SH DEL LLANO SAS -> ACEPTAR
      5. Ingresar Número de Documento del paciente -> Clic CONSULTAR (superior)
      6. Clic pestaña CONSULTAR DIRECCIONAMIENTO -> Clic botón CONSULTAR (inferior)
      7. Extraer tabla inferior de procedimientos con códigos CUPS, números de direccionamiento y vigencias.
    """
    NOMBRE_EPS = "Salud Total EPS"
    CODIGO_EPS = "salud_total"

    def __init__(
        self,
        tipo_doc_ips: str = None,
        nit_ips: str = None,
        tipo_doc_usr: str = None,
        doc_usr: str = None,
        password: str = None,
        sucursal: str = None,
        sede: str = None,
        default_headless: bool = True
    ):
        super().__init__(default_headless=default_headless)
        self.tipo_doc_ips = tipo_doc_ips or os.getenv("EPS_ST_TIPO_DOC_IPS", "NIT")
        self.nit_ips = nit_ips or os.getenv("EPS_ST_NIT_IPS", "901119103")
        self.tipo_doc_usr = tipo_doc_usr or os.getenv("EPS_ST_TIPO_DOC_USR", "CEDULA DE CIUDADANIA")
        self.doc_usr = doc_usr or os.getenv("EPS_ST_DOC_USR", "9011191031")
        self.password = password or os.getenv("EPS_ST_PASS", "LLANO031")
        self.sucursal = sucursal or os.getenv("EPS_ST_SUCURSAL", "META")
        self.sede = sede or os.getenv("EPS_ST_SEDE", "NEUROELECTRODIAGNOSTICO SH DEL LLANO SAS")

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
        cups_esperado = str(kwargs.get("cups_esperado", "")).strip()
        fecha_minima = str(kwargs.get("fecha_minima") or "").strip()[:10]

        def _norm_fecha(val: str) -> str:
            s = str(val or "").strip()
            m_iso = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
            if m_iso:
                return f"{int(m_iso.group(1)):04d}-{int(m_iso.group(2)):02d}-{int(m_iso.group(3)):02d}"
            m_lat = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
            if m_lat:
                return f"{int(m_lat.group(3)):04d}-{int(m_lat.group(2)):02d}-{int(m_lat.group(1)):02d}"
            return ""

        async with async_playwright() as p:
            browser = None
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
                    ]
                }
                if os.name == 'nt' and not is_headless:
                    launch_kwargs["channel"] = "chrome"

                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(no_viewport=True if not is_headless else False, viewport={"width": 1440, "height": 900} if is_headless else None)
                page = await context.new_page()
                await self.stealth.apply_stealth_async(page)
                await self.activar_visor_en_vivo(page, is_headless, dir_evidencias)

                # =========================================================================
                # PASO 1 (Imagen 1): Login en Oficina Virtual Salud Total -> Pestaña IPS
                # =========================================================================
                login_url = "https://transaccional.saludtotal.com.co/OficinaVirtual/#/"
                await page.goto(login_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(2)

                # Seleccionar pestaña IPS
                tab_ips = page.locator("#k-tabstrip-tab-3, li.k-item:has-text('IPS')").first
                await tab_ips.click()
                await asyncio.sleep(1.5)

                # 1.1 Tipo Documento IPS (NIT)
                inp_tipo_ips = page.locator("input[placeholder='TIPO DE DOCUMENTO DE LA IPS']")
                await inp_tipo_ips.click()
                await inp_tipo_ips.fill(self.tipo_doc_ips)
                await asyncio.sleep(0.8)
                li_nit = page.locator(f"li.k-item:has-text('{self.tipo_doc_ips}')").first
                if await li_nit.count() > 0:
                    await li_nit.click()
                else:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(0.5)

                # 1.2 Número Identificación IPS (901119103)
                inp_nit = page.locator("input[placeholder*='IDENTIFICACI']")
                await inp_nit.click()
                await inp_nit.fill(self.nit_ips)
                await asyncio.sleep(0.5)

                # 1.3 Tipo Documento Usuario (CEDULA DE CIUDADANIA)
                inp_tipo_usr = page.locator("input[placeholder='TIPO DE DOCUMENTO DEL USUARIO']")
                await inp_tipo_usr.click()
                await inp_tipo_usr.fill("CEDULA")
                await asyncio.sleep(0.8)
                li_cc = page.locator("li.k-item:has-text('CEDULA DE CIUDADANIA')").first
                if await li_cc.count() > 0:
                    await li_cc.click()
                else:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(0.5)

                # 1.4 Número Usuario (9011191031)
                inp_usr = page.locator("input[placeholder*='MERO DEL USUARIO']")
                await inp_usr.click()
                await inp_usr.fill(self.doc_usr)
                await asyncio.sleep(0.5)

                # 1.5 Contraseña (LLANO031)
                inp_pwd = page.locator("input[type='password']")
                await inp_pwd.click()
                await inp_pwd.fill(self.password)
                await asyncio.sleep(0.5)

                # 1.6 Clic en INGRESAR
                await page.click("button.k-button:has-text('INGRESAR')")
                await asyncio.sleep(8)

                # =========================================================================
                # PASO 2 (Imagen 2): Clic en DIRECCIONAMIENTOS
                # =========================================================================
                btn_dir = page.locator("button.cardST").filter(has_text=re.compile(r"^\s*DIRECCIONAMIENTOS\s*$"))
                await btn_dir.wait_for(state="visible", timeout=20000)
                await btn_dir.click()
                await asyncio.sleep(3)

                # =========================================================================
                # PASO 3 (Imagen 3): Clic en REGISTRAR DIRECCIONAMIENTO
                # =========================================================================
                btn_reg = page.locator("button.cardST, a, div").filter(has_text=re.compile(r"^\s*REGISTRAR DIRECCIONAMIENTO\s*$")).first
                await btn_reg.click()
                await asyncio.sleep(4)

                # =========================================================================
                # PASO 4 (Imagen 4): Modal INFORMACIÓN SEDE (Sucursal META / Sede NEURODX)
                # =========================================================================
                inp_sucursal = page.locator("input[placeholder='SELECCIONE UNA SUCURSAL']")
                if await inp_sucursal.count() > 0 and await inp_sucursal.is_visible():
                    await inp_sucursal.click()
                    await inp_sucursal.fill(self.sucursal)
                    await asyncio.sleep(1)
                    li_suc = page.locator("li.k-item").filter(has_text=re.compile(rf"^\s*{re.escape(self.sucursal)}\s*$", re.I)).first
                    if await li_suc.count() > 0:
                        await li_suc.click()
                    else:
                        await page.keyboard.press("Enter")
                    await asyncio.sleep(1.5)

                    inp_sede = page.locator("input[placeholder='SELECCIONE UNA SEDE']")
                    await inp_sede.click()
                    await inp_sede.fill("NEUROELECTRODIAGNOSTICO")
                    await asyncio.sleep(1)
                    li_sede = page.locator("li.k-item:has-text('NEUROELECTRODIAGNOSTICO')").first
                    if await li_sede.count() > 0:
                        await li_sede.click()
                    else:
                        await page.keyboard.press("Enter")
                    await asyncio.sleep(1)

                    btn_aceptar = page.locator("button.k-button:has-text('ACEPTAR')")
                    await btn_aceptar.click()
                    await asyncio.sleep(2.5)

                # =========================================================================
                # PASO 5 (Imagen 5): Número de Documento -> Consultar -> Consultar Direccionamiento -> Consultar
                # =========================================================================
                inp_doc = page.locator("input[formcontrolname='txtNumeroIdentificacion']")
                await inp_doc.click()
                await inp_doc.fill("")
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await inp_doc.fill(doc_limpio)
                await asyncio.sleep(0.5)

                # Clic en el primer botón CONSULTAR (superior)
                btn_consultar_top = page.locator("button.k-button:has-text('CONSULTAR')").first
                await btn_consultar_top.click()
                await asyncio.sleep(4)

                # Verificar si aparece alerta de "No se encontró el afiliado"
                body_after_top = await page.evaluate("() => document.body.innerText")
                if "No se encontró el afiliado" in body_after_top or "No se encontro el afiliado" in body_after_top:
                    resultado["status"] = "SIN_CONTRATO"
                    resultado["estado_eps"] = "NO_AFILIADO"
                    resultado["mensaje"] = f"No se encontró el afiliado con documento {doc_limpio} en Salud Total EPS."
                    await page.screenshot(path=evidencia_path, full_page=True)
                    return resultado

                # Obtener nombre del paciente si cargó
                inp_nombre = page.locator("input[formcontrolname='txtNombreAfiliado']")
                if await inp_nombre.count() > 0:
                    resultado["paciente_nombre"] = (await inp_nombre.input_value()).strip()

                # Clic en pestaña CONSULTAR DIRECCIONAMIENTO
                tab_cons_dir = page.locator("#k-tabstrip-tab-1, li.k-item:has-text('CONSULTAR DIRECCIONAMIENTO')").first
                await tab_cons_dir.click()
                await asyncio.sleep(2)

                # Clic en el botón CONSULTAR dentro de la pestaña CONSULTAR DIRECCIONAMIENTO
                consultar_btns = page.locator("button.k-button:has-text('CONSULTAR'):visible")
                cnt = await consultar_btns.count()
                if cnt > 1:
                    await consultar_btns.nth(cnt - 1).click()
                elif cnt == 1:
                    await consultar_btns.first.click()

                await asyncio.sleep(6)

                # Hacer scroll hacia la tabla inferior para que salga completa en la captura de evidencia
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)
                await page.screenshot(path=evidencia_path, full_page=True)

                # =========================================================================
                # PASO 6: Extraer lista de procedimientos y códigos CUPS de la tabla inferior
                # =========================================================================
                procedimientos = await page.evaluate("""() => {
                    const rows = Array.from(document.querySelectorAll('table tbody tr'));
                    const items = [];
                    for (const tr of rows) {
                        const cells = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
                        // Validar que sea una fila de datos de direccionamiento (tiene > 20 columnas y código de servicio numérico)
                        if (cells.length >= 25 && /^[0-9]{6,12}$/.test(cells[3])) {
                            const codServicio = cells[3];
                            // Salud Total suele enviar 10 dígitos donde los primeros 6 son el CUPS estándar (ej. 8915090000 -> 891509)
                            const cups6 = codServicio.length >= 6 ? codServicio.substring(0, 6) : codServicio;
                            items.push({
                                tipo: cells[2] || 'POS',
                                codigo_servicio: codServicio,
                                cups: cups6,
                                servicio: cells[4] || '',
                                clasificacion: cells[5] || '',
                                cantidad: cells[6] || '1',
                                num_autorizacion: cells[7] || '',
                                direccionamiento_anterior: cells[8] || '',
                                convenio: cells[17] || '',
                                prestador: cells[18] || '',
                                forma_pago: cells[21] || '',
                                valor_cm: cells[22] || '0',
                                estado: cells[23] || '',
                                fecha_aprobacion: cells[24] || '',
                                fecha_uso: cells[25] || '',
                                fecha_vigencia: cells[26] || '',
                                fecha_utilizacion_ips: cells[27] || '',
                                ambito: cells[28] || ''
                            });
                        }
                    }
                    return items;
                }""")

                if fecha_minima and procedimientos:
                    procedimientos = [
                        p_it for p_it in procedimientos
                        if not _norm_fecha(p_it.get("fecha_aprobacion")) or _norm_fecha(p_it.get("fecha_aprobacion")) >= fecha_minima
                    ]

                resultado["procedimientos"] = procedimientos

                if procedimientos and len(procedimientos) > 0:
                    # Si se especificó un CUPS esperado, buscarlo primero; si no, tomar el primero
                    seleccionado = procedimientos[0]
                    if cups_esperado:
                        for p_item in procedimientos:
                            if p_item["cups"] == cups_esperado[:6] or p_item["codigo_servicio"] == cups_esperado:
                                seleccionado = p_item
                                break

                    todos_cups = ", ".join([f"{p['cups']} ({p['servicio']})" for p in procedimientos])
                    resultado["status"] = "AUTORIZADO"
                    resultado["estado_eps"] = seleccionado.get("estado", "DIRECCIONADO")
                    resultado["num_autorizacion"] = seleccionado.get("num_autorizacion")
                    resultado["cups"] = seleccionado.get("cups")
                    resultado["servicio"] = seleccionado.get("servicio")
                    resultado["fecha_aprobacion"] = seleccionado.get("fecha_aprobacion")
                    resultado["fecha_vigencia"] = seleccionado.get("fecha_vigencia")
                    resultado["prestador"] = seleccionado.get("prestador")
                    resultado["mensaje"] = (
                        f"Se encontraron {len(procedimientos)} procedimiento(s) direccionado(s): {todos_cups}."
                    )
                else:
                    resultado["status"] = "SIN_VOLANTE"
                    resultado["estado_eps"] = "SIN_DIRECCIONAMIENTOS"
                    nom_str = f" ({resultado['paciente_nombre']})" if resultado.get("paciente_nombre") else ""
                    resultado["mensaje"] = f"El afiliado {doc_limpio}{nom_str} no tiene direccionamientos registrados para esta sede."

            except Exception as e:
                resultado["status"] = "ERROR"
                resultado["mensaje"] = f"Error en Bot Salud Total: {str(e)}"
                if page:
                    try:
                        await page.screenshot(path=evidencia_path, full_page=True)
                    except:
                        pass
            finally:
                if browser:
                    try:
                        await browser.close()
                    except:
                        pass

            return resultado

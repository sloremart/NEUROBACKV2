import os
import re
import json
import base64
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from .base_bot import BaseEPSBot

load_dotenv()


class BotCapitalSalud(BaseEPSBot):
    """
    Bot RPA para validación de Servicios Autorizados en el portal transaccional
    de Capital Salud EPS-S:
    https://portal.capitalsalud.gov.co/AutorizacionWebWS/Access/Index#headingTwo

    Flujo automatizado:
    1. Ingreso en `Access/Index`:
       - Tipo de Usuario: `IPS`
       - Identificación: `IPSNEDSHDELLLANO2`
       - Contraseña: `Neuro2026.`
       - Clic en botón `Ingresar` (`#BtnLogin`).
    2. Pantalla de Bienvenida (`/AutorizacionWebWS/Home`):
       - Clic en botón `Aceptar`.
    3. Selección de Punto de Atención (`/AutorizacionWebWS/Home/IngresoIPS`):
       - Selecciona `NEUROELCTRODX DEL LLANO NO RED` (`#cboPuntosAtencionIPS`).
       - Clic en el botón circular `Continuar` (`#btnContinuar`).
    4. Consulta de Afiliado (`/AutorizacionWebWS/Afiliado`):
       - Selecciona Tipo de Identificación (`#cboTipoDocumento`) e ingresa Número (`#txtNoIdentificacion`).
       - Clic en botón circular de lupa (`#btnBuscarAfiliado`).
       - Extrae nombres, apellidos, estado, plan y sede cápita del afiliado.
    5. Extracción de Servicios Autorizados:
       - Clic en botón `Ver Servicios Autorizados` (`#btnVerAutorizaciones`).
       - Extrae la lista completa de autorizaciones desde `gridApiAutorizaciones` (CUPS `IdServicio`,
         `DescripcionServicio`, `NumAutorizacion`, `cantidad_servicio`, `PuntoAtencion`,
         `fecha_autorizacion`, `fecha_vencimiento`, `dias_vigencia`, `observacion`, etc.).
    """

    NOMBRE_EPS = "CAPITAL SALUD"
    CODIGO_EPS = "capital_salud"
    URL_LOGIN = "https://portal.capitalsalud.gov.co/AutorizacionWebWS/Access/Index#headingTwo"

    MAPA_TIPOS_DOC = {
        "CC": "1",
        "NI": "2",
        "NIT": "2",
        "TI": "3",
        "CE": "4",
        "PA": "5",
        "CD": "6",
        "RC": "7",
        "UN": "8",
        "CN": "9",
        "SC": "10",
        "PE": "11",
        "AS": "12",
        "MS": "13",
        "PT": "14",
        "PC": "17",
    }

    def __init__(self, default_headless: bool = True):
        super().__init__(default_headless=default_headless)
        self.tipo_usuario = os.getenv("EPS_CS_TIPO_USUARIO", "IPS")
        self.user = os.getenv("EPS_CS_USER", "")
        self.password = os.getenv("EPS_CS_PASS", "")
        self.punto_atencion = os.getenv("EPS_CS_PUNTO_ATENCION", "NEUROELCTRODX DEL LLANO NO RED")

    @staticmethod
    async def _cerrar_modales_bloqueantes(page):
        """Cierra modales informativos de SweetAlert2 o loaders que intercepten clics."""
        try:
            await page.evaluate("""() => {
                const confirmBtn = document.querySelector('.swal2-confirm');
                if (confirmBtn && confirmBtn.offsetParent !== null) {
                    confirmBtn.click();
                }
                const closeBtn = document.querySelector('#close-modal');
                if (closeBtn && closeBtn.offsetParent !== null) {
                    closeBtn.click();
                }
                const swalContainer = document.querySelector('.swal2-container');
                if (swalContainer) swalContainer.remove();
            }""")
        except Exception:
            pass

    async def consultar(
        self,
        doc_tipo: str,
        doc_num: str,
        headless: bool = None,
        evidencias_dir: str = None,
        **kwargs
    ) -> dict:
        use_headless = self.default_headless if headless is None else headless
        doc_num_clean = self.limpiar_documento(doc_num)
        ev_dir = self.obtener_evidencias_dir(evidencias_dir)

        evidencia_filename = f"evidencia_capitalsalud_{doc_num_clean}.png"
        evidencia_path = os.path.join(ev_dir, evidencia_filename)

        res = self.crear_estructura_resultado(doc_tipo, doc_num_clean, evidencia_filename)

        if not self.user or not self.password:
            res["status"] = "ERROR_CONFIGURACION"
            res["mensaje"] = "Faltan credenciales EPS_CS_USER o EPS_CS_PASS en el archivo .env"
            return res

        codigo_tipo_doc = self.MAPA_TIPOS_DOC.get(str(doc_tipo).strip().upper(), "1")
        fecha_minima = str(kwargs.get("fecha_minima") or "").strip()[:10]
        fecha_minima_slash = fecha_minima.replace("-", "/") if fecha_minima else ""

        async with async_playwright() as p:
            launch_kwargs = {
                "headless": use_headless,
                "slow_mo": 250 if not use_headless else 0,
                "args": ["--disable-blink-features=AutomationControlled", "--start-maximized"]
            }
            if os.name == "nt" and not use_headless:
                launch_kwargs["channel"] = "chrome"

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                no_viewport=True if not use_headless else False,
                viewport={"width": 1440, "height": 900} if use_headless else None,
                ignore_https_errors=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)
            await self.activar_visor_en_vivo(page, use_headless, ev_dir)

            try:
                # 1. Ir al Login de Capital Salud
                await page.goto(self.URL_LOGIN, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1500)
                await self._cerrar_modales_bloqueantes(page)

                await page.select_option("#cboTiposUsuario", self.tipo_usuario)
                await page.fill("#txtIdentificacion", self.user)
                await page.fill("#txtContrasena", self.password)
                await self._cerrar_modales_bloqueantes(page)
                await page.click("#BtnLogin")
                await page.wait_for_timeout(3500)

                # Verificar si hubo error de login
                if "Access" in page.url:
                    swal_err = await page.evaluate("""() => {
                        const el = document.querySelector('.swal2-html-container, .swal2-content, .swal2-title');
                        return el ? el.innerText.trim() : null;
                    }""")
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "ERROR_CREDENCIALES"
                    res["mensaje"] = f"No fue posible iniciar sesión en Capital Salud: {swal_err or 'Credenciales rechazadas'}"
                    return res

                # 2. Pantalla de Bienvenida (/AutorizacionWebWS/Home) -> Clic en Aceptar
                await self._cerrar_modales_bloqueantes(page)
                await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
                    const aceptar = btns.find(b => (b.innerText || b.value || '').trim().toLowerCase() === 'aceptar' && !b.className.includes('swal2'));
                    if (aceptar) aceptar.click();
                }""")
                await page.wait_for_timeout(3000)

                # 3. Selección de Punto de Atención (/AutorizacionWebWS/Home/IngresoIPS) -> Clic botón circular #btnContinuar
                await self._cerrar_modales_bloqueantes(page)
                if await page.locator("#cboPuntosAtencionIPS").count() > 0:
                    await page.evaluate(
                        """(filtroPunto) => {
                            const s = document.querySelector('#cboPuntosAtencionIPS');
                            if (!s) return;
                            const opt = Array.from(s.options).find(o =>
                                o.text.toUpperCase().includes(filtroPunto.toUpperCase()) ||
                                o.text.toUpperCase().includes('NEURO')
                            ) || s.options[0];
                            if (opt) {
                                s.value = opt.value;
                                s.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""",
                        self.punto_atencion
                    )
                    await page.wait_for_timeout(600)
                    await page.click("#btnContinuar")
                    await page.wait_for_timeout(3500)

                # 4. Consulta de Afiliado (/AutorizacionWebWS/Afiliado)
                await self._cerrar_modales_bloqueantes(page)
                await page.select_option("#cboTipoDocumento", codigo_tipo_doc)
                await page.fill("#txtNoIdentificacion", "")
                await page.fill("#txtNoIdentificacion", doc_num_clean)

                # Consultar afiliado usando el endpoint interno / fcBuscarAfiliado para capturar respuesta exacta
                afiliado_res = await page.evaluate(
                    """async ({ tipoDoc, numDoc }) => {
                        return new Promise((resolve) => {
                            const url = `/AutorizacionWebWS/Afiliado/BuscarAfiliado?sTipoDoc=${encodeURIComponent(tipoDoc)}&sNumeroIdentificacion=${encodeURIComponent(numDoc)}`;
                            $.post(url, function (data) {
                                try {
                                    const parsed = JSON.parse(data);
                                    if (parsed.codigo === "OK") {
                                        const datos = JSON.parse(parsed.resultado);
                                        if (typeof CargarAfiliado === "function") {
                                            CargarAfiliado(datos);
                                        }
                                        resolve({ ok: true, datos });
                                    } else {
                                        resolve({ ok: false, codigo: parsed.codigo, error: parsed.resultado });
                                    }
                                } catch (e) {
                                    resolve({ ok: false, error: String(e) });
                                }
                            }).fail(function (xhr) {
                                resolve({ ok: false, error: "Error HTTP al buscar afiliado: " + xhr.status });
                            });
                        });
                    }""",
                    {"tipoDoc": codigo_tipo_doc, "numDoc": doc_num_clean}
                )
                await page.wait_for_timeout(1500)

                if not afiliado_res or not afiliado_res.get("ok"):
                    await page.screenshot(path=evidencia_path, full_page=True)
                    err_msg = re.sub(r"<[^>]+>", " ", str((afiliado_res or {}).get("error") or "Afiliado no encontrado")).strip()
                    res["status"] = "SIN_CONTRATO"
                    res["estado_eps"] = "NO_AFILIADO"
                    res["mensaje"] = f"Capital Salud: {err_msg}"
                    return res

                # Extraer datos cargados en el formulario del afiliado
                info_afiliado = await page.evaluate("""() => ({
                    nombre: (document.querySelector('#txtNombreAfiliado')?.value || '').trim(),
                    apellido: (document.querySelector('#txtApellidoAfiliado')?.value || '').trim(),
                    estado: (document.querySelector('#txtEstado')?.value || '').trim(),
                    plan: (document.querySelector('#txtPlan')?.value || '').trim(),
                    sede: (document.querySelector('#txtSedeCapita')?.value || '').trim(),
                    ciudad: (document.querySelector('#txtNombreCiudadSede')?.value || '').trim()
                })""")

                nombre_completo = f"{info_afiliado.get('nombre', '')} {info_afiliado.get('apellido', '')}".strip()
                res["paciente_nombre"] = nombre_completo or doc_num_clean
                res["estado_eps"] = (info_afiliado.get("estado") or "ACTIVO").upper()

                # 5. Clic en "Ver Servicios Autorizados" (#btnVerAutorizaciones) y extracción de la lista
                try:
                    btn_ver = page.locator("#btnVerAutorizaciones")
                    if await btn_ver.is_visible():
                        await btn_ver.click()
                    else:
                        await page.evaluate("() => { $('#divGenerarAutorizacion').modal('show'); fcVerAutorizaciones(); }")
                except Exception:
                    await page.evaluate("() => { $('#divGenerarAutorizacion').modal('show'); fcVerAutorizaciones(); }")

                await page.wait_for_timeout(3500)

                # Consultar / extraer directamente las filas de Servicios Autorizados (filtrando por fecha_minima si aplica)
                aut_res = await page.evaluate(
                    """async ({ tipoDoc, numDoc, fechaMinSlash }) => {
                        if (fechaMinSlash) {
                            const inpIni = document.querySelector('#datFechaInicialAutorizacion');
                            if (inpIni) inpIni.value = fechaMinSlash;
                        } else if (typeof gridApiAutorizaciones !== 'undefined' && gridApiAutorizaciones) {
                            const rows = [];
                            gridApiAutorizaciones.forEachNode(n => {
                                if (n && n.data) rows.push(n.data);
                            });
                            if (rows.length > 0) {
                                return { ok: true, rows };
                            }
                        }
                        const fIni = fechaMinSlash || document.querySelector('#datFechaInicialAutorizacion')?.value || '';
                        const fFin = document.querySelector('#datFechaFinalAutorizacion')?.value || '';
                        return new Promise((resolve) => {
                            const url = `/AutorizacionWebWS/Afiliado/VerAutorizaciones?sTipoDoc=${encodeURIComponent(tipoDoc)}&sNumeroIdentificacion=${encodeURIComponent(numDoc)}&sFechaInicial=${encodeURIComponent(fIni)}&sFechaFinal=${encodeURIComponent(fFin)}`;
                            $.post(url, function (data) {
                                try {
                                    const parsed = JSON.parse(data);
                                    if (parsed.codigo === "OK") {
                                        const datos = JSON.parse(parsed.resultado);
                                        if (typeof gridApiAutorizaciones !== 'undefined' && gridApiAutorizaciones) {
                                            gridApiAutorizaciones.setGridOption('rowData', datos);
                                        }
                                        $('#divGenerarAutorizacion').modal('show');
                                        resolve({ ok: true, rows: Array.isArray(datos) ? datos : [] });
                                    } else {
                                        resolve({ ok: false, error: parsed.resultado, rows: [] });
                                    }
                                } catch (e) {
                                    resolve({ ok: false, error: String(e), rows: [] });
                                }
                            }).fail(function () {
                                resolve({ ok: false, error: "Error consultando VerAutorizaciones", rows: [] });
                            });
                        });
                    }""",
                    {"tipoDoc": codigo_tipo_doc, "numDoc": doc_num_clean, "fechaMinSlash": fecha_minima_slash}
                )

                await page.wait_for_timeout(1200)
                await page.screenshot(path=evidencia_path, full_page=True)

                filas = (aut_res or {}).get("rows") or []
                if not filas:
                    msg_portal = re.sub(r"<[^>]+>", " ", str((aut_res or {}).get("error") or "Sin autorizaciones vigentes")).strip()
                    res["status"] = "SIN_VOLANTE"
                    res["mensaje"] = (
                        f"El afiliado {res['paciente_nombre']} ({res['estado_eps']}) no presenta servicios autorizados "
                        f"en el rango consultado ({msg_portal})."
                    )
                    return res

                procedimientos = []
                for item in filas:
                    num_aut = str(item.get("NumAutorizacion") or "").strip()
                    cups_cod = str(item.get("IdServicio") or "").strip()
                    desc_serv = str(item.get("DescripcionServicio") or "").strip()
                    cantidad = item.get("cantidad_servicio", 1)
                    punto_at = str(item.get("PuntoAtencion") or "").strip()
                    nom_punto = str(item.get("NomPuntoAtencion") or "").strip()
                    fecha_aut = str(item.get("fecha_autorizacion") or "").strip()
                    if not fecha_aut and re.match(r"^2[0-9]\d{3}\d{8,12}$", num_aut):
                        try:
                            from datetime import datetime as _dt
                            fecha_aut = _dt.strptime("20" + num_aut[:5], "%Y%j").strftime("%Y-%m-%d")
                        except Exception:
                            pass
                    if fecha_minima and fecha_aut and fecha_aut[:10].replace("/", "-") < fecha_minima:
                        continue
                    fecha_venc = str(item.get("fecha_vencimiento") or item.get("fecha_vigencia") or "").strip()
                    dias_vig = item.get("dias_vigencia", "")
                    observacion = str(item.get("observacion") or "").strip()
                    programa = str(item.get("Programa") or "").strip()

                    prestador_str = f"{punto_at} - {nom_punto}".strip(" -") or self.punto_atencion

                    procedimientos.append({
                        "cups": cups_cod,
                        "codigo_servicio": f"Prog: {programa}" if programa else cups_cod,
                        "servicio": desc_serv,
                        "descripcion": desc_serv,
                        "cantidad": cantidad,
                        "num_autorizacion": num_aut,
                        "estado": f"VIGENTE ({dias_vig}d)" if dias_vig else "AUTORIZADO",
                        "fecha_aprobacion": fecha_aut,
                        "fecha_vigencia": fecha_venc,
                        "fecha_vencimiento": fecha_venc,
                        "prestador": prestador_str,
                        "observacion": observacion
                    })

                if not procedimientos:
                    res["status"] = "SIN_VOLANTE"
                    res["mensaje"] = (
                        f"El afiliado {res['paciente_nombre']} ({res['estado_eps']}) no presenta servicios autorizados "
                        f"con fecha igual o posterior a {fecha_minima or 'la orden'}."
                    )
                    return res

                primera = procedimientos[0]
                todos_cups = ", ".join([p["cups"] for p in procedimientos if p.get("cups")])
                todas_auts = ", ".join(sorted({p["num_autorizacion"] for p in procedimientos if p.get("num_autorizacion")}))

                res["status"] = "AUTORIZADO"
                res["num_autorizacion"] = todas_auts or primera.get("num_autorizacion")
                res["cups"] = todos_cups or primera.get("cups")
                res["servicio"] = (
                    f"{len(procedimientos)} servicio(s): "
                    + " | ".join([f"{p['cups']} - {p['servicio']} (Cant: {p['cantidad']})" for p in procedimientos])
                )
                res["fecha_aprobacion"] = primera.get("fecha_aprobacion")
                res["fecha_vigencia"] = primera.get("fecha_vigencia")
                res["prestador"] = primera.get("prestador")
                res["procedimientos"] = procedimientos
                res["mensaje"] = (
                    f"Se encontraron {len(procedimientos)} servicio(s) autorizado(s) para {res['paciente_nombre']}. "
                    f"Autorización(es): {res['num_autorizacion']} | CUPS: {res['cups']} | Vence: {res['fecha_vigencia']}."
                )
                return res

            except Exception as e:
                try:
                    await page.screenshot(path=evidencia_path, full_page=True)
                except Exception:
                    pass
                res["status"] = "ERROR"
                res["mensaje"] = f"Excepción durante la ejecución del bot de Capital Salud: {str(e)}"
                return res
            finally:
                await browser.close()

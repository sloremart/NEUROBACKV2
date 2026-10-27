import os
import re
import base64
import urllib.request
from datetime import datetime
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from .base_bot import BaseEPSBot

load_dotenv()


class BotFomag(BaseEPSBot):
    """
    Bot RPA para validación de órdenes de servicios y descarga automática de
    Orden PDF + Adjuntos en el portal Horus2 de FOMAG:
    https://horus2.horus-health.com/

    Flujo automatizado:
    1. Ingreso con credenciales (Bypass 100% automático de reCAPTCHA v2 invocando
       `usuarioIngreso` en la instancia Vue/Vuex y cerrando sesiones previas si hay bloqueo 403).
    2. Navegación a Históricos -> Órdenes (`/historico/historico-ordenes`).
    3. Selección de Tipo de Orden: `Servicios` (id=2) y búsqueda por Número de Documento.
    4. Filtrado de órdenes donde `Dirección prestador` (`nombre_rep`) contenga
       `NEUROELECTRODIAGNOSTICO` y `Estado` (`estado_nombre`) sea `Activo`.
    5. Descarga automática en carpeta local (`data/descargas_fomag/<doc_num>/`) de:
       - Orden de servicio en PDF (`/pdf` -> tipo `servicio`).
       - Soportes / Adjuntos clínicos (`transcripciones/buscarAdjuntos` -> `$adjuntos` / URL temporal).
    """

    NOMBRE_EPS = "FOMAG"
    CODIGO_EPS = "fomag"
    URL_PORTAL = "https://horus2.horus-health.com/"

    def __init__(self, default_headless: bool = True):
        super().__init__(default_headless=default_headless)
        self.user = os.getenv("EPS_FOMAG_USER", "")
        self.password = os.getenv("EPS_FOMAG_PASS", "")
        self.prestador_filtro = os.getenv("EPS_FOMAG_PRESTADOR_FILTRO", "NEUROELECTRODIAGNOSTICO")

    def obtener_descargas_dir(self, doc_num_clean: str, override_dir: str = None) -> str:
        """Crea y retorna la carpeta donde se guardan las órdenes y adjuntos del paciente."""
        if override_dir:
            base_dir = override_dir
        else:
            base_hub = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            base_dir = os.path.join(base_hub, "data", "descargas_fomag", doc_num_clean)
        os.makedirs(base_dir, exist_ok=True)
        return base_dir

    @staticmethod
    def _limpiar_nombre_archivo(nombre: str) -> str:
        nombre = re.sub(r'[\\/*?:"<>|]', '_', str(nombre).strip())
        return nombre or "adjunto.pdf"

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
        descargas_dir = self.obtener_descargas_dir(doc_num_clean, kwargs.get("descargas_dir"))

        evidencia_filename = f"evidencia_fomag_{doc_num_clean}.png"
        evidencia_path = os.path.join(ev_dir, evidencia_filename)

        res = self.crear_estructura_resultado(doc_tipo, doc_num_clean, evidencia_filename)
        res["carpeta_descargas"] = descargas_dir
        res["archivos_descargados"] = []

        if not self.user or not self.password:
            res["status"] = "ERROR_CONFIGURACION"
            res["mensaje"] = "Faltan credenciales EPS_FOMAG_USER o EPS_FOMAG_PASS en el archivo .env"
            return res

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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)
            await self.activar_visor_en_vivo(page, use_headless, ev_dir)

            try:
                # 1. Abrir portal Horus2 FOMAG
                await page.goto(self.URL_PORTAL, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(1500)

                # Llenar visualmente los campos de correo y contraseña para trazabilidad visual
                try:
                    inputs = page.locator("input")
                    if await inputs.count() >= 2:
                        await inputs.nth(0).fill(self.user)
                        await inputs.nth(1).fill(self.password)
                except Exception:
                    pass

                # 2. Login directo por acción Vuex `usuarioIngreso` (Bypass 100% de reCAPTCHA v2)
                login_res = await page.evaluate(
                    """async ({ email, password }) => {
                        function findLoginComp(root) {
                            if (!root) return null;
                            if (root.$options && root.$options.methods && root.$options.methods.usuarioIngreso) {
                                return root;
                            }
                            if (root.$children) {
                                for (const c of root.$children) {
                                    const found = findLoginComp(c);
                                    if (found) return found;
                                }
                            }
                            return null;
                        }
                        const loginVm = findLoginComp(window.$nuxt && window.$nuxt.$root);
                        if (!loginVm) {
                            return { ok: false, error: "No se encontró el componente de login en Horus2" };
                        }
                        try {
                            await loginVm.usuarioIngreso({ email, password });
                            return { ok: true };
                        } catch (err) {
                            const status = err && err.response ? err.response.status : null;
                            if (status === 403) {
                                try {
                                    await loginVm.$axios.post('auth/cerrar-sesiones-usuario', { email, password });
                                    await loginVm.usuarioIngreso({ email, password });
                                    return { ok: true, recovered_session: true };
                                } catch (err2) {
                                    return {
                                        ok: false,
                                        error: (err2.response && err2.response.data && err2.response.data.error) || err2.message || String(err2)
                                    };
                                }
                            }
                            return {
                                ok: false,
                                error: (err && err.response && err.response.data && (err.response.data.error || err.response.data.message)) || err.message || String(err)
                            };
                        }
                    }""",
                    {"email": self.user, "password": self.password}
                )

                if not login_res or not login_res.get("ok"):
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "ERROR_CREDENCIALES"
                    res["mensaje"] = f"Error de autenticación en FOMAG Horus2: {(login_res or {}).get('error', 'Desconocido')}"
                    return res

                await page.wait_for_timeout(2500)

                # 3. Navegar a Históricos -> Órdenes (/historico/historico-ordenes)
                await page.evaluate("() => window.$nuxt.$router.push('/historico/historico-ordenes')")
                await page.wait_for_timeout(3500)

                # 4. Seleccionar Tipo de Orden = Servicios (id: 2) y consultar documento
                search_res = await page.evaluate(
                    """async (docNum) => {
                        function findHistoricoComp(root) {
                            if (!root) return null;
                            if (root.$data && 'tipoOrdenes' in root.$data && 'orden' in root.$data) {
                                return root;
                            }
                            if (root.$children) {
                                for (const c of root.$children) {
                                    const r = findHistoricoComp(c);
                                    if (r) return r;
                                }
                            }
                            return null;
                        }
                        const comp = findHistoricoComp(window.$nuxt.$root);
                        if (!comp) return { ok: false, error: "No cargó la vista /historico/historico-ordenes" };

                        comp.orden.tipo_orden = 2; // Servicios
                        comp.orden.numero_documento = docNum;
                        await comp.buscarAfiliadoPorDocumento();

                        // Esperar a que termine loading.buscar
                        for (let i = 0; i < 30; i++) {
                            if (!comp.loading || !comp.loading.buscar) break;
                            await new Promise(r => setTimeout(r, 300));
                        }

                        if (!comp.afiliado) {
                            return { ok: true, afiliado: null };
                        }
                        return { ok: true, afiliado: comp.afiliado };
                    }""",
                    doc_num_clean
                )

                if not search_res or not search_res.get("ok"):
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "ERROR_PORTAL"
                    res["mensaje"] = (search_res or {}).get("error", "Error buscando afiliado en FOMAG")
                    return res

                afiliado = search_res.get("afiliado")
                if not afiliado:
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "NO_ENCONTRADO"
                    res["estado_eps"] = "NO_AFILIADO"
                    res["mensaje"] = f"El documento {doc_num_clean} no se encuentra registrado como afiliado en FOMAG."
                    return res

                # Nombre completo del paciente
                partes_nombre = [
                    afiliado.get("primer_nombre"),
                    afiliado.get("segundo_nombre"),
                    afiliado.get("primer_apellido"),
                    afiliado.get("segundo_apellido")
                ]
                res["paciente_nombre"] = " ".join([p.strip() for p in partes_nombre if p and str(p).strip()])
                res["estado_eps"] = "ACTIVO"

                # Esperar a que el componente hijo HistoricoServicios cargue `ordenesServicios`
                await page.wait_for_timeout(4500)

                ordenes_data = await page.evaluate(
                    """async (filtroPrestador) => {
                        function findServComp(root) {
                            if (!root) return null;
                            if (root.$data && 'ordenesServicios' in root.$data) return root;
                            if (root.$children) {
                                for (const c of root.$children) {
                                    const r = findServComp(c);
                                    if (r) return r;
                                }
                            }
                            return null;
                        }
                        const serv = findServComp(window.$nuxt.$root);
                        if (!serv) return { ok: false, error: "No se encontró el componente HistoricoServicios" };

                        // Esperar si sigue cargando
                        for (let i = 0; i < 30; i++) {
                            if (!serv.loading || !serv.loading.buscar) break;
                            await new Promise(r => setTimeout(r, 300));
                        }

                        // Filtrar visualmente en el campo buscar de la tabla para que la captura muestre NEUROELECTRODIAGNOSTICO
                        if ('buscar' in serv.$data) {
                            serv.buscar = filtroPrestador;
                        }

                        const todas = Array.isArray(serv.ordenesServicios) ? serv.ordenesServicios : [];
                        return {
                            ok: true,
                            total_ordenes: todas.length,
                            ordenes: todas
                        };
                    }""",
                    self.prestador_filtro
                )

                if not ordenes_data or not ordenes_data.get("ok"):
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "ERROR_PORTAL"
                    res["mensaje"] = (ordenes_data or {}).get("error", "No fue posible listar las órdenes de servicios")
                    return res

                todas_ordenes = ordenes_data.get("ordenes", [])
                filtro_upper = self.prestador_filtro.upper()

                # Filtrar órdenes de NEUROELECTRODIAGNOSTICO SH DEL LLANO IPS
                ordenes_neuro = [
                    o for o in todas_ordenes
                    if filtro_upper in str(o.get("nombre_rep") or "").upper()
                ]
                ordenes_neuro_activas_todas = [
                    o for o in ordenes_neuro
                    if str(o.get("estado_nombre") or "").strip().lower() == "activo"
                    or str(o.get("estado_id") or "") == "1"
                ]

                # Regla de negocio: filtrar estrictamente órdenes generadas el mismo día o después de fecha_minima
                # (y nunca descargar órdenes de años anteriores)
                fecha_minima_kw = str(kwargs.get("fecha_minima") or "").strip()[:10]
                fecha_corte = fecha_minima_kw if re.match(r"^\d{4}-\d{2}-\d{2}$", fecha_minima_kw) else f"{datetime.now().year}-01-01"

                cups_filtro_kw = kwargs.get("cups_filtro")
                if isinstance(cups_filtro_kw, str) and cups_filtro_kw.strip():
                    cups_permitidos = {re.sub(r"[^0-9A-Za-z]", "", c).upper() for c in cups_filtro_kw.split(",") if c.strip()}
                elif isinstance(cups_filtro_kw, (list, tuple, set)):
                    cups_permitidos = {re.sub(r"[^0-9A-Za-z]", "", str(c)).upper() for c in cups_filtro_kw if str(c).strip()}
                else:
                    cups_permitidos = set()

                ordenes_neuro_activas = []
                for o in ordenes_neuro_activas_todas:
                    f_crea = str(o.get("fecha_creacion") or "").strip()[:10]
                    if f_crea and f_crea < fecha_corte:
                        continue
                    if cups_permitidos:
                        c_ord = re.sub(r"[^0-9A-Za-z]", "", str(o.get("cup_codigo") or "")).upper()
                        if c_ord and not any(c_ord == cp or cp in c_ord or c_ord in cp for cp in cups_permitidos):
                            continue
                    ordenes_neuro_activas.append(o)

                if not ordenes_neuro_activas:
                    await page.wait_for_timeout(800)
                    await page.screenshot(path=evidencia_path, full_page=True)
                    res["status"] = "SIN_ORDENES"
                    if ordenes_neuro_activas_todas:
                        fechas_ant = sorted({str(o.get("fecha_creacion") or "")[:10] for o in ordenes_neuro_activas_todas if o.get("fecha_creacion")}, reverse=True)
                        ult_f = fechas_ant[0] if fechas_ant else "anterior"
                        res["mensaje"] = (
                            f"El paciente {res['paciente_nombre']} tiene órdenes históricas en FOMAG (última del {ult_f}), "
                            f"pero ninguna autorización vigente para el servicio solicitado con fecha igual o posterior a {fecha_corte}."
                        )
                    elif ordenes_neuro:
                        estados_str = ", ".join(sorted({str(o.get("estado_nombre", "")) for o in ordenes_neuro}))
                        res["mensaje"] = (
                            f"El paciente {res['paciente_nombre']} tiene {len(ordenes_neuro)} orden(es) para "
                            f"{self.prestador_filtro}, pero ninguna está en estado Activo (Estados: {estados_str})."
                        )
                    else:
                        res["mensaje"] = (
                            f"El paciente {res['paciente_nombre']} tiene {len(todas_ordenes)} orden(es) en histórico, "
                            f"pero ninguna direccionada a {self.prestador_filtro}."
                        )
                    return res

                # 5. Para cada orden ACTIVA de NEUROELECTRODIAGNOSTICO, descargar Orden PDF y Adjuntos
                procedimientos = []
                archivos_descargados = []
                adjuntos_por_consulta_cache = {}

                for idx, o in enumerate(ordenes_neuro_activas):
                    consecutivo = str(o.get("consecutivo") or "").strip()
                    orden_id = str(o.get("orden_id") or "").strip()
                    estado_id = str(o.get("estado_id") or "1").strip()
                    consulta_id = str(o.get("consulta_id") or "").strip()
                    cup_codigo = str(o.get("cup_codigo") or "").strip()
                    cup_nombre = str(o.get("cup_nombre") or "").strip()
                    cantidad = o.get("cantidad", 1)
                    fecha_creacion = (str(o.get("fecha_creacion") or "")[:10])
                    fecha_vigencia = (str(o.get("fecha_vigencia") or "")[:10])
                    prestador_nombre = str(o.get("nombre_rep") or "").strip()
                    estado_nombre = str(o.get("estado_nombre") or "Activo").strip()

                    archivos_item = []

                    # 5.1 Descargar PDF de la Orden (botón verde "Descargar") con reintento automático
                    orden_pdf_res = await page.evaluate(
                        """async ({ orden_id, estado_id, consecutivo }) => {
                            const root = window.$nuxt && (window.$nuxt.$root || window.$nuxt);
                            const axios = (window.$nuxt && window.$nuxt.$axios) || (root && root.$axios);
                            const payload = {
                                tipo: "servicio",
                                id: orden_id,
                                filtro: estado_id,
                                detalles: { id: consecutivo }
                            };
                            let lastErr = "";
                            for (let intento = 0; intento < 3; intento++) {
                                try {
                                    const resp = await axios.post("/pdf", payload, { responseType: "arraybuffer" });
                                    const bytes = new Uint8Array(resp.data);
                                    if (bytes.length > 100) {
                                        let binary = "";
                                        const chunkSize = 0x8000;
                                        for (let i = 0; i < bytes.length; i += chunkSize) {
                                            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
                                        }
                                        return { ok: true, base64: btoa(binary), size: bytes.length };
                                    }
                                } catch (err) {
                                    lastErr = err.message || String(err);
                                }
                                await new Promise(r => setTimeout(r, 600));
                            }
                            return { ok: false, error: lastErr || "No se pudo descargar PDF" };
                        }""",
                        {"orden_id": orden_id, "estado_id": estado_id, "consecutivo": consecutivo}
                    )

                    orden_filename = f"Orden_{consecutivo}_FOMAG_{orden_id}_CUPS_{cup_codigo}.pdf"
                    orden_filepath = os.path.join(descargas_dir, orden_filename)

                    if orden_pdf_res and orden_pdf_res.get("ok") and orden_pdf_res.get("base64"):
                        pdf_bytes = base64.b64decode(orden_pdf_res["base64"])
                        with open(orden_filepath, "wb") as f_out:
                            f_out.write(pdf_bytes)
                        info_archivo = {
                            "tipo": "ORDEN_PDF",
                            "consecutivo": consecutivo,
                            "orden_id": orden_id,
                            "cups": cup_codigo,
                            "nombre_archivo": orden_filename,
                            "ruta_local": orden_filepath,
                            "url_descarga": f"/api/descarga_fomag/{doc_num_clean}/{orden_filename}"
                        }
                        archivos_item.append(info_archivo)
                        archivos_descargados.append(info_archivo)

                    # 5.2 Consultar y descargar Adjuntos de la consulta (botón azul "Adjuntos")
                    if consulta_id not in adjuntos_por_consulta_cache:
                        adj_list_res = await page.evaluate(
                            """async ({ afiliado_id, consulta_id }) => {
                                try {
                                    const root = window.$nuxt && (window.$nuxt.$root || window.$nuxt);
                                    const axios = (window.$nuxt && window.$nuxt.$axios) || (root && root.$axios);
                                    const adjuntosFn = (window.$nuxt && window.$nuxt.$adjuntos) || (root && root.$adjuntos);
                                    const resp = await axios.post("transcripciones/buscarAdjuntos", {
                                        afiliado_id,
                                        consulta_id
                                    });
                                    const lista = Array.isArray(resp.data) ? resp.data : [];
                                    const resultados = [];
                                    for (const adj of lista) {
                                        let item = {
                                            nombre: adj.nombre || "adjunto.pdf",
                                            ruta: adj.ruta,
                                            created_at: adj.created_at,
                                            ok: false
                                        };
                                        try {
                                            if (adjuntosFn) {
                                                const r = await adjuntosFn(adj.ruta);
                                                if (r && r[1] && r[1].byteLength > 0) {
                                                    const bytes = new Uint8Array(r[1]);
                                                    let binary = "";
                                                    const chunkSize = 0x8000;
                                                    for (let i = 0; i < bytes.length; i += chunkSize) {
                                                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
                                                    }
                                                    item.ok = true;
                                                    item.mime = r[0] || "application/pdf";
                                                    item.base64 = btoa(binary);
                                                }
                                            }
                                        } catch (e1) {}

                                        if (!item.ok) {
                                            try {
                                                const r2 = await axios.post("/adjuntos/generar-url-temporal-consulta", {
                                                    disco: "digital",
                                                    path: adj.ruta,
                                                    tiempo: 10
                                                });
                                                if (r2 && r2.data) {
                                                    item.ok = true;
                                                    item.signed_url = r2.data;
                                                }
                                            } catch (e2) {
                                                item.error = e2.message || String(e2);
                                            }
                                        }
                                        resultados.push(item);
                                    }
                                    return { ok: true, adjuntos: resultados };
                                } catch (err) {
                                    return { ok: false, error: err.message || String(err), adjuntos: [] };
                                }
                            }""",
                            {"afiliado_id": afiliado.get("id"), "consulta_id": consulta_id}
                        )

                        saved_adjuntos_consulta = []
                        for adj in (adj_list_res or {}).get("adjuntos", []):
                            clean_name = self._limpiar_nombre_archivo(adj.get("nombre", "adjunto.pdf"))
                            adj_filename = f"Adjunto_Consulta_{consulta_id}_{clean_name}"
                            adj_filepath = os.path.join(descargas_dir, adj_filename)

                            if adj.get("base64"):
                                adj_bytes = base64.b64decode(adj["base64"])
                                with open(adj_filepath, "wb") as f_adj:
                                    f_adj.write(adj_bytes)
                                saved_adjuntos_consulta.append({
                                    "tipo": "ADJUNTO",
                                    "consulta_id": consulta_id,
                                    "nombre_original": clean_name,
                                    "nombre_archivo": adj_filename,
                                    "ruta_local": adj_filepath,
                                    "url_descarga": f"/api/descarga_fomag/{doc_num_clean}/{adj_filename}"
                                })
                            elif adj.get("signed_url"):
                                try:
                                    urllib.request.urlretrieve(adj["signed_url"], adj_filepath)
                                    saved_adjuntos_consulta.append({
                                        "tipo": "ADJUNTO",
                                        "consulta_id": consulta_id,
                                        "nombre_original": clean_name,
                                        "nombre_archivo": adj_filename,
                                        "ruta_local": adj_filepath,
                                        "url_descarga": f"/api/descarga_fomag/{doc_num_clean}/{adj_filename}"
                                    })
                                except Exception:
                                    pass

                        adjuntos_por_consulta_cache[consulta_id] = saved_adjuntos_consulta
                        for s_adj in saved_adjuntos_consulta:
                            archivos_descargados.append(s_adj)

                    # Asociar adjuntos de esta consulta al procedimiento actual
                    for s_adj in adjuntos_por_consulta_cache.get(consulta_id, []):
                        archivos_item.append(s_adj)

                    procedimientos.append({
                        "consecutivo": consecutivo,
                        "codigo_servicio": f"Cons. {consecutivo}",
                        "num_orden": orden_id,
                        "num_autorizacion": f"{orden_id} (Cons. {consecutivo})" if consecutivo else orden_id,
                        "codigo_autorizacion": f"{orden_id} (Cons. {consecutivo})" if consecutivo else orden_id,
                        "consulta_id": consulta_id,
                        "cups": cup_codigo,
                        "servicio": cup_nombre,
                        "descripcion": cup_nombre,
                        "cantidad": cantidad,
                        "estado": estado_nombre,
                        "prestador": prestador_nombre,
                        "fecha_orden": fecha_creacion,
                        "fecha_aprobacion": fecha_creacion,
                        "fecha_vigencia": fecha_vigencia,
                        "fecha_vencimiento": fecha_vigencia,
                        "archivos": archivos_item
                    })

                # Abrir el modal de adjuntos de la primera orden activa para que quede en la evidencia visual
                try:
                    await page.evaluate(
                        """(consecutivoTarget) => {
                            function findServComp(root) {
                                if (!root) return null;
                                if (root.$data && 'ordenesServicios' in root.$data) return root;
                                if (root.$children) {
                                    for (const c of root.$children) {
                                        const r = findServComp(c);
                                        if (r) return r;
                                    }
                                }
                                return null;
                            }
                            const serv = findServComp(window.$nuxt.$root);
                            if (serv && Array.isArray(serv.ordenesServicios)) {
                                const row = serv.ordenesServicios.find(o => String(o.consecutivo) === String(consecutivoTarget));
                                if (row && serv.abrirModalDetalle) {
                                    serv.abrirModalDetalle(row);
                                }
                            }
                        }""",
                        ordenes_neuro_activas[0].get("consecutivo")
                    )
                    await page.wait_for_timeout(1800)
                except Exception:
                    pass

                await page.screenshot(path=evidencia_path, full_page=True)

                # Poblar resumen principal con la orden activa más reciente
                primera = procedimientos[0]
                todos_cups = ", ".join([p["cups"] for p in procedimientos if p.get("cups")])
                todas_ordenes_ids = ", ".join([f"#{p['consecutivo']} (Orden {p['num_orden']})" for p in procedimientos])

                res["status"] = "AUTORIZADO"
                res["num_autorizacion"] = todas_ordenes_ids
                res["cups"] = todos_cups
                res["servicio"] = (
                    f"{len(procedimientos)} procedimiento(s): "
                    + " | ".join([f"{p['cups']} - {p['descripcion']} (Cant: {p['cantidad']})" for p in procedimientos])
                )
                res["fecha_aprobacion"] = primera.get("fecha_orden")
                res["fecha_vigencia"] = primera.get("fecha_vencimiento")
                res["prestador"] = primera.get("prestador")
                res["procedimientos"] = procedimientos
                res["archivos_descargados"] = archivos_descargados
                res["mensaje"] = (
                    f"Se encontraron {len(procedimientos)} orden(es) ACTIVA(S) para {primera.get('prestador')} "
                    f"y se descargaron {len(archivos_descargados)} archivo(s) (Órdenes PDF y Adjuntos) en "
                    f"{descargas_dir}."
                )
                return res

            except Exception as e:
                try:
                    await page.screenshot(path=evidencia_path, full_page=True)
                except Exception:
                    pass
                res["status"] = "ERROR"
                res["mensaje"] = f"Excepción durante la ejecución del bot de FOMAG: {str(e)}"
                return res
            finally:
                await browser.close()

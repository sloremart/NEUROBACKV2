from flask import Flask, render_template, jsonify, send_from_directory, request
from werkzeug.utils import secure_filename
import sqlite3
import os
import re
import json
import threading
from datetime import datetime

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

# Configuración de rutas según entorno (Docker vs Local)
if os.path.exists('/app/data'):
    DATA_DIR = '/app/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

EVIDENCIAS_DIR = os.path.join(DATA_DIR, 'evidencias')
os.makedirs(EVIDENCIAS_DIR, exist_ok=True)

DESCARGAS_FOMAG_DIR = os.path.join(DATA_DIR, 'descargas_fomag')
os.makedirs(DESCARGAS_FOMAG_DIR, exist_ok=True)

ADJUNTOS_ORDENES_DIR = os.path.join(DATA_DIR, 'adjuntos_ordenes')
os.makedirs(ADJUNTOS_ORDENES_DIR, exist_ok=True)

DESKTOP_HUB_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'RPA_BOTS_HUB', 'data')

DB_LOCAL = os.path.join(DATA_DIR, 'historico.db')

NEUROBACK_API_URL = os.getenv("NEUROBACK_API_URL", "http://192.168.1.211:8009")


def mapear_entidad_a_bot(entidad: str):
    """Detecta a cuál de los 4 bots validadores corresponde el nombre de la Entidad en ZeusSalud."""
    if not entidad:
        return None
    ent = str(entidad).upper().strip()
    if "CAPITAL SALUD" in ent or "CAPITALSALUD" in ent:
        return "capital_salud"
    if "SALUD TOTAL" in ent or "SALUDTOTAL" in ent:
        return "salud_total"
    if (
        "FOMAG" in ent
        or "MAGISTERIO" in ent
        or "FIDUPREVISORA" in ent
        or "LA PREVISORA" in ent
        or "FIDEICOMISO" in ent
        or "PRESTACIONES SOCIALES" in ent
    ):
        return "fomag"
    if "SANITAS" in ent or "COLSANITAS" in ent:
        return "colsanitas"
    return None


def cruzar_resultado_bot_con_cups(res: dict, cups_solicitado: str, fecha_orden: str = "") -> dict:
    """Cruza el resultado de un bot RPA contra un código CUPS específico (respaldo; se reemplaza por validador_hub_service)."""
    cups_req = str(cups_solicitado or "").strip()
    status_bot = res.get("status", "ERROR")
    procedimientos = res.get("procedimientos") or []
    todos_archivos = res.get("archivos_descargados") or []

    num_aut_final = res.get("num_autorizacion") or ""
    cups_final = res.get("cups") or ""
    vigencia_final = res.get("fecha_vigencia") or ""
    archivos_filtrados = list(todos_archivos)
    estado_final = status_bot
    estado_cita_sugerido = None

    if status_bot == "AUTORIZADO":
        procs_coincidentes = []
        if cups_req and procedimientos:
            for p in procedimientos:
                c_item = str(p.get("cups") or "").strip()
                if c_item and (cups_req == c_item or cups_req in c_item or c_item in cups_req):
                    procs_coincidentes.append(p)

        if procs_coincidentes:
            estado_final = "AUTORIZADO"
            estado_cita_sugerido = "PENDIENTE_CITA_AUT_EPS"
            auts_match = []
            vigs_match = []
            archivos_match = []
            seen_files = set()

            for pm in procs_coincidentes:
                aut_val = str(pm.get("num_autorizacion") or pm.get("num_orden") or pm.get("consecutivo") or "").strip()
                if aut_val and aut_val not in auts_match:
                    auts_match.append(aut_val)
                vig_val = str(pm.get("fecha_vigencia") or pm.get("fecha_vencimiento") or "").strip()
                if vig_val and vig_val not in vigs_match:
                    vigs_match.append(vig_val)
                for arch in (pm.get("archivos") or []):
                    fname = arch.get("nombre_archivo") or arch.get("url_descarga")
                    if fname and fname not in seen_files:
                        seen_files.add(fname)
                        archivos_match.append(arch)

            if auts_match:
                num_aut_final = ", ".join(auts_match)
            cups_final = cups_req
            if vigs_match:
                vigencia_final = vigs_match[0]
            if archivos_match:
                archivos_filtrados = archivos_match

        elif cups_req and cups_final:
            if cups_req in cups_final or cups_final in cups_req:
                estado_final = "AUTORIZADO"
                estado_cita_sugerido = "PENDIENTE_CITA_AUT_EPS"
            else:
                estado_final = "DISCREPANCIA_CUPS"
                estado_cita_sugerido = "PENDIENTE_AUT_EPS"
        else:
            estado_final = "AUTORIZADO"
            estado_cita_sugerido = "PENDIENTE_CITA_AUT_EPS"

    elif status_bot in ("SIN_VOLANTE", "SIN_ORDENES"):
        estado_final = "SIN_VOLANTE"
        estado_cita_sugerido = "PENDIENTE_AUT_EPS"

    return {
        "ok": True,
        "estado_final": estado_final,
        "estado_cita_sugerido": estado_cita_sugerido,
        "num_autorizacion": num_aut_final,
        "cups": cups_final,
        "fecha_vigencia": vigencia_final,
        "fecha_consulta": res.get("fecha_consulta") or "",
        "soporte_evidencia": res.get("soporte_evidencia") or "",
        "mensaje": res.get("mensaje") or "",
        "archivos": archivos_filtrados,
        "procedimientos": procedimientos,
        "resultado_raw": res
    }


try:
    # Versión estricta: mismo CUPS + autorización con fecha >= fecha de la orden en SIESA
    from validador_hub_service import cruzar_resultado_bot_con_cups  # noqa: F811
except Exception:
    pass


def _ejecutar_validacion_universal(doc_num: str, cups_solicitado: str, entidad: str, abrir_navegador: bool = False, fecha_orden: str = "") -> dict:
    """
    Intenta usar validador_hub_service / BotHub (para las 4 EPS).
    Si en el contenedor aún no se ha reconstruido la imagen con `bots/`, hace fallback seguro a validador_colsanitas.
    """
    try:
        from validador_hub_service import ejecutar_validacion_y_cruce
        return ejecutar_validacion_y_cruce(
            doc_num=doc_num,
            cups_solicitado=cups_solicitado,
            entidad=entidad,
            evidencias_dir=EVIDENCIAS_DIR,
            descargas_fomag_dir=DESCARGAS_FOMAG_DIR,
            headless=(not abrir_navegador),
            fecha_orden=fecha_orden
        )
    except ImportError:
        codigo_eps = mapear_entidad_a_bot(entidad)
        if codigo_eps == "colsanitas":
            from validador_colsanitas import validar_autorizacion_sync
            res = validar_autorizacion_sync("CC", doc_num, evidencias_dir=EVIDENCIAS_DIR, headless=(not abrir_navegador))
            cruce = cruzar_resultado_bot_con_cups(res, cups_solicitado, fecha_orden)
            cruce["eps_codigo"] = "colsanitas"
            return cruce
        return {
            "ok": False,
            "error": "Debe copiar la carpeta 'bots/', 'hub.py' y 'validador_hub_service.py' al contenedor (o ejecutar docker compose up -d --build)."
        }


def get_db_connection():
    conn = sqlite3.connect(DB_LOCAL, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db_extensions():
    """Garantiza que las columnas para validación de EPS, seguimiento de cita y archivos adjuntos existan en SQLite."""
    if not os.path.exists(DB_LOCAL):
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cols = [
                ("val_eps_estado", "TEXT DEFAULT 'PENDIENTE'"),
                ("val_eps_autorizacion", "TEXT DEFAULT ''"),
                ("val_eps_cups", "TEXT DEFAULT ''"),
                ("val_eps_vigencia", "TEXT DEFAULT ''"),
                ("val_eps_fecha", "TEXT DEFAULT ''"),
                ("val_eps_soporte", "TEXT DEFAULT ''"),
                ("val_eps_mensaje", "TEXT DEFAULT ''"),
                ("observacion_gestion", "TEXT DEFAULT ''"),
                ("fecha_observacion", "TEXT DEFAULT ''"),
                ("estado_cita", "TEXT DEFAULT ''"),
                ("fecha_cita", "TEXT DEFAULT ''"),
                ("val_eps_archivos", "TEXT DEFAULT '[]'"),
                ("val_eps_procedimientos", "TEXT DEFAULT '[]'"),
                ("nro_admision", "TEXT DEFAULT ''"),
                ("numero_orden", "TEXT DEFAULT ''"),
            ]
            for col_name, col_type in cols:
                try:
                    cursor.execute(f"ALTER TABLE ordenes_historico_v2 ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
    except Exception as e:
        print(f"Nota en migración de columnas: {e}")


init_db_extensions()


def _parse_json_list(raw_str):
    if not raw_str:
        return []
    try:
        val = json.loads(raw_str)
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _descubrir_archivos_locales_fomag(doc_num: str, cups_orden: str = "") -> list:
    doc_clean = re.sub(r"[^0-9A-Za-z]", "", str(doc_num or "").strip())
    if not doc_clean:
        return []

    candidatos_dirs = [
        os.path.join(DESCARGAS_FOMAG_DIR, doc_clean),
        os.path.join(DESKTOP_HUB_DATA, 'descargas_fomag', doc_clean)
    ]
    encontrados = []
    seen = set()

    for carpeta in candidatos_dirs:
        if os.path.isdir(carpeta):
            try:
                for fname in sorted(os.listdir(carpeta)):
                    if fname in seen or not fname.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg')):
                        continue
                    if fname.startswith("Orden_") and "_CUPS_" in fname and cups_orden:
                        if f"_CUPS_{cups_orden}" not in fname:
                            continue
                    seen.add(fname)
                    es_orden = fname.startswith("Orden_")
                    encontrados.append({
                        "tipo": "ORDEN_PDF" if es_orden else "ADJUNTO",
                        "nombre_archivo": fname,
                        "nombre_original": "Orden PDF" if es_orden else re.sub(r"^Adjunto_Consulta_\d+_", "", fname),
                        "url_descarga": f"/api/descarga_fomag/{doc_clean}/{fname}"
                    })
            except Exception:
                pass
    return encontrados


@app.route('/')
def index():
    return render_template('index.html', neuroback_api_url=NEUROBACK_API_URL)


@app.route('/api/ordenes')
def get_ordenes():
    try:
        if not os.path.exists(DB_LOCAL):
            return jsonify({"data": []})

        init_db_extensions()
        conn = get_db_connection()

        query = """
            SELECT 
                id_zeus AS Id,
                fecha_registro AS Fecha,
                medico AS Medico,
                paciente AS Paciente,
                documento AS Documento,
                telefono AS Telefono,
                entidad AS Entidad,
                cups AS Cups,
                orden AS Orden,
                observacion AS Observacion,
                COALESCE(observacion_gestion, '') AS ObservacionGestion,
                COALESCE(fecha_observacion, '') AS FechaObservacion,
                estado AS Estado,
                COALESCE(val_eps_estado, 'PENDIENTE') AS ValEpsEstado,
                COALESCE(val_eps_autorizacion, '') AS ValEpsAutorizacion,
                COALESCE(val_eps_cups, '') AS ValEpsCups,
                COALESCE(val_eps_vigencia, '') AS ValEpsVigencia,
                COALESCE(val_eps_fecha, '') AS ValEpsFecha,
                COALESCE(val_eps_soporte, '') AS ValEpsSoporte,
                COALESCE(val_eps_mensaje, '') AS ValEpsMensaje,
                COALESCE(estado_cita, '') AS EstadoCita,
                COALESCE(fecha_cita, '') AS FechaCita,
                COALESCE(val_eps_archivos, '[]') AS ValEpsArchivosRaw,
                COALESCE(val_eps_procedimientos, '[]') AS ValEpsProcedimientosRaw,
                COALESCE(nro_admision, '') AS NroAdmision,
                COALESCE(numero_orden, '') AS NroOrden
            FROM ordenes_historico_v2
            ORDER BY fecha_registro DESC
        """
        cursor = conn.execute(query)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            d = dict(row)
            archivos = _parse_json_list(d.pop("ValEpsArchivosRaw", "[]"))
            procedimientos = _parse_json_list(d.pop("ValEpsProcedimientosRaw", "[]"))

            bot_code = mapear_entidad_a_bot(d.get("Entidad"))
            if not archivos and bot_code == "fomag" and d.get("ValEpsEstado") == "AUTORIZADO" and not procedimientos and d.get("ValEpsAutorizacion") not in ("", "AUTORIZADO EPS"):
                archivos = _descubrir_archivos_locales_fomag(d.get("Documento"), d.get("Cups"))

            estado_cita = (d.get("EstadoCita") or "").strip()
            if not estado_cita and d.get("ValEpsEstado") == "AUTORIZADO":
                estado_cita = "PENDIENTE_CITA_AUT_EPS"
            d["EstadoCita"] = estado_cita
            d["ValEpsArchivos"] = archivos
            d["ValEpsProcedimientos"] = procedimientos
            d["EpsBotCodigo"] = bot_code or ""
            results.append(d)

        return jsonify({"data": results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/evidencia/<filename>')
def serve_evidencia(filename):
    if os.path.exists(os.path.join(EVIDENCIAS_DIR, filename)):
        return send_from_directory(EVIDENCIAS_DIR, filename)
    hub_ev = os.path.join(DESKTOP_HUB_DATA, 'evidencias')
    if os.path.exists(os.path.join(hub_ev, filename)):
        return send_from_directory(hub_ev, filename)
    return send_from_directory(EVIDENCIAS_DIR, filename)


@app.route('/api/descarga_fomag/<doc_num>/<filename>')
def serve_descarga_fomag(doc_num, filename):
    doc_clean = re.sub(r"[^0-9A-Za-z]", "", str(doc_num or "").strip())
    local_dir = os.path.join(DESCARGAS_FOMAG_DIR, doc_clean)
    if os.path.exists(os.path.join(local_dir, filename)):
        return send_from_directory(local_dir, filename)
    hub_dir = os.path.join(DESKTOP_HUB_DATA, 'descargas_fomag', doc_clean)
    if os.path.exists(os.path.join(hub_dir, filename)):
        return send_from_directory(hub_dir, filename)
    return send_from_directory(local_dir, filename)


@app.route('/api/adjunto_orden/<id_zeus>/<filename>')
def serve_adjunto_orden(id_zeus, filename):
    safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(id_zeus))
    carpeta = os.path.join(ADJUNTOS_ORDENES_DIR, safe_id)
    return send_from_directory(carpeta, filename)


@app.route('/api/validar_eps/<id_zeus>', methods=['POST', 'GET'])
def api_validar_eps(id_zeus):
    try:
        init_db_extensions()
        conn = get_db_connection()
        cursor = conn.execute(
            "SELECT documento, cups, entidad, estado_cita, val_eps_archivos, fecha_registro FROM ordenes_historico_v2 WHERE id_zeus = ?",
            (id_zeus,)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return jsonify({"error": "Orden no encontrada"}), 404

        doc = row["documento"]
        cups_orden = (row["cups"] or "").strip()
        entidad = (row["entidad"] or "").strip()
        estado_cita_actual = (row["estado_cita"] or "").strip()
        archivos_actuales = _parse_json_list(row["val_eps_archivos"])
        fecha_orden = row["fecha_registro"] or ""
        conn.close()

        req_json = request.get_json(silent=True) or {}
        abrir_navegador = bool(req_json.get("visual", False))

        resultado_cruce = _ejecutar_validacion_universal(
            doc_num=doc,
            cups_solicitado=cups_orden,
            entidad=entidad,
            abrir_navegador=abrir_navegador,
            fecha_orden=fecha_orden
        )

        if not resultado_cruce.get("ok"):
            return jsonify({
                "success": False,
                "error": resultado_cruce.get("error", "Entidad no soportada")
            }), 400

        estado_final = resultado_cruce["estado_final"]
        sugerido_cita = resultado_cruce.get("estado_cita_sugerido")

        nuevo_estado_cita = estado_cita_actual
        if sugerido_cita and estado_cita_actual not in ("CITA_AGENDADA",):
            nuevo_estado_cita = sugerido_cita

        archivos_manuales = [a for a in archivos_actuales if a.get("origen") == "MANUAL" or a.get("tipo") == "MANUAL"]
        archivos_nuevos = resultado_cruce.get("archivos") or []
        archivos_combinados = archivos_nuevos + archivos_manuales
        raw_bot_res = resultado_cruce.get("resultado_raw") or {}

        with get_db_connection() as conn:
            conn.execute("""
                UPDATE ordenes_historico_v2
                SET val_eps_estado = ?,
                    val_eps_autorizacion = ?,
                    val_eps_cups = ?,
                    val_eps_vigencia = ?,
                    val_eps_fecha = ?,
                    val_eps_soporte = ?,
                    val_eps_mensaje = ?,
                    estado_cita = ?,
                    val_eps_archivos = ?,
                    val_eps_procedimientos = ?
                WHERE id_zeus = ?
            """, (
                estado_final,
                resultado_cruce.get("num_autorizacion") or "",
                resultado_cruce.get("cups") or "",
                resultado_cruce.get("fecha_vigencia") or "",
                resultado_cruce.get("fecha_consulta") or "",
                resultado_cruce.get("soporte_evidencia") or "",
                resultado_cruce.get("mensaje") or "",
                nuevo_estado_cita,
                json.dumps(archivos_combinados, ensure_ascii=False),
                json.dumps(resultado_cruce.get("procedimientos") or [], ensure_ascii=False),
                id_zeus
            ))

            otras_filas = conn.execute(
                "SELECT id_zeus, cups, fecha_registro, COALESCE(estado_cita, '') AS estado_cita FROM ordenes_historico_v2 WHERE documento = ? AND id_zeus != ?",
                (doc, id_zeus)
            ).fetchall()
            for otra in otras_filas:
                cruce_otra = cruzar_resultado_bot_con_cups(raw_bot_res, otra["cups"], otra["fecha_registro"] or "")
                est_cita_otra = otra["estado_cita"]
                if cruce_otra.get("estado_cita_sugerido") and est_cita_otra not in ("CITA_AGENDADA",):
                    est_cita_otra = cruce_otra["estado_cita_sugerido"]
                conn.execute("""
                    UPDATE ordenes_historico_v2
                    SET val_eps_estado = ?,
                        val_eps_autorizacion = ?,
                        val_eps_cups = ?,
                        val_eps_vigencia = ?,
                        val_eps_fecha = ?,
                        val_eps_soporte = ?,
                        val_eps_mensaje = ?,
                        estado_cita = ?,
                        val_eps_archivos = ?,
                        val_eps_procedimientos = ?
                    WHERE id_zeus = ?
                """, (
                    cruce_otra["estado_final"],
                    cruce_otra.get("num_autorizacion") or "",
                    cruce_otra.get("cups") or "",
                    cruce_otra.get("fecha_vigencia") or "",
                    cruce_otra.get("fecha_consulta") or "",
                    cruce_otra.get("soporte_evidencia") or "",
                    cruce_otra.get("mensaje") or "",
                    est_cita_otra,
                    json.dumps(cruce_otra.get("archivos") or [], ensure_ascii=False),
                    json.dumps(cruce_otra.get("procedimientos") or [], ensure_ascii=False),
                    otra["id_zeus"]
                ))
            conn.commit()

        return jsonify({
            "success": True,
            "id_zeus": id_zeus,
            "estado": estado_final,
            "estado_cita": nuevo_estado_cita,
            "archivos": archivos_combinados,
            "detalle": raw_bot_res
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/guardar_observacion', methods=['POST'])
def api_guardar_observacion():
    try:
        init_db_extensions()
        data = request.get_json() or {}
        id_zeus = str(data.get("id_zeus", "")).strip()
        observacion = str(data.get("observacion", "")).strip()
        estado_cita = data.get("estado_cita")
        fecha_cita = data.get("fecha_cita")

        if not id_zeus:
            return jsonify({"success": False, "error": "ID de orden no proporcionado"}), 400

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            if estado_cita is not None or fecha_cita is not None:
                conn.execute("""
                    UPDATE ordenes_historico_v2
                    SET observacion_gestion = ?,
                        fecha_observacion = ?,
                        estado_cita = COALESCE(?, estado_cita),
                        fecha_cita = COALESCE(?, fecha_cita)
                    WHERE id_zeus = ?
                """, (observacion, now_str, estado_cita, fecha_cita, id_zeus))
            else:
                conn.execute("""
                    UPDATE ordenes_historico_v2
                    SET observacion_gestion = ?,
                        fecha_observacion = ?
                    WHERE id_zeus = ?
                """, (observacion, now_str, id_zeus))
            conn.commit()

        return jsonify({
            "success": True,
            "id_zeus": id_zeus,
            "observacion": observacion,
            "estado_cita": estado_cita,
            "fecha_cita": fecha_cita,
            "fecha": now_str
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/guardar_seguimiento_cita', methods=['POST'])
def api_guardar_seguimiento_cita():
    try:
        init_db_extensions()
        data = request.get_json() or {}
        id_zeus = str(data.get("id_zeus", "")).strip()
        if not id_zeus:
            return jsonify({"success": False, "error": "ID de orden no proporcionado"}), 400

        estado_cita = str(data.get("estado_cita", "")).strip()
        fecha_cita = str(data.get("fecha_cita", "")).strip()
        observacion = data.get("observacion")
        confirmar_aut = bool(data.get("confirmar_autorizacion", False))
        num_autorizacion = str(data.get("num_autorizacion", "")).strip()
        val_eps_estado_req = data.get("val_eps_estado")
        val_eps_archivos_req = data.get("val_eps_archivos")
        val_eps_mensaje_req = data.get("val_eps_mensaje")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT observacion_gestion, val_eps_estado, val_eps_autorizacion FROM ordenes_historico_v2 WHERE id_zeus = ?",
                (id_zeus,)
            )
            actual = cursor.fetchone()
            if not actual:
                return jsonify({"success": False, "error": "Orden no encontrada"}), 404

            obs_final = observacion.strip() if observacion is not None else (actual["observacion_gestion"] or "")
            val_estado_final = "AUTORIZADO" if confirmar_aut else (actual["val_eps_estado"] or "PENDIENTE")
            val_aut_final = num_autorizacion if num_autorizacion else (actual["val_eps_autorizacion"] or "")
            if val_eps_estado_req is not None:
                val_estado_final = str(val_eps_estado_req).strip() or "PENDIENTE"
                if val_estado_final != "AUTORIZADO" and not num_autorizacion:
                    val_aut_final = ""
            if isinstance(val_eps_archivos_req, list):
                conn.execute("UPDATE ordenes_historico_v2 SET val_eps_archivos = ?, val_eps_procedimientos = '[]' WHERE id_zeus = ?",
                             (json.dumps(val_eps_archivos_req, ensure_ascii=False), id_zeus))
            if val_eps_mensaje_req is not None:
                conn.execute("UPDATE ordenes_historico_v2 SET val_eps_mensaje = ? WHERE id_zeus = ?",
                             (str(val_eps_mensaje_req), id_zeus))

            conn.execute("""
                UPDATE ordenes_historico_v2
                SET estado_cita = ?,
                    fecha_cita = ?,
                    observacion_gestion = ?,
                    fecha_observacion = ?,
                    val_eps_estado = ?,
                    val_eps_autorizacion = ?
                WHERE id_zeus = ?
            """, (
                estado_cita,
                fecha_cita,
                obs_final,
                now_str,
                val_estado_final,
                val_aut_final,
                id_zeus
            ))
            conn.commit()

        return jsonify({
            "success": True,
            "id_zeus": id_zeus,
            "estado_cita": estado_cita,
            "fecha_cita": fecha_cita
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/subir_orden_adjunta/<id_zeus>', methods=['POST'])
def api_subir_orden_adjunta(id_zeus):
    try:
        init_db_extensions()
        if 'archivo' not in request.files:
            return jsonify({"success": False, "error": "No se seleccionó ningún archivo"}), 400

        file = request.files['archivo']
        if not file or not file.filename:
            return jsonify({"success": False, "error": "Archivo vacío"}), 400

        safe_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(id_zeus))
        carpeta = os.path.join(ADJUNTOS_ORDENES_DIR, safe_id)
        os.makedirs(carpeta, exist_ok=True)

        filename = secure_filename(file.filename)
        if not filename:
            filename = f"Orden_Adjunta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        filepath = os.path.join(carpeta, filename)
        file.save(filepath)

        tipo_doc = request.form.get("tipo_archivo", "ORDEN_PDF")
        marcar_aut = request.form.get("marcar_autorizado", "1") == "1"

        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT val_eps_archivos, estado_cita, val_eps_estado FROM ordenes_historico_v2 WHERE id_zeus = ?",
                (id_zeus,)
            )
            row = cursor.fetchone()
            if not row:
                return jsonify({"success": False, "error": "Orden no encontrada"}), 404

            archivos = _parse_json_list(row["val_eps_archivos"])
            nuevo_item = {
                "tipo": tipo_doc,
                "origen": "MANUAL",
                "nombre_original": file.filename,
                "nombre_archivo": filename,
                "url_descarga": f"/api/adjunto_orden/{safe_id}/{filename}"
            }
            archivos = [a for a in archivos if a.get("nombre_archivo") != filename]
            archivos.append(nuevo_item)

            estado_cita_act = (row["estado_cita"] or "").strip()
            val_estado_act = (row["val_eps_estado"] or "PENDIENTE").strip()
            if marcar_aut:
                val_estado_act = "AUTORIZADO"
                if estado_cita_act not in ("CITA_AGENDADA",):
                    estado_cita_act = "PENDIENTE_CITA_AUT_EPS"

            conn.execute("""
                UPDATE ordenes_historico_v2
                SET val_eps_archivos = ?,
                    estado_cita = ?,
                    val_eps_estado = ?
                WHERE id_zeus = ?
            """, (
                json.dumps(archivos, ensure_ascii=False),
                estado_cita_act,
                val_estado_act,
                id_zeus
            ))
            conn.commit()

        return jsonify({
            "success": True,
            "id_zeus": id_zeus,
            "archivo": nuevo_item,
            "archivos": archivos,
            "estado_cita": estado_cita_act
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


_barrido_lock = threading.Lock()


@app.route('/api/estado_tarea_programada', methods=['GET'])
def api_estado_tarea_programada():
    for rpa_url in ["http://192.168.1.244:5070/api/estado_barrido_dia", "http://localhost:5070/api/estado_barrido_dia"]:
        try:
            import urllib.request
            req = urllib.request.Request(rpa_url, headers={'User-Agent': 'DemandaWeb'})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data and isinstance(data, dict):
                    if "intervalo_minutos" not in data:
                        data["intervalo_minutos"] = int(os.getenv("INTERVALO_BARRIDO_MINUTOS", "15"))
                    return jsonify(data)
        except Exception:
            pass

    estado_file = os.path.join(DATA_DIR, 'estado_tarea_programada.json')
    info = {
        "estado": "PROGRAMADA_ACTIVA",
        "intervalo_minutos": int(os.getenv("INTERVALO_BARRIDO_MINUTOS", "15")),
        "inicio": "-",
        "fin": "-",
        "pacientes_procesados": 0,
        "ordenes_actualizadas": 0,
        "autorizados_detectados": 0,
        "ultimo_paciente": "-"
    }
    if os.path.exists(estado_file):
        try:
            with open(estado_file, "r", encoding="utf-8") as f:
                info.update(json.load(f))
        except Exception:
            pass
    return jsonify(info)


@app.route('/api/ejecutar_barrido_programado', methods=['POST'])
def api_ejecutar_barrido_programado():
    data = request.get_json(silent=True) or {}
    max_pac = int(data.get("max_pacientes", 10))
    fecha_inicio = str(data.get("fecha_inicio", "")).strip()
    fecha_fin = str(data.get("fecha_fin", fecha_inicio)).strip()
    eps_filtro = str(data.get("eps_filtro", "TODAS")).strip()

    for rpa_url in ["http://192.168.1.244:5070/api/ejecutar_barrido_dia", "http://localhost:5070/api/ejecutar_barrido_dia"]:
        try:
            import urllib.request
            payload = json.dumps({
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin or fecha_inicio,
                "eps_filtro": eps_filtro,
                "max_pacientes": max_pac,
                "solo_pendientes": True,
                "visual": False
            }).encode('utf-8')
            req = urllib.request.Request(rpa_url, data=payload, headers={'Content-Type': 'application/json', 'User-Agent': 'DemandaWeb'})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                res_data = json.loads(resp.read().decode('utf-8'))
                return jsonify(res_data)
        except Exception:
            pass

    try:
        from validador_worker import ejecutar_ciclo_barrido
    except Exception as e_mod:
        return jsonify({
            "success": False,
            "error": f"No se pudo conectar con el motor de bots RPA en Windows (192.168.1.244:5070) ni ejecutar localmente: {e_mod}. Asegúrese de que playground.py esté corriendo en la máquina con Windows."
        }), 503

    if _barrido_lock.locked():
        return jsonify({"success": False, "error": "Ya hay un barrido de validación en ejecución en este momento."}), 409

    def _run():
        with _barrido_lock:
            ejecutar_ciclo_barrido(
                max_pacientes=max_pac,
                incluir_revalidacion_sin_volante=True,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                eps_filtro=eps_filtro
            )

    threading.Thread(target=_run, daemon=True).start()
    rango_txt = f" para la fecha {fecha_inicio}" if fecha_inicio else ""
    return jsonify({"success": True, "mensaje": f"Barrido automático iniciado{rango_txt} (hasta {max_pac} pacientes)."})


def _adjuntar_pdf_local_si_existe(payload: dict) -> dict:
    """Si la orden tiene PDF adjunto o se puede obtener desde SIESA (Django), lo inyecta en el payload."""
    if payload.get("pdf_b64") or payload.get("url_pdf"):
        return payload
    id_zeus = re.sub(r"[^0-9A-Za-z_-]", "_", str(payload.get("id_zeus") or ""))
    if not id_zeus:
        return payload

    # 1. PDF adjunto manualmente (prioridad)
    carpeta = os.path.join(ADJUNTOS_ORDENES_DIR, id_zeus)
    if os.path.isdir(carpeta):
        import base64 as _b64
        archivos_pdf = [fn for fn in sorted(os.listdir(carpeta)) if fn.lower().endswith(".pdf")]
        archivos_pdf.sort(key=lambda fn: 1 if fn.startswith("Orden_") else 0)
        for fn in archivos_pdf:
            ruta = os.path.join(carpeta, fn)
            try:
                with open(ruta, "rb") as fh:
                    raw = fh.read()
                if raw[:4] == b"%PDF":
                    payload["pdf_b64"] = _b64.b64encode(raw).decode("ascii")
                    payload["pdf_nombre"] = payload.get("pdf_nombre") or fn
                    return payload
            except Exception:
                pass

    # 2. Sin PDF local: intentar URL de SIESA desde el backend Django
    try:
        id_zeus_orig = str(payload.get("id_zeus") or "")
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT nro_admision, numero_orden FROM ordenes_historico_v2 WHERE id_zeus = ?",
                (id_zeus_orig,)
            ).fetchone()
        if row and row["nro_admision"] and row["numero_orden"]:
            siesa_url = (f"{NEUROBACK_API_URL}/gedocumental/ver-pdf-orden/"
                         f"?estudio={row['nro_admision']}&numero={row['numero_orden']}")
            payload["url_pdf"] = siesa_url
            if not payload.get("pdf_nombre"):
                payload["pdf_nombre"] = f"orden_{row['numero_orden']}_siesa.pdf"
    except Exception:
        pass

    return payload


@app.route('/api/fomag/extraer_orden', methods=['POST'])
def api_fomag_extraer_orden_proxy():
    data = _adjuntar_pdf_local_si_existe(dict(request.get_json(silent=True) or {}))
    import urllib.request
    import urllib.error
    body = json.dumps(data).encode('utf-8')
    for rpa_url in ["http://192.168.1.244:5070/api/fomag/extraer_orden", "http://localhost:5070/api/fomag/extraer_orden"]:
        try:
            req = urllib.request.Request(
                rpa_url, data=body,
                headers={'Content-Type': 'application/json', 'User-Agent': 'DemandaWeb'}
            )
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                return jsonify(json.loads(resp.read().decode('utf-8')))
        except Exception:
            pass
    return jsonify({
        "ok": False,
        "error": "No se pudo conectar con el servidor RPA en Windows (192.168.1.244:5070). Verifique que playground.py esté activo."
    }), 503


@app.route('/api/fomag/radicar_transcripcion', methods=['POST'])
def api_fomag_radicar_transcripcion_proxy():
    data = _adjuntar_pdf_local_si_existe(dict(request.get_json(silent=True) or {}))
    import urllib.request
    import urllib.error
    body = json.dumps(data).encode('utf-8')
    for rpa_url in ["http://192.168.1.244:5070/api/fomag/radicar_transcripcion", "http://localhost:5070/api/fomag/radicar_transcripcion"]:
        try:
            req = urllib.request.Request(
                rpa_url, data=body,
                headers={'Content-Type': 'application/json', 'User-Agent': 'DemandaWeb'}
            )
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                return jsonify(json.loads(resp.read().decode('utf-8')))
        except urllib.error.HTTPError as he:
            try:
                err_json = json.loads(he.read().decode('utf-8'))
                return jsonify(err_json), he.code
            except Exception:
                pass
        except Exception:
            pass
    return jsonify({
        "ok": False,
        "error": "No se pudo conectar con el servidor RPA en Windows (192.168.1.244:5070). Verifique que playground.py esté activo."
    }), 503


@app.route('/deploy/<path:filepath>', methods=['GET'])
def api_deploy_file(filepath):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(base_dir, filepath)


@app.route('/deploy.sh', methods=['GET'])
def api_deploy_sh():
    host_url = request.host_url.rstrip('/')
    script = "\n".join([
        "#!/bin/bash",
        "set -e",
        "echo '[*] Descargando archivos actualizados desde " + host_url + " ...'",
        "mkdir -p templates bots",
        f"curl -fsSL '{host_url}/deploy/app.py' -o app.py",
        f"curl -fsSL '{host_url}/deploy/templates/index.html' -o templates/index.html",
        f"curl -fsSL '{host_url}/deploy/validador_hub_service.py' -o validador_hub_service.py",
        f"curl -fsSL '{host_url}/deploy/validador_worker.py' -o validador_worker.py",
        f"curl -fsSL '{host_url}/deploy/hub.py' -o hub.py",
        f"curl -fsSL '{host_url}/deploy/bots/__init__.py' -o bots/__init__.py",
        f"curl -fsSL '{host_url}/deploy/bots/bot_fomag.py' -o bots/bot_fomag.py",
        f"curl -fsSL '{host_url}/deploy/bots/bot_fomag_transcripcion.py' -o bots/bot_fomag_transcripcion.py",
        f"curl -fsSL '{host_url}/deploy/bots/bot_capital_salud.py' -o bots/bot_capital_salud.py",
        f"curl -fsSL '{host_url}/deploy/bots/bot_salud_total.py' -o bots/bot_salud_total.py",
        f"curl -fsSL '{host_url}/deploy/bots/bot_colsanitas.py' -o bots/bot_colsanitas.py",
        "echo '[*] Verificando contenedor demanda_inducida_web...'",
        "if ! docker inspect demanda_inducida_web >/dev/null 2>&1; then",
        "    docker compose up -d demanda-inducida-web || docker-compose up -d demanda-inducida-web || true",
        "fi",
        "docker start demanda_inducida_web >/dev/null 2>&1 || true",
        "docker cp app.py demanda_inducida_web:/app/app.py",
        "docker cp templates/index.html demanda_inducida_web:/app/templates/index.html",
        "docker cp validador_hub_service.py demanda_inducida_web:/app/validador_hub_service.py",
        "docker cp validador_worker.py demanda_inducida_web:/app/validador_worker.py",
        "docker cp hub.py demanda_inducida_web:/app/hub.py",
        "docker cp bots demanda_inducida_web:/app/",
        "docker restart demanda_inducida_web",
        "sleep 2",
        "docker logs --tail 15 demanda_inducida_web",
        "echo '[*] Limpiando autorizaciones EPS anteriores a la fecha de la orden...'",
        "sleep 3",
        f"curl -fsSL '{host_url}/deploy/limpiar_autorizaciones_antiguas.py' -o limpiar_autorizaciones_antiguas.py",
        "docker cp limpiar_autorizaciones_antiguas.py demanda_inducida_web:/app/limpiar_autorizaciones_antiguas.py",
        "docker exec demanda_inducida_web python /app/limpiar_autorizaciones_antiguas.py http://localhost:5000 --aplicar || true",
        "echo '[OK] Contenedor demanda_inducida_web actualizado y activo en el puerto 5050.'",
        ""
    ])
    resp = app.response_class(response=script, status=200, mimetype='text/plain; charset=utf-8')
    return resp


if __name__ == '__main__':
    puerto = int(os.getenv("PORT_DASHBOARD", "5000"))
    app.run(host='0.0.0.0', port=puerto, debug=False, threaded=True)

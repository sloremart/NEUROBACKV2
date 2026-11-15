import os
import re
import sys
import time
import json
import sqlite3
from datetime import datetime
try:
    import requests as http_requests
except ImportError:
    http_requests = None
from validador_hub_service import ejecutar_validacion_y_cruce, cruzar_resultado_bot_con_cups, mapear_entidad_a_bot, normalizar_fecha_iso

REMOTE_DEMANDA_URL = os.getenv("DEMANDA_REMOTE_URL", "http://192.168.1.211:5050")

if os.path.exists('/app/data'):
    DATA_DIR = '/app/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

EVIDENCIAS_DIR = os.path.join(DATA_DIR, 'evidencias')
os.makedirs(EVIDENCIAS_DIR, exist_ok=True)

DESCARGAS_FOMAG_DIR = os.path.join(DATA_DIR, 'descargas_fomag')
os.makedirs(DESCARGAS_FOMAG_DIR, exist_ok=True)

DB_LOCAL = os.path.join(DATA_DIR, 'historico.db')
ESTADO_TAREA_FILE = os.path.join(DATA_DIR, 'estado_tarea_programada.json')


def get_connection():
    conn = sqlite3.connect(DB_LOCAL, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_worker_columns():
    if not os.path.exists(DB_LOCAL):
        return
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            for col_name, col_type in [
                ("estado_cita", "TEXT DEFAULT ''"),
                ("fecha_cita", "TEXT DEFAULT ''"),
                ("val_eps_archivos", "TEXT DEFAULT '[]'"),
                ("val_eps_procedimientos", "TEXT DEFAULT '[]'")
            ]:
                try:
                    cursor.execute(f"ALTER TABLE ordenes_historico_v2 ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
    except Exception:
        pass


def guardar_estado_tarea(info: dict):
    try:
        actual = {}
        if os.path.exists(ESTADO_TAREA_FILE):
            with open(ESTADO_TAREA_FILE, "r", encoding="utf-8") as f:
                actual = json.load(f)
        actual.update(info)
        with open(ESTADO_TAREA_FILE, "w", encoding="utf-8") as f:
            json.dump(actual, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def ejecutar_ciclo_barrido(
    max_pacientes: int = 25,
    incluir_revalidacion_sin_volante: bool = True,
    fecha_inicio: str = "",
    fecha_fin: str = "",
    eps_filtro: str = "TODAS"
) -> dict:
    """
    Ejecuta un barrido programado de validación EPS agrupando por paciente:
    1. Prioriza órdenes con val_eps_estado = 'PENDIENTE'.
    2. Opcionalmente filtra por rango de fechas (fecha_inicio / fecha_fin) y EPS.
    """
    init_worker_columns()
    resumen = {
        "inicio": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pacientes_procesados": 0,
        "ordenes_actualizadas": 0,
        "autorizados_detectados": 0,
        "ultimo_paciente": "",
        "estado": "EN_EJECUCION"
    }
    guardar_estado_tarea(resumen)

    if not os.path.exists(DB_LOCAL):
        resumen["estado"] = "SIN_BD"
        guardar_estado_tarea(resumen)
        return resumen

    conn = get_connection()
    cursor = conn.cursor()

    f_ini = (fecha_inicio or "").strip()[:10]
    f_fin = (fecha_fin or f_ini).strip()[:10]
    eps_req = (eps_filtro or "TODAS").strip().lower()

    if f_ini:
        # Cuando el usuario selecciona un día/rango específico, validar todas las órdenes de ese día que no estén AUTORIZADO
        condicion_estado = "(val_eps_estado IS NULL OR val_eps_estado != 'AUTORIZADO')"
    else:
        condicion_estado = "(val_eps_estado IS NULL OR val_eps_estado = 'PENDIENTE' OR val_eps_estado = '')"
        if incluir_revalidacion_sin_volante:
            condicion_estado = f"""(
                {condicion_estado}
                OR (
                    val_eps_estado = 'SIN_VOLANTE'
                    AND fecha_registro >= date('now', '-15 days')
                    AND (val_eps_fecha IS NULL OR val_eps_fecha = '' OR val_eps_fecha <= datetime('now', 'localtime', '-12 hours'))
                )
            )"""

    params_sql = []
    filtro_fecha_sql = ""
    if f_ini:
        filtro_fecha_sql += " AND substr(fecha_registro, 1, 10) >= ?"
        params_sql.append(f_ini)
    if f_fin:
        filtro_fecha_sql += " AND substr(fecha_registro, 1, 10) <= ?"
        params_sql.append(f_fin)

    cursor.execute(f"""
        SELECT id_zeus, documento, cups, entidad, paciente, fecha_registro, COALESCE(estado_cita, '') AS estado_cita
        FROM ordenes_historico_v2
        WHERE {condicion_estado}
          {filtro_fecha_sql}
          AND (
              UPPER(entidad) LIKE '%SANITAS%'
              OR UPPER(entidad) LIKE '%COLSANITAS%'
              OR UPPER(entidad) LIKE '%SALUD TOTAL%'
              OR UPPER(entidad) LIKE '%CAPITAL SALUD%'
              OR UPPER(entidad) LIKE '%FOMAG%'
              OR UPPER(entidad) LIKE '%MAGISTERIO%'
              OR UPPER(entidad) LIKE '%FIDUPREVISORA%'
              OR UPPER(entidad) LIKE '%LA PREVISORA%'
              OR UPPER(entidad) LIKE '%FIDEICOMISO%'
          )
        ORDER BY
          CASE WHEN val_eps_estado IS NULL OR val_eps_estado = 'PENDIENTE' OR val_eps_estado = '' THEN 0 ELSE 1 END ASC,
          fecha_registro DESC
    """, tuple(params_sql))
    filas = cursor.fetchall()
    conn.close()

    # Agrupar por documento para consultar una sola vez por paciente en el portal EPS
    grupos_pacientes = {}
    for f in filas:
        bot_code = mapear_entidad_a_bot(f["entidad"])
        if not bot_code:
            continue
        if eps_req not in ("", "todas", "all") and bot_code != eps_req:
            continue
        doc_key = str(f["documento"] or "").strip()
        if not doc_key:
            continue
        if doc_key not in grupos_pacientes:
            grupos_pacientes[doc_key] = []
        grupos_pacientes[doc_key].append(dict(f))

    for doc_key, ordenes_pac in list(grupos_pacientes.items())[:max_pacientes]:
        primera = ordenes_pac[0]
        entidad = str(primera["entidad"] or "").strip()
        paciente = primera["paciente"]
        cups_principal = str(primera["cups"] or "").strip()

        resumen["ultimo_paciente"] = f"{paciente} ({doc_key})"
        guardar_estado_tarea(resumen)

        print(f"\n[TAREA PROGRAMADA EPS: {entidad}] Validando paciente: {paciente} (CC: {doc_key}) - {len(ordenes_pac)} orden(es)")

        resultado_primera = ejecutar_validacion_y_cruce(
            doc_num=doc_key,
            cups_solicitado=cups_principal,
            entidad=entidad,
            evidencias_dir=EVIDENCIAS_DIR,
            descargas_fomag_dir=DESCARGAS_FOMAG_DIR,
            headless=True,
            fecha_orden=primera.get("fecha_registro") or "",
            fecha_minima=min([normalizar_fecha_iso(o.get("fecha_registro")) for o in ordenes_pac if normalizar_fecha_iso(o.get("fecha_registro"))] or [""])
        )

        if not resultado_primera.get("ok"):
            continue

        raw_bot_res = resultado_primera.get("resultado_raw") or {}
        resumen["pacientes_procesados"] += 1

        # Actualizar todas las órdenes de este mismo paciente cruzando cada CUPS individual
        with get_connection() as conn:
            for ord_row in ordenes_pac:
                cruce_fila = cruzar_resultado_bot_con_cups(raw_bot_res, ord_row.get("cups"), ord_row.get("fecha_registro") or "")
                est_final = cruce_fila["estado_final"]
                sug_cita = cruce_fila.get("estado_cita_sugerido")
                est_cita_act = ord_row.get("estado_cita") or ""
                nuevo_est_cita = est_cita_act
                if sug_cita and est_cita_act not in ("CITA_AGENDADA",):
                    nuevo_est_cita = sug_cita

                if est_final == "AUTORIZADO":
                    resumen["autorizados_detectados"] += 1
                    try:
                        http_requests.post(
                            f"{REMOTE_DEMANDA_URL}/api/guardar_seguimiento_cita",
                            json={
                                "id_zeus": ord_row["id_zeus"],
                                "estado_cita": nuevo_est_cita or "PENDIENTE_CITA_AUT_EPS",
                                "fecha_cita": "",
                                "confirmar_autorizacion": True,
                                "num_autorizacion": cruce_fila.get("num_autorizacion") or "AUTORIZADO EPS"
                            },
                            timeout=8
                        )
                        for arch in (cruce_fila.get("archivos") or []):
                            ruta_local = arch.get("ruta_local")
                            fname = arch.get("nombre_archivo")
                            if ruta_local and os.path.exists(ruta_local):
                                tipo_arch = arch.get("tipo") or ("ORDEN_PDF" if str(fname).startswith("Orden_") else "ADJUNTO")
                                with open(ruta_local, "rb") as f_bin:
                                    http_requests.post(
                                        f"{REMOTE_DEMANDA_URL}/api/subir_orden_adjunta/{ord_row['id_zeus']}",
                                        files={"archivo": (fname, f_bin, "application/pdf")},
                                        data={"tipo_archivo": tipo_arch, "marcar_autorizado": "1"},
                                        timeout=15
                                    )
                    except Exception:
                        pass

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
                    est_final,
                    cruce_fila.get("num_autorizacion") or "",
                    cruce_fila.get("cups") or "",
                    cruce_fila.get("fecha_vigencia") or "",
                    cruce_fila.get("fecha_consulta") or "",
                    cruce_fila.get("soporte_evidencia") or "",
                    cruce_fila.get("mensaje") or "",
                    nuevo_est_cita,
                    json.dumps(cruce_fila.get("archivos") or [], ensure_ascii=False),
                    json.dumps(cruce_fila.get("procedimientos") or [], ensure_ascii=False),
                    ord_row["id_zeus"]
                ))
                resumen["ordenes_actualizadas"] += 1
            conn.commit()

        guardar_estado_tarea(resumen)
        time.sleep(10)

    resumen["fin"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumen["estado"] = "COMPLETADO"
    guardar_estado_tarea(resumen)
    return resumen


def procesar_cola_validaciones():
    """
    Servicio continuo programado que ejecuta barridos automáticos cada intervalo configurado
    (por defecto cada 15 minutos para nuevas órdenes y revalidación de pendientes).
    """
    intervalo_min = int(os.getenv("INTERVALO_BARRIDO_MINUTOS", "15"))
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando Tarea Programada Multi-EPS (cada {intervalo_min} min)...")

    while True:
        try:
            ejecutar_ciclo_barrido(max_pacientes=25, incluir_revalidacion_sin_volante=True)
        except Exception as e:
            print(f"[ERROR EN TAREA PROGRAMADA EPS]: {e}")
            guardar_estado_tarea({
                "estado": "ERROR",
                "ultimo_error": str(e),
                "fin": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        time.sleep(intervalo_min * 60)


if __name__ == "__main__":
    if "--una-vez" in sys.argv or "--once" in sys.argv:
        res = ejecutar_ciclo_barrido(max_pacientes=50, incluir_revalidacion_sin_volante=True)
        print(f"Barrido finalizado: {json.dumps(res, ensure_ascii=False)}")
    else:
        procesar_cola_validaciones()

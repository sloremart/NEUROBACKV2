import os
import re
import sys
import json

# Permitir importar desde RPA_BOTS_HUB si existe en el escritorio, o desde el directorio local
DESKTOP_HUB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RPA_BOTS_HUB")
if os.path.isdir(DESKTOP_HUB) and DESKTOP_HUB not in sys.path:
    sys.path.insert(0, DESKTOP_HUB)

from hub import BotHub


def mapear_entidad_a_bot(entidad: str):
    """
    Detecta a cuál de los 4 bots validadores corresponde el nombre de la Entidad en ZeusSalud.
    """
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


def normalizar_fecha_iso(valor) -> str:
    """Convierte 'YYYY-MM-DD', 'YYYY/MM/DD', 'DD/MM/YYYY' o 'DD-MM-YYYY' (con o sin hora) a 'YYYY-MM-DD'."""
    s = str(valor or "").strip()
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def fecha_procedimiento(p: dict) -> str:
    for k in ("fecha_aprobacion", "fecha_creacion", "fecha_autorizacion", "fecha_orden"):
        f = normalizar_fecha_iso(p.get(k))
        if f:
            return f
    # Capital Salud: el número de autorización inicia con AA + día juliano (ej. 26294... = 2026-10-21)
    num = re.sub(r"[^0-9]", "", str(p.get("num_autorizacion") or ""))
    if len(num) >= 13 and num.startswith("2"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime("20" + num[:5], "%Y%j").strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def cruzar_resultado_bot_con_cups(res: dict, cups_solicitado: str, fecha_orden: str = "") -> dict:
    """
    Cruza el resultado obtenido de un bot RPA contra un código CUPS específico.
    REGLA ESTRICTA: solo cuenta una autorización EPS del MISMO CUPS cuya fecha sea
    igual o posterior a la fecha en que se generó la orden en SIESA (fecha_orden).
    Autorizaciones/órdenes anteriores se ignoran y sus archivos NO se adjuntan.
    """
    cups_req = re.sub(r"[^0-9A-Za-z]", "", str(cups_solicitado or "")).upper()
    f_orden = normalizar_fecha_iso(fecha_orden)
    status_bot = res.get("status", "ERROR")
    procedimientos = res.get("procedimientos") or []

    estado_final = status_bot
    estado_cita_sugerido = None
    num_aut_final = ""
    cups_final = ""
    vigencia_final = ""
    archivos_filtrados = []
    procs_coincidentes = []

    if status_bot == "AUTORIZADO":
        for p in procedimientos:
            c_item = re.sub(r"[^0-9A-Za-z]", "", str(p.get("cups") or "")).upper()
            if cups_req and not (c_item and (cups_req == c_item or cups_req in c_item or c_item in cups_req)):
                continue
            f_p = fecha_procedimiento(p)
            if f_orden and f_p and f_p < f_orden:
                continue
            procs_coincidentes.append(p)

        if procs_coincidentes:
            estado_final = "AUTORIZADO"
            estado_cita_sugerido = "PENDIENTE_CITA_AUT_EPS"
            auts_match, vigs_match, seen_files = [], [], set()
            for pm in procs_coincidentes:
                aut_val = str(pm.get("num_autorizacion") or pm.get("codigo_autorizacion") or pm.get("num_orden") or pm.get("consecutivo") or "").strip()
                if aut_val and aut_val not in auts_match:
                    auts_match.append(aut_val)
                vig_val = str(pm.get("fecha_vigencia") or pm.get("fecha_vencimiento") or "").strip()
                if vig_val and vig_val not in vigs_match:
                    vigs_match.append(vig_val)
                for arch in (pm.get("archivos") or []):
                    fname = arch.get("nombre_archivo") or arch.get("url_descarga")
                    if fname and fname not in seen_files:
                        seen_files.add(fname)
                        archivos_filtrados.append(arch)
            num_aut_final = ", ".join(auts_match) or "AUTORIZADO EPS"
            cups_final = cups_req
            vigencia_final = vigs_match[0] if vigs_match else ""
        else:
            estado_final = "SIN_VOLANTE"
            estado_cita_sugerido = "PENDIENTE_AUT_EPS"

    elif status_bot in ("SIN_VOLANTE", "SIN_ORDENES"):
        estado_final = "SIN_VOLANTE"
        estado_cita_sugerido = "PENDIENTE_AUT_EPS"

    mensaje = res.get("mensaje") or ""
    if status_bot == "AUTORIZADO" and not procs_coincidentes:
        mensaje = f"Sin autorización EPS para CUPS {cups_req} con fecha igual o posterior a la orden ({f_orden or '-'}). Autorizaciones anteriores ignoradas."

    return {
        "ok": True,
        "estado_final": estado_final,
        "estado_cita_sugerido": estado_cita_sugerido,
        "num_autorizacion": num_aut_final,
        "cups": cups_final,
        "fecha_vigencia": vigencia_final,
        "fecha_consulta": res.get("fecha_consulta") or "",
        "soporte_evidencia": res.get("soporte_evidencia") or "",
        "mensaje": mensaje,
        "archivos": archivos_filtrados,
        "procedimientos": procs_coincidentes,
        "resultado_raw": res
    }


def ejecutar_validacion_y_cruce(
    doc_num: str,
    cups_solicitado: str,
    entidad: str,
    evidencias_dir: str,
    descargas_fomag_dir: str,
    headless: bool = True,
    doc_tipo: str = "CC",
    fecha_orden: str = "",
    fecha_minima: str = ""
) -> dict:
    """
    Ejecuta el bot correspondiente a la EPS del paciente y cruza el código CUPS solicitado.
    """
    codigo_eps = mapear_entidad_a_bot(entidad)
    doc_clean = re.sub(r"[^0-9A-Za-z]", "", str(doc_num or "").strip())
    cups_req = str(cups_solicitado or "").strip()

    if not codigo_eps:
        return {
            "ok": False,
            "error": f"La entidad '{entidad}' aún no tiene bot RPA asignado.",
            "estado_final": "NO_SOPORTADO",
            "estado_cita_sugerido": None
        }

    carpeta_paciente_fomag = os.path.join(descargas_fomag_dir, doc_clean)
    os.makedirs(carpeta_paciente_fomag, exist_ok=True)

    res = BotHub.validar(
        eps=codigo_eps,
        doc_tipo=doc_tipo or "CC",
        doc_num=doc_clean,
        headless=headless,
        evidencias_dir=evidencias_dir,
        descargas_dir=carpeta_paciente_fomag,
        cups_esperado=cups_req,
        fecha_minima=normalizar_fecha_iso(fecha_minima or fecha_orden)
    )

    cruce = cruzar_resultado_bot_con_cups(res, cups_req, fecha_orden)
    cruce["eps_codigo"] = codigo_eps
    return cruce

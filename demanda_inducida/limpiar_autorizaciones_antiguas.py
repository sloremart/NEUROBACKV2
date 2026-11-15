"""
Limpia filas marcadas como AUTORIZADO con órdenes/autorizaciones EPS ANTERIORES a la fecha
en que se generó la orden en SIESA (regla: fecha_autorizacion >= fecha_orden y mismo CUPS).

Uso:
    python limpiar_autorizaciones_antiguas.py http://192.168.1.211:5050 [--aplicar]
Sin --aplicar solo muestra lo que haría (simulación).
"""
import re
import sys
import json
import zlib
import urllib.request
import urllib.error
from datetime import datetime


def http_get_json(url: str, timeout: int = 20):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DemandaCleaner"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[!] Error GET JSON {url}: {e}")
        return None


def http_get_bytes(url: str, timeout: int = 15) -> bytes:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DemandaCleaner"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return b""


def http_post_json(url: str, payload: dict, timeout: int = 15) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "DemandaCleaner"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"   ! Error POST {url}: {e}")
        return False


def fecha_iso(v):
    s = str(v or "")
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def fecha_pdf(data: bytes) -> str:
    fechas = set(re.findall(r"20[2-3][0-9]-\d{2}-\d{2}", data.decode("latin1", errors="ignore")))
    for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", data, re.DOTALL):
        try:
            fechas.update(re.findall(r"20[2-3][0-9]-\d{2}-\d{2}", zlib.decompress(m.group(1)).decode("latin1", errors="ignore")))
        except Exception:
            pass
    return min(fechas) if fechas else ""


def fecha_aut_capital(num: str) -> str:
    n = re.sub(r"[^0-9]", "", str(num or ""))
    if len(n) >= 13 and n.startswith("2"):
        try:
            return datetime.strptime("20" + n[:5], "%Y%j").strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://192.168.1.211:5050").rstrip("/")
    aplicar = "--aplicar" in sys.argv

    data = http_get_json(f"{base}/api/ordenes", timeout=25) or {}
    filas = data.get("data") or []
    cambios = 0

    print(f"[*] Analizando {len(filas)} órdenes en {base} (Modo: {'APLICAR CAMBIOS' if aplicar else 'SIMULACIÓN'})...")

    for o in filas:
        archivos = o.get("ValEpsArchivos") or []
        estado_act = o.get("ValEpsEstado") or ""
        aut = str(o.get("ValEpsAutorizacion") or "").strip()

        # Solo evaluar filas que tengan archivos o que estén marcadas como AUTORIZADO
        if estado_act != "AUTORIZADO" and not archivos:
            continue

        id_z = o.get("Id")
        f_ord = fecha_iso(o.get("Fecha"))
        cups = re.sub(r"[^0-9A-Za-z]", "", str(o.get("Cups") or "")).upper()
        bot = o.get("EpsBotCodigo")

        if bot == "fomag":
            validos, auts, cons_validos, descartados = [], [], set(), []
            for a in archivos:
                fn = a.get("nombre_archivo") or ""
                m = re.match(r"Orden_(\d+)_FOMAG_(\d+)_CUPS_([0-9A-Za-z]+)\.pdf", fn)
                if not m:
                    continue
                f_pdf = ""
                pdf_bytes = http_get_bytes(base + a.get("url_descarga", ""), timeout=15)
                if pdf_bytes:
                    f_pdf = fecha_pdf(pdf_bytes)

                if m.group(3).upper() != cups or (f_pdf and f_pdf < f_ord) or not f_pdf:
                    descartados.append(f"{fn}:{f_pdf or 'sin fecha'}")
                    continue
                validos.append(a)
                cons_validos.add(m.group(1))
                auts.append(f"{m.group(2)} (Cons. {m.group(1)})")

            # Adjuntos de consulta asociados a una orden válida
            for a in archivos:
                fn = a.get("nombre_archivo") or ""
                if fn.startswith("Orden_"):
                    continue
                m = re.match(r"Adjunto_Consulta_(\d+)_", fn)
                if m and m.group(1) in cons_validos:
                    validos.append(a)
                elif not m:
                    validos.append(a)  # carga manual real del usuario

            # Si no hay archivos descartados y la autorización ya está limpia, no tocar
            if not descartados and aut not in ("", "AUTORIZADO EPS") and estado_act == "AUTORIZADO":
                continue

            if auts:
                num_aut_str = " / ".join(auts[:3])
                payload = {
                    "val_eps_estado": "AUTORIZADO",
                    "num_autorizacion": num_aut_str,
                    "val_eps_archivos": validos,
                    "estado_cita": o.get("EstadoCita") or "PENDIENTE_CITA_AUT_EPS",
                    "val_eps_mensaje": f"Autorización confirmada ({num_aut_str}) generada con fecha igual o posterior a la orden."
                }
                accion = f"CORREGIR -> {num_aut_str} ({len(validos)} archivos válidos, {len(descartados)} eliminados)"
            else:
                payload = {
                    "val_eps_estado": "SIN_VOLANTE",
                    "num_autorizacion": "",
                    "val_eps_archivos": [],
                    "estado_cita": "PENDIENTE_AUT_EPS" if o.get("EstadoCita") != "CITA_AGENDADA" else "CITA_AGENDADA",
                    "val_eps_mensaje": f"Sin autorización EPS con fecha igual o posterior a la orden ({f_ord}). Órdenes anteriores eliminadas."
                }
                accion = f"ELIMINAR TODO -> RESET A SIN_VOLANTE ({len(descartados)} archivos eliminados)"

        elif bot == "capital_salud" and aut == "AUTORIZADO EPS":
            payload = {
                "val_eps_estado": "PENDIENTE",
                "num_autorizacion": "",
                "val_eps_archivos": [],
                "estado_cita": "PENDIENTE_AUT_EPS" if o.get("EstadoCita") != "CITA_AGENDADA" else "CITA_AGENDADA",
                "val_eps_mensaje": "Revalidar con regla de fecha estricta."
            }
            accion = "RESETEAR A PENDIENTE (revalidar con regla estricta)"
        else:
            continue

        cambios += 1
        print(f"[{id_z}] {o.get('Paciente')} | Orden: {f_ord} | CUPS: {cups} -> {accion}")
        if aplicar:
            payload.update({"id_zeus": id_z, "fecha_cita": o.get("FechaCita") or ""})
            http_post_json(f"{base}/api/guardar_seguimiento_cita", payload, timeout=15)

    print(f"\nTotal filas {'corregidas y limpiadas' if aplicar else 'a corregir (simulación)'}: {cambios}")


if __name__ == "__main__":
    main()

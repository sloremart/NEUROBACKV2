import os
import json
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

RPA_HUB_URL     = os.getenv("RPA_HUB_URL", "http://localhost:5055")
DEMANDA_WEB_URL = os.getenv("DEMANDA_WEB_URL", "http://192.168.1.211:5050")

# Mapa: fragmentos del nombre de entidad → código bot RPA
EPS_MAPA = {
    "SANITAS":               "colsanitas",
    "COLSANITAS":            "colsanitas",
    "CAPITAL SALUD":         "capital_salud",
    "CAPITALSALUD":          "capital_salud",
    "FOMAG":                 "fomag",
    "MAGISTERIO":            "fomag",
    "FIDUPREVISORA":         "fomag",
    "LA PREVISORA":          "fomag",
    "FIDEICOMISO":           "fomag",
    "PRESTACIONES SOCIALES": "fomag",
    "SALUD TOTAL":           "salud_total",
    "SALUDTOTAL":            "salud_total",
}


def detectar_eps_bot(nombre_entidad: str) -> str | None:
    """Retorna el código de bot RPA si la entidad está soportada, o None."""
    nombre_upper = (nombre_entidad or "").upper()
    for fragmento, codigo in EPS_MAPA.items():
        if fragmento in nombre_upper:
            return codigo
    return None


@require_GET
def demanda_inducida_view(request):
    # Fechas como date (no texto): SQL Server no depende del formato de fecha de la sesión (dmy/mdy)
    try:
        fecha_desde = date.fromisoformat(request.GET.get("fecha_desde", "").strip() or "0001-01-01")
        fecha_hasta = date.fromisoformat(request.GET.get("fecha_hasta", "").strip() or "0001-01-01")
    except ValueError:
        return JsonResponse({"error": "Las fechas deben tener el formato AAAA-MM-DD."}, status=400)
    fecha_desde = None if fecha_desde == date.min else fecha_desde
    fecha_hasta = None if fecha_hasta == date.min else fecha_hasta + timedelta(days=1)

    try:
        with connections['zeussalud'].cursor() as cursor:
            if fecha_desde and fecha_hasta:
                filtro_fecha = "AND o.fecha >= %s AND o.fecha < %s"
                params = [fecha_desde, fecha_hasta]
            elif fecha_desde:
                filtro_fecha = "AND o.fecha >= %s"
                params = [fecha_desde]
            elif fecha_hasta:
                filtro_fecha = "AND o.fecha < %s"
                params = [fecha_hasta]
            else:
                filtro_fecha = "AND o.fecha >= DATEADD(day,-30,GETDATE())"
                params = []

            cursor.execute(f"""
                SELECT
                    CAST(o.numero AS VARCHAR) + '-' + CAST(i.codigo AS VARCHAR) AS IdUnico,
                    CAST(o.numero AS VARCHAR)                                    AS NroOrden,
                    CAST(m.con_estudio AS VARCHAR)                               AS NroAdmision,
                    CONVERT(varchar, o.fecha, 23) + ' ' + ISNULL(o.hora, '00:00')       AS FechaRegistro,
                    ISNULL(p.primer_nom,'') + ' ' + ISNULL(p.segundo_nom,'') + ' ' +
                        ISNULL(p.primer_ape,'') + ' ' + ISNULL(p.segundo_ape,'')        AS NombresPaciente,
                    p.num_id                                                             AS NumeroDocumento,
                    ISNULL(p.telefono, 'No registra')                                   AS TelefonosContacto,
                    e.nombre                                                             AS Entidad,
                    i.codigo                                                             AS Cups,
                    i.detalle                                                            AS DetalleOrden,
                    ISNULL(i.ObservacionItem, '')                                        AS ObservacionItem,
                    ISNULL(med.nombre, 'MÉDICO NO ASIGNADO')                             AS Medico
                FROM ordenes_detalle i
                JOIN ordenes o   ON i.numero_orden = o.numero
                JOIN sis_maes m  ON o.ingreso       = m.con_estudio
                JOIN sis_paci p  ON m.autoid         = p.autoid
                JOIN contratos c ON m.contrato       = c.codigo
                JOIN sis_empre e ON c.empresa        = e.codigo
                LEFT JOIN sis_medi med ON o.cod_med  = med.codigo
                WHERE c.codigo NOT IN (5, 6)
                  AND (
                      UPPER(e.nombre) LIKE '%%SANITAS%%'
                      OR UPPER(e.nombre) LIKE '%%COLSANITAS%%'
                      OR UPPER(e.nombre) LIKE '%%CAPITAL SALUD%%'
                      OR UPPER(e.nombre) LIKE '%%CAPITALSALUD%%'
                      OR UPPER(e.nombre) LIKE '%%FOMAG%%'
                      OR UPPER(e.nombre) LIKE '%%FIDUPREVISORA%%'
                      OR UPPER(e.nombre) LIKE '%%MAGISTERIO%%'
                      OR UPPER(e.nombre) LIKE '%%LA PREVISORA%%'
                      OR UPPER(e.nombre) LIKE '%%FIDEICOMISO%%'
                      OR UPPER(e.nombre) LIKE '%%SALUD TOTAL%%'
                      OR UPPER(e.nombre) LIKE '%%SALUDTOTAL%%'
                  )
                  {filtro_fecha}
            """, params)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

        data = []
        for row in rows:
            record = dict(zip(columns, row))
            entidad = record["Entidad"] or ""
            eps_bot = detectar_eps_bot(entidad)
            data.append({
                "Id":          record["IdUnico"],
                "NroOrden":    record["NroOrden"],
                "NroAdmision": record["NroAdmision"],
                "Fecha":       (record["FechaRegistro"] or "").strip(),
                "Paciente":    (record["NombresPaciente"] or "").strip(),
                "Documento":   record["NumeroDocumento"],
                "Telefono":    record["TelefonosContacto"],
                "Entidad":     entidad,
                "EpsBot":      eps_bot,
                "Cups":        record["Cups"],
                "Orden":       record["DetalleOrden"],
                "Observacion": record["ObservacionItem"],
                "Medico":      (record["Medico"] or "").strip(),
                "Estado":      "PENDIENTE",
                # Campos de validación RPA — se enriquecen abajo
                "ValEpsEstado":       "",
                "ValEpsAutorizacion": "",
                "ValEpsVigencia":     "",
                "ValEpsMensaje":      "",
                "ValEpsArchivos":     [],
                # Cita agendada en ZeusSalud — se enriquece abajo
                "TieneCita":  False,
                "FechaCita":  "",
            })

        # Enriquecer con resultados de autorizaciones ya consultadas por los bots
        try:
            from gedocumental.models import AutorizacionRPA
            eps_doc_set = {
                (item["EpsBot"], str(item["Documento"] or "").replace(" ", ""))
                for item in data if item["EpsBot"]
            }
            if eps_doc_set:
                eps_list = list({p[0] for p in eps_doc_set})
                doc_list = list({p[1] for p in eps_doc_set})
                auths = AutorizacionRPA.objects.filter(eps__in=eps_list, doc_num__in=doc_list)
                aut_map = {(a.eps, a.doc_num): a for a in auths}
                for item in data:
                    if not item["EpsBot"]:
                        continue
                    key = (item["EpsBot"], str(item["Documento"] or "").replace(" ", ""))
                    aut = aut_map.get(key)
                    if aut:
                        item["ValEpsEstado"]       = aut.status or ""
                        item["ValEpsAutorizacion"] = aut.num_autorizacion or ""
                        item["ValEpsVigencia"]     = aut.fecha_vigencia or ""
                        item["ValEpsMensaje"]      = aut.mensaje or ""
                        dc = aut.datos_completos or {}
                        archivos_raw = dc.get("archivos") or dc.get("archivos_descargados", [])
                        # Rutas relativas /api/descarga_fomag/ y /api/adjunto_orden/ viven en Flask
                        for arch in archivos_raw:
                            url = arch.get("url_descarga", "")
                            if url.startswith("/api/"):
                                arch["url_descarga"] = DEMANDA_WEB_URL + url
                        item["ValEpsArchivos"] = archivos_raw
        except Exception:
            pass  # Si falla el enriquecimiento, devolver datos sin validación

        # Enriquecer con citas agendadas desde ZeusSalud (mismos CUPS, fecha >= hoy)
        try:
            doc_list_citas = list({str(item["Documento"] or "").replace(" ", "") for item in data if item["Documento"]})
            if doc_list_citas:
                placeholders_c = ", ".join(["%s"] * len(doc_list_citas))
                with connections['zeussalud'].cursor() as cur_c:
                    cur_c.execute(f"""
                        SELECT
                            LTRIM(RTRIM(p.num_id))  AS doc_num,
                            cups_norm               AS cups,
                            CONVERT(varchar, MIN(c.fecha), 23) AS proxima_cita
                        FROM citas c
                        JOIN sis_paci p ON p.autoid = c.autoid
                        JOIN (
                            SELECT id_cita,
                                CASE WHEN CHARINDEX('-', LTRIM(RTRIM(CAST(id_procedimiento AS VARCHAR)))) > 0
                                     THEN LEFT(LTRIM(RTRIM(CAST(id_procedimiento AS VARCHAR))),
                                               CHARINDEX('-', LTRIM(RTRIM(CAST(id_procedimiento AS VARCHAR)))) - 1)
                                     ELSE LTRIM(RTRIM(CAST(id_procedimiento AS VARCHAR)))
                                END AS cups_norm
                            FROM citas_procedimientos
                            UNION ALL
                            SELECT IdCita AS id_cita,
                                LTRIM(RTRIM(CodProcedimiento)) AS cups_norm
                            FROM citas_procedimientos_asuntos
                        ) cp ON cp.id_cita = c.id
                        WHERE LTRIM(RTRIM(p.num_id)) IN ({placeholders_c})
                          AND ISNULL(c.estado, 'P') NOT IN ('C', 'X')
                          AND CONVERT(date, c.fecha) >= CONVERT(date, GETDATE())
                        GROUP BY LTRIM(RTRIM(p.num_id)), cups_norm
                    """, doc_list_citas)
                    citas_rows = cur_c.fetchall()
                citas_map = {
                    (str(r[0] or "").strip(), str(r[1] or "").strip()): str(r[2] or "").strip()
                    for r in citas_rows
                }
                for item in data:
                    doc  = str(item["Documento"] or "").replace(" ", "")
                    cups = str(item["Cups"] or "").strip()
                    fecha_c = citas_map.get((doc, cups))
                    if fecha_c:
                        item["TieneCita"] = True
                        item["FechaCita"] = fecha_c
        except Exception:
            pass  # Si falla la consulta de citas, devolver sin ese dato

        return JsonResponse({"data": data})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def _vigencia_expirada(fecha_vigencia_str: str) -> bool:
    """Retorna True si la fecha de vigencia ya pasó. Si no puede parsear, asume vigente."""
    if not fecha_vigencia_str:
        return False
    formatos = [
        "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y",
        "%B/%d/%Y", "%b/%d/%Y",                     # enero/14/2027
        "%d-%m-%Y", "%Y/%m/%d",
    ]
    # Normalizar nombres de meses en español a inglés para strptime
    meses_es = {
        "enero": "January", "febrero": "February", "marzo": "March",
        "abril": "April", "mayo": "May", "junio": "June",
        "julio": "July", "agosto": "August", "septiembre": "September",
        "octubre": "October", "noviembre": "November", "diciembre": "December",
    }
    fecha_str = fecha_vigencia_str.strip().lower()
    for es, en in meses_es.items():
        fecha_str = fecha_str.replace(es, en)
    for fmt in formatos:
        try:
            fecha = datetime.strptime(fecha_str, fmt)
            return fecha.date() < datetime.now().date()
        except ValueError:
            continue
    return False


@csrf_exempt
@require_POST
def consultar_autorizacion_view(request):
    """
    Proxy al RPA Hub con persistencia en BD.
    Flujo: buscar en tabla → si existe y no venció, devolver → si no, llamar RPA y guardar.
    """
    from gedocumental.models import AutorizacionRPA

    try:
        body = json.loads(request.body)
        eps      = body.get("eps", "")
        doc_tipo = body.get("doc_tipo", "CC")
        doc_num  = (body.get("doc_num", "") or "").replace(" ", "")

        if not eps or not doc_num:
            return JsonResponse({"success": False, "error": "eps y doc_num son requeridos"}, status=400)

        # 1. Buscar en base de datos
        try:
            registro = AutorizacionRPA.objects.get(eps=eps, doc_num=doc_num)
            # Solo devolver si no es ERROR y la vigencia no ha expirado
            if registro.status not in ("ERROR", "ERROR_CREDENCIALES") and \
               not _vigencia_expirada(registro.fecha_vigencia):
                datos = dict(registro.datos_completos or {})
                datos["_from_db"] = True
                archivos_db = list(datos.get("archivos") or datos.get("archivos_descargados") or [])
                for arch in archivos_db:
                    url = arch.get("url_descarga", "")
                    if url.startswith("/api/"):
                        arch["url_descarga"] = DEMANDA_WEB_URL + url
                datos["ValEpsArchivos"] = archivos_db
                return JsonResponse({"success": True, "data": datos})
            # Si venció o fue error → re-consultar (continúa abajo)
        except AutorizacionRPA.DoesNotExist:
            pass

        # 2. Llamar al RPA Hub
        payload = json.dumps({"eps": eps, "doc_tipo": doc_tipo, "doc_num": doc_num, "headless": True}).encode()
        req = urllib.request.Request(
            f"{RPA_HUB_URL}/api/validar",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            resultado = json.loads(resp.read().decode())

        # 3. Guardar en BD si el bot respondió con un status válido
        datos = resultado.get("data") or resultado
        if isinstance(datos, dict) and datos.get("status") not in (None, ""):
            AutorizacionRPA.objects.update_or_create(
                eps=eps,
                doc_num=doc_num,
                defaults={
                    "status":           datos.get("status", ""),
                    "num_autorizacion": datos.get("num_autorizacion") or "",
                    "fecha_vigencia":   datos.get("fecha_vigencia") or "",
                    "servicio":         datos.get("servicio") or "",
                    "cups":             datos.get("cups") or "",
                    "mensaje":          datos.get("mensaje") or "",
                    "datos_completos":  datos,
                    "fecha_consulta":   timezone.now(),
                },
            )

        # 4. Inyectar ValEpsArchivos con URLs absolutas en la respuesta
        datos_resp = resultado.get("data") if isinstance(resultado, dict) else resultado
        if isinstance(datos_resp, dict):
            dc = datos_resp
            archivos_raw = list(dc.get("archivos") or dc.get("archivos_descargados") or [])
            for arch in archivos_raw:
                url = arch.get("url_descarga", "")
                if url.startswith("/api/"):
                    arch["url_descarga"] = DEMANDA_WEB_URL + url
            datos_resp["ValEpsArchivos"] = archivos_raw

        return JsonResponse(resultado)

    except urllib.error.URLError as e:
        return JsonResponse({"success": False, "error": f"RPA Hub no disponible: {e.reason}"}, status=503)
    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

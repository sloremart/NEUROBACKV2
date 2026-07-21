"""
Generación de PDFs de resultados a partir del servidor de reportes SIESA.

Reemplaza el flujo anterior (Antares/Lumier) que leía XMLs de datosipsndx.

URL SIESA:
  http://192.168.1.209:8091/ZeusSalud/Reportes/CLIENTE//html/reporte_paraclinicoFormato.php
  ?formato=02&estudio={estudio}&id={id}&ImprimirImagenes=0

Endpoints:
  GET /api/v2/gedocumental/generar-pdf-siesa/?estudio=117&id=1219
      Genera y registra el PDF de un estudio individual.

  GET /api/v2/gedocumental/generar-pdfs-siesa/?fecha_inicio=2026-06-02&fecha_fin=2026-06-02
      Procesa en lote todos los estudios de imágenes de un rango de fechas.
"""

import base64
import html as html_lib
import hashlib
import os
import re
import stat
from datetime import datetime

import subprocess
import tempfile
import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connections
from rest_framework.decorators import api_view

from gedocumental.models import ArchivoFacturacion


class SinLecturaError(Exception):
    """El reporte de SIESA existe pero no tiene lectura dictada aún."""


def _destablar_lectura(html: str) -> str:
    """
    Reemplaza la tabla externa de LECTURA (única con <tr></tr> vacío inicial)
    por un <div>, y convierte la tabla interna (label + contenido) a divs.
    Esto permite que wkhtmltopdf corte el contenido entre páginas sin dejar
    un espacio vacío en la primera hoja.
    """
    outer_open_re = re.compile(
        r'<table\b[^>]+border=["\']1["\'][^>]+>\s*<tr></tr>\s*<tr>\s*'
        r'<td\b[^>]+border:solid[^>]*>',
        re.DOTALL | re.IGNORECASE,
    )
    m = outer_open_re.search(html)
    if not m:
        return html

    after_open = m.end()

    # Buscar el cierre de la tabla externa contando profundidad
    depth = 0
    pos = after_open
    outer_close_start = None
    while pos < len(html):
        next_open  = html.lower().find('<table',  pos)
        next_close = html.lower().find('</table>', pos)
        if next_open != -1 and (next_close == -1 or next_open < next_close):
            depth += 1
            pos = next_open + 1
        elif next_close != -1:
            if depth == 0:
                outer_close_start = next_close
                break
            depth -= 1
            pos = next_close + 1
        else:
            break

    if outer_close_start is None:
        return html

    outer_close_end = outer_close_start + len('</table>')

    # Extraer contenido interior (quitar el </td></tr> previo al </table> externo)
    inner = html[after_open:outer_close_start]
    inner = re.sub(r'\s*</td>\s*</tr>\s*$', '', inner.rstrip(), flags=re.IGNORECASE)

    # Convertir tabla interna (LECTURA label + párrafo) a divs
    inner = re.sub(r'<table\b[^>]*>', '<div style="width:100%;">', inner, flags=re.IGNORECASE)
    inner = re.sub(r'</table>', '</div>', inner, flags=re.IGNORECASE)
    inner = re.sub(r'<tr\b[^>]*>', '<div>', inner, flags=re.IGNORECASE)
    inner = re.sub(r'</tr>', '</div>', inner, flags=re.IGNORECASE)
    inner = re.sub(r'<td\b([^>]*)>', r'<div\1>', inner, flags=re.IGNORECASE)
    inner = re.sub(r'</td>', '</div>', inner, flags=re.IGNORECASE)

    replacement = (
        '<div style="border:1px solid #CECECE;margin:2px 0;padding:2px;">'
        + inner
        + '</div>'
    )
    return html[:m.start()] + replacement + html[outer_close_end:]

SIESA_REPORT_URL = (
    "http://192.168.1.209:8091/ZeusSalud/Reportes/CLIENTE//html/"
    "reporte_paraclinicoFormato.php"
)
SIESA_LOGIN_BASE = "http://192.168.1.209:8091/ZeusSalud/ips/App/controlador/login/"
SIESA_LOGIN_URL  = SIESA_LOGIN_BASE + "login.php"
SIESA_ROOT_URL        = "http://192.168.1.209:8091/ZeusSalud/"
SIESA_CTRL_ACCESO_URL = "http://192.168.1.209:8091/ZeusSalud/ips/ctrl_acceso_2.php"
SIESA_SEDE_ID         = 2   # SEDE 01
SIESA_SEDE_NOMBRE     = "SEDE 01"

_siesa_session_cache: requests.Session | None = None

SIESA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "http://192.168.1.209:8091/ZeusSalud/ips/iniciando.php",
    "Origin":  "http://192.168.1.209:8091",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIESA_APP_URL = "http://192.168.1.209:8091/ZeusSalud/ips/iniciando.php"


def _siesa_login() -> requests.Session:
    """Crea una sesión autenticada en ZeusSalud (tres pasos)."""
    session = requests.Session()
    session.headers.update(SIESA_HEADERS)

    usuario   = getattr(settings, "SIESA_USUARIO",    "")
    clave     = getattr(settings, "SIESA_CLAVE",      "")
    clave_md5 = hashlib.md5(clave.encode()).hexdigest()

    bd_servidor = getattr(settings, "SIESA_BD_SERVIDOR", "NEUROBACK")
    bd_nombre   = getattr(settings, "SIESA_BD_NOMBRE",   "ZeusSalud_Neuro")
    bd_usuario  = getattr(settings, "SIESA_BD_USUARIO",  "")
    bd_password = getattr(settings, "SIESA_BD_PASSWORD", "")

    # Paso 0a: visitar el root de ZeusSalud — puede establecer ASP.NET_SessionId
    session.get(SIESA_ROOT_URL, timeout=15)
    # Paso 0b: visitar iniciando.php para establecer PHPSESSID
    session.get(SIESA_APP_URL, timeout=15)

    # Paso 1: login con credenciales del usuario — responde con lista de sedes
    session.post(
        SIESA_LOGIN_URL,
        data={
            "operacion":       "Login",
            "BaseDato":        bd_nombre,
            "ServidorBD":      bd_servidor,
            "UsuarioBD":       bd_usuario,
            "PasswordBD":      bd_password,
            "NombreUsuario":   usuario,
            "PasswordUsuario": clave_md5,
        },
        timeout=60,
    )

    # Paso 2: seleccionar sede/punto de atención
    session.post(
        SIESA_LOGIN_URL,
        data={
            "operacion":           "SetSedePuntoAtencion",
            "IdPuntoAtencion":     SIESA_SEDE_ID,
            "NombrePuntoAtencion": SIESA_SEDE_NOMBRE,
            "IdSede":              SIESA_SEDE_ID,
        },
        timeout=30,
    )

    # Paso 3: ctrl_acceso_2.php — inicializa $_SESSION del IPS con datos completos de la clínica
    session.post(
        SIESA_CTRL_ACCESO_URL,
        data={
            "id_sede": "", "hostname": "SERVER", "existeCliente": "N",
            "manejaBdCentral": "N", "software_name": "ZeusSalud", "conexion": "0",
            "bd_0": bd_nombre, "servidor_0": bd_servidor,
            "usuario_0": bd_usuario, "password_0": bd_password,
            "file_bd_0": "", "file_servidor_0": "", "file_usuario_0": "", "file_password_0": "",
            "default_0": "1", "usuario": usuario, "password": clave,
        },
        timeout=30,
    )

    return session


def _get_siesa_session() -> requests.Session:
    """Devuelve la sesión activa o crea una nueva si no existe."""
    global _siesa_session_cache
    if _siesa_session_cache is None:
        _siesa_session_cache = _siesa_login()
    return _siesa_session_cache


def _fetch_pdf_siesa(estudio: int, id_admision: int) -> bytes:
    """
    Descarga el reporte de SIESA como PDF pasando la cookie de sesión
    directamente a wkhtmltopdf — idéntico al navegador con sesión activa.
    Si la sesión expira (500), re-autentica y reintenta.
    """
    def _do_fetch(session: requests.Session) -> bytes:
        url = (
            f"{SIESA_REPORT_URL}"
            f"?formato=02&estudio={estudio}&id={id_admision}&ImprimirImagenes=0"
        )
        # El contenido del reporte es PHP-estático — no requiere JS
        resp = session.get(url, timeout=30)
        # SIESA devuelve ISO-8859-1; decodificar correctamente antes de escribir UTF-8
        html = resp.content.decode(resp.encoding or 'iso-8859-1', errors='replace')
        # Si el informe no tiene lectura dictada, no generar PDF
        # Quitar etiquetas HTML, convertir entidades (&nbsp; etc.) y buscar letra real
        html_texto = html_lib.unescape(re.sub(r'<[^>]+>', ' ', html))
        if not re.search(r'ATENDIDO POR:\s{0,20}[A-Za-záéíóúÁÉÍÓÚñÑ]', html_texto, re.IGNORECASE):
            raise SinLecturaError("El informe aún no tiene lectura dictada en SIESA.")
        # Actualizar declaración charset para que wkhtmltopdf lea el archivo como UTF-8
        html = re.sub(
            r'<meta[^>]+charset=["\']?[a-zA-Z0-9_-]+["\']?[^>]*/?>',
            '<meta charset="UTF-8">',
            html, count=1, flags=re.IGNORECASE
        )

        # Convertir tablas de LECTURA a divs para permitir saltos de página
        html = _destablar_lectura(html)

        # Anclar URLs relativas al servidor SIESA
        html = html.replace(
            "</head>",
            '<base href="http://192.168.1.209:8091/ZeusSalud/">\n</head>',
            1,
        )

        # Inyectar logo de la clínica (el archivo en SIESA retorna 404)
        logo_path = "/app/siesa_logo.jpeg"
        if os.path.exists(logo_path):
            with open(logo_path, "rb") as lf:
                logo_b64 = base64.b64encode(lf.read()).decode()
            logo_tag = (
                f'<img src="data:image/jpeg;base64,{logo_b64}" '
                f'style="max-height:70px;max-width:130px;">'
            )
            html = re.sub(
                r'<img[^>]+PuntosDeAtencion[^>]*/?>',
                logo_tag,
                html,
                flags=re.IGNORECASE,
            )

        # Reemplazar nombre de sede
        html = html.replace("SEDE 01", "NEUROELECTRODIAGNOSTICO SH DEL LLANO S.A.S")

        tmp_html_path = None
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False, mode="w", encoding="utf-8"
            ) as tmp_html:
                tmp_html.write(html)
                tmp_html_path = tmp_html.name

            cmd = ["wkhtmltopdf", "--encoding", "UTF-8"]
            css_path = "/app/siesa_printable.css"
            if os.path.exists(css_path):
                cmd += ["--user-style-sheet", css_path]
            cmd += ["--quiet", tmp_html_path, tmp_path]
            result = subprocess.run(cmd, capture_output=True, timeout=90)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode(errors="replace"))
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            for p in (tmp_html_path, tmp_path):
                if p and os.path.exists(p):
                    os.remove(p)

    session = _get_siesa_session()
    try:
        return _do_fetch(session)
    except SinLecturaError:
        raise
    except Exception:
        global _siesa_session_cache
        _siesa_session_cache = None
        session = _get_siesa_session()
        return _do_fetch(session)


def _guardar_pdf(estudio: int, pdf_bytes: bytes) -> str:
    """Guarda el PDF en media y devuelve la ruta relativa."""
    carpeta = os.path.join(
        settings.MEDIA_ROOT, "gdocumental", "archivosFacturacion", str(estudio)
    )
    os.makedirs(carpeta, exist_ok=True)
    os.chmod(carpeta, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    nombre = f"{estudio}R.pdf"
    ruta_absoluta = os.path.join(carpeta, nombre)
    with open(ruta_absoluta, "wb") as f:
        f.write(pdf_bytes)

    ruta_relativa = os.path.join(
        "gdocumental", "archivosFacturacion", str(estudio), nombre
    )
    return ruta_relativa, ruta_absoluta, nombre


def _ya_existe_resultado(estudio: int) -> bool:
    """Devuelve True si ya hay un archivo RESULTADO para este estudio."""
    return ArchivoFacturacion.objects.filter(
        Admision_id=estudio, Tipo="RESULTADO"
    ).exists()


def _registrar_en_bd(estudio: int, nombre: str, ruta_relativa: str):
    """Registra el PDF generado en la tabla archivos (upsert para evitar duplicados)."""
    ArchivoFacturacion.objects.update_or_create(
        Admision_id=estudio,
        Tipo="RESULTADO",
        defaults={
            "NombreArchivo": nombre,
            "RutaArchivo": ruta_relativa,
            "NumeroAdmision": str(estudio),
            "FechaCreacionArchivo": datetime.now(),
            "FechaCreacionAntares": datetime.now(),
            "RevisionPrimera": False,
            "RevisionSegunda": False,
            "RevisionTercera": False,
            "Radicado": False,
        },
    )


def _estudios_por_fecha(fecha_inicio: str, fecha_fin: str):
    """
    Devuelve lista de (con_estudio, siesa_id) donde siesa_id = sis_deta.id,
    que es el parámetro 'id' que requiere el URL de reporte de SIESA.
    id_sede=3 → SEDE 02 (TAC, Resonancia, RX, Mamografía, PET/CT)
    id_sede=2 → SEDE 01 (Ecografías y otros procedimientos imagen)
    """
    with connections["zeussalud"].cursor() as cursor:
        cursor.execute(
            """
            SELECT sm.con_estudio, MIN(sd.id) AS siesa_id
            FROM sis_maes sm
            JOIN sis_deta sd ON sd.estudio = sm.con_estudio
            WHERE CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
              AND sm.id_sede IN (2, 3)
              AND sm.estado = 'A'
              AND LEFT(sd.cups, 2) IN ('87', '88')
            GROUP BY sm.con_estudio
            ORDER BY sm.con_estudio
            """,
            [fecha_inicio, fecha_fin],
        )
        return cursor.fetchall()


# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

@api_view(["GET"])
def generar_pdf_siesa(request):
    """
    Genera el PDF de un estudio individual desde SIESA.

    Query params:
      - estudio: número de estudio (con_estudio en Zeus)
      - id:      autoid de la admisión en Zeus
      - force:   1 para regenerar aunque ya exista (opcional)
    """
    estudio_str = request.GET.get("estudio")
    id_str = request.GET.get("id")
    force = request.GET.get("force", "0") == "1"

    if not estudio_str or not id_str:
        return JsonResponse(
            {"success": False, "detail": "Se requieren los parámetros 'estudio' e 'id'."},
            status=400,
        )

    try:
        estudio = int(estudio_str)
        id_admision = int(id_str)
    except ValueError:
        return JsonResponse(
            {"success": False, "detail": "Los parámetros deben ser numéricos."},
            status=400,
        )

    if not force and _ya_existe_resultado(estudio):
        return JsonResponse(
            {"success": True, "detail": f"El PDF del estudio {estudio} ya existe.", "regenerado": False},
            status=200,
        )

    try:
        pdf_bytes = _fetch_pdf_siesa(estudio, id_admision)
    except Exception as e:
        return JsonResponse(
            {"success": False, "detail": f"Error al obtener/generar el PDF de SIESA: {e}"},
            status=502,
        )

    ruta_relativa, ruta_absoluta, nombre = _guardar_pdf(estudio, pdf_bytes)
    _registrar_en_bd(estudio, nombre, ruta_relativa)

    return JsonResponse(
        {
            "success": True,
            "detail": f"PDF del estudio {estudio} generado correctamente.",
            "archivo": nombre,
            "ruta": ruta_relativa,
            "regenerado": force,
        },
        status=201,
    )


@api_view(["GET"])
def debug_siesa_login(request):
    """
    Diagnóstico exhaustivo: prueba el flujo de login de SIESA paso a paso
    buscando en qué URL se establece el ASP.NET_SessionId.

    GET /api/v2/gedocumental/debug-siesa/?estudio=3039&id=7506
    """
    import hashlib as _hashlib
    import re as _re

    usuario     = getattr(settings, "SIESA_USUARIO",    "NO_CONFIGURADO")
    clave       = getattr(settings, "SIESA_CLAVE",      "")
    bd_usuario  = getattr(settings, "SIESA_BD_USUARIO",  "NO_CONFIGURADO")
    bd_password = getattr(settings, "SIESA_BD_PASSWORD", "")
    bd_servidor = getattr(settings, "SIESA_BD_SERVIDOR", "NEUROBACK")
    bd_nombre   = getattr(settings, "SIESA_BD_NOMBRE",   "ZeusSalud_Neuro")
    clave_md5   = _hashlib.md5(clave.encode()).hexdigest()

    estudio  = request.GET.get("estudio", "3039")
    id_param = request.GET.get("id", "7506")
    log = []

    def _step(label, fn):
        try:
            r = fn()
            cookies_now = dict(session.cookies)
            entry = {
                "paso": label,
                "status": r.status_code,
                "cookies_despues": cookies_now,
                "set_cookie_header": r.headers.get("Set-Cookie", ""),
                "tiene_aspnet": "ASP.NET_SessionId" in cookies_now,
            }
            if label in ("root", "iniciando"):
                entry["body_inicio"] = r.text[:800]
            else:
                entry["body"] = r.text[:400]
            return entry
        except Exception as e:
            return {"paso": label, "error": str(e)}

    session = requests.Session()
    session.headers.update(SIESA_HEADERS)

    # Paso 0: Root de ZeusSalud
    log.append(_step("root", lambda: session.get(SIESA_ROOT_URL, timeout=15)))

    # Paso 1: iniciando.php
    log.append(_step("iniciando", lambda: session.get(SIESA_APP_URL, timeout=15)))

    # Buscar en el HTML de iniciando.php URLs que puedan ser ASP.NET
    iniciando_html = log[-1].get("body_inicio", "")
    urls_encontradas = list(set(_re.findall(
        r'(?:src|href|action)\s*=\s*["\']([^"\']+\.(?:aspx|axd|ashx)[^"\']*)["\']',
        iniciando_html, _re.IGNORECASE
    )))

    # Paso 2: Login
    log.append(_step("login", lambda: session.post(SIESA_LOGIN_URL, data={
        "operacion": "Login", "BaseDato": bd_nombre, "ServidorBD": bd_servidor,
        "UsuarioBD": bd_usuario, "PasswordBD": bd_password,
        "NombreUsuario": usuario, "PasswordUsuario": clave_md5,
    }, timeout=30)))

    # Paso 3: Sede
    log.append(_step("sede", lambda: session.post(SIESA_LOGIN_URL, data={
        "operacion": "SetSedePuntoAtencion",
        "IdPuntoAtencion": SIESA_SEDE_ID,
        "NombrePuntoAtencion": SIESA_SEDE_NOMBRE,
        "IdSede": SIESA_SEDE_ID,
    }, timeout=15)))

    # Paso 4: ctrl_acceso_2.php — igual que producción
    log.append(_step("ctrl_acceso", lambda: session.post(SIESA_CTRL_ACCESO_URL, data={
        "id_sede": "", "hostname": "SERVER", "existeCliente": "N",
        "manejaBdCentral": "N", "software_name": "ZeusSalud", "conexion": "0",
        "bd_0": bd_nombre, "servidor_0": bd_servidor,
        "usuario_0": bd_usuario, "password_0": bd_password,
        "file_bd_0": "", "file_servidor_0": "", "file_usuario_0": "", "file_password_0": "",
        "default_0": "1", "usuario": usuario, "password": clave,
    }, timeout=30)))

    # Paso 5: Reporte — verifica si hay datos del paciente
    cookies_finales = dict(session.cookies)
    try:
        r5 = session.get(SIESA_REPORT_URL, params={
            "formato": "02", "estudio": estudio, "id": id_param, "ImprimirImagenes": "0",
        }, timeout=30)
        html = r5.text
        datos = _re.findall(r'<td[^>]*>([^<]{5,})</td>', html)[:20]
        log.append({
            "paso": "reporte",
            "status": r5.status_code,
            "content_length": len(html),
            "tiene_datos": len(datos) > 3,
            "muestra_celdas": datos[:10],
            "body_inicio": html[:600],
        })
    except Exception as e:
        log.append({"paso": "reporte", "error": str(e)})

    return JsonResponse({
        "config": {
            "usuario": usuario,
            "bd_usuario": bd_usuario,
            "bd_servidor": bd_servidor,
            "bd_nombre": bd_nombre,
        },
        "cookies_finales": cookies_finales,
        "aspnet_encontrado": "ASP.NET_SessionId" in cookies_finales,
        "urls_aspnet_en_iniciando": urls_encontradas,
        "pasos": log,
    })


@api_view(["GET"])
def generar_pdfs_siesa_lote(request):
    """
    Procesa en lote todos los estudios de imágenes de un rango de fechas.

    Query params:
      - fecha_inicio: YYYY-MM-DD
      - fecha_fin:    YYYY-MM-DD
      - force:        1 para regenerar aunque ya existan (opcional)
    """
    fecha_inicio = request.GET.get("fecha_inicio")
    fecha_fin = request.GET.get("fecha_fin")
    force = request.GET.get("force", "0") == "1"

    if not fecha_inicio or not fecha_fin:
        return JsonResponse(
            {"success": False, "detail": "Se requieren 'fecha_inicio' y 'fecha_fin'."},
            status=400,
        )

    try:
        estudios = _estudios_por_fecha(fecha_inicio, fecha_fin)
    except Exception as e:
        return JsonResponse(
            {"success": False, "detail": f"Error al consultar Zeus: {e}"},
            status=500,
        )

    resultados = {"generados": [], "omitidos": [], "errores": []}

    for con_estudio, autoid in estudios:
        if not force and _ya_existe_resultado(con_estudio):
            resultados["omitidos"].append(con_estudio)
            continue

        try:
            pdf_bytes = _fetch_pdf_siesa(con_estudio, autoid)
            ruta_relativa, _, nombre = _guardar_pdf(con_estudio, pdf_bytes)
            _registrar_en_bd(con_estudio, nombre, ruta_relativa)
            resultados["generados"].append(con_estudio)
        except SinLecturaError:
            resultados["omitidos"].append(con_estudio)
        except Exception as e:
            resultados["errores"].append({"estudio": con_estudio, "error": str(e)})

    return JsonResponse(
        {
            "success": True,
            "total": len(estudios),
            "generados": len(resultados["generados"]),
            "omitidos": len(resultados["omitidos"]),
            "errores": len(resultados["errores"]),
            "detalle": resultados,
        },
        status=200,
    )

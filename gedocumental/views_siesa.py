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
import shutil
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


def _copiar_a_examenes(ruta_absoluta: str, nombre: str) -> None:
    """Copia el PDF al directorio del portal de resultados (/media/disco1/examenes/)."""
    destino_dir = "/media/disco1/examenes"
    os.makedirs(destino_dir, exist_ok=True)
    shutil.copy2(ruta_absoluta, os.path.join(destino_dir, nombre))


def _ya_existe_resultado(estudio: int) -> bool:
    """Devuelve True si ya hay un archivo RESULTADO para este estudio."""
    return ArchivoFacturacion.objects.filter(
        Admision_id=estudio, Tipo="RESULTADO"
    ).exists()


def _registrar_en_bd(estudio: int, nombre: str, ruta_relativa: str):
    """
    Registra el PDF generado en la tabla archivos (upsert para evitar duplicados).
    Si la admisión ya tiene más de un RESULTADO (ej. uno importado de la carpeta de exámenes
    y otro de SIESA), se actualiza el generado por SIESA ({estudio}R.pdf) o, si no hay, el primero.
    """
    campos = {
        "NombreArchivo": nombre,
        "RutaArchivo": ruta_relativa,
        "NumeroAdmision": str(estudio),
        "FechaCreacionArchivo": datetime.now(),
        "FechaCreacionAntares": datetime.now(),
        "RevisionPrimera": False,
        "RevisionSegunda": False,
        "RevisionTercera": False,
        "Radicado": False,
    }
    existentes = ArchivoFacturacion.objects.filter(Admision_id=estudio, Tipo="RESULTADO").order_by("IdArchivo")
    archivo = existentes.filter(NombreArchivo=nombre).first() or existentes.first()
    if archivo is None:
        ArchivoFacturacion.objects.create(Admision_id=estudio, Tipo="RESULTADO", **campos)
        return
    for campo, valor in campos.items():
        setattr(archivo, campo, valor)
    archivo.save()


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
              AND LEFT(sd.cups, 2) IN ('87', '88', '92')
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
    _copiar_a_examenes(ruta_absoluta, nombre)

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
            ruta_relativa, ruta_absoluta, nombre = _guardar_pdf(con_estudio, pdf_bytes)
            _registrar_en_bd(con_estudio, nombre, ruta_relativa)
            _copiar_a_examenes(ruta_absoluta, nombre)
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


# ---------------------------------------------------------------------------
# Notas de Enfermería — Administración de Medicamentos (ufuncional = 12)
# ---------------------------------------------------------------------------
#
# Flujo SIESA:
#   1. POST FormularioFastReport.php  → devuelve form HTML con parámetros
#   2. POST c_informe_view.aspx       → genera el PDF en FastReport, devuelve HTML
#   3. GET  FastReport.Export.aspx?frxprint=pdf&frxreport={hash} → PDF bytes
#
# Endpoint:
#   GET /api/v2/gedocumental/generar-pdfs-enfermeria/?fecha_inicio=YYYY-MM-DD&fecha_fin=YYYY-MM-DD
# ---------------------------------------------------------------------------

SIESA_FORMULARIO_URL  = "http://192.168.1.209:8091/ZeusSalud/ips/App/controlador/General/FormularioFastReport.php"
SIESA_REPORT_VIEW_URL = "http://192.168.1.209:8091/ZeusSalud/Reportes/FastReport/Pages/c_informe_view.aspx"
SIESA_EXPORT_BASE_URL = "http://192.168.1.209:8091/ZeusSalud/Reportes/FastReport/Pages/"


def _parse_hidden_inputs(html: str) -> dict:
    """Extrae todos los campos type=hidden de un bloque HTML de formulario."""
    fields = {}
    for m in re.finditer(r"<input[^>]+>", html, re.IGNORECASE):
        tag = m.group(0)
        if not re.search(r'type=["\']?hidden["\']?', tag, re.IGNORECASE):
            continue
        name  = re.search(r"name=[\"']([^\"']+)[\"']", tag)
        value = re.search(r"value=[\"']([^\"']*)[\"']", tag)
        if name:
            fields[name.group(1)] = value.group(1) if value else ""
    return fields


def _fetch_pdf_enfermeria(estudio: int, verificar: bool = True) -> bytes:
    """
    Descarga el PDF de nota de enfermería de SIESA para un estudio
    de administración de medicamentos (ufuncional = 12).

    Se fuerza una sesión PHP nueva por cada estudio para evitar que SIESA
    use el estado de la sesión anterior y devuelva el PDF de otro paciente.
    """
    global _siesa_session_cache
    _siesa_session_cache = None          # sesión fresca → PHP session limpia en SIESA
    session = _get_siesa_session()
    usuario_session = getattr(settings, "SIESA_USUARIO_SESSION", "")

    def _do_fetch_enfermeria(s: requests.Session) -> bytes:
        import time

        # Paso 1: obtener parámetros del formulario FastReport
        r1 = s.post(
            SIESA_FORMULARIO_URL,
            data={
                "estudio":              estudio,
                "ImpHC":                0,
                "ImpNotasEnfermeria":   1,
                "UsuarioSession":       usuario_session,
                "pdf":                  1,
                "generarNombreArchivo": 1,
                "reportName":           "HistoriaClinicaGeneral",
            },
            timeout=30,
        )
        r1.raise_for_status()
        payload_html = r1.json().get("payload", "")
        form_fields  = _parse_hidden_inputs(payload_html)

        if not form_fields:
            raise Exception("No se obtuvieron campos del formulario FastReport")

        nombre_pdf = form_fields.get("nombreArchivoPdf")
        if not nombre_pdf:
            raise Exception("SIESA no devolvió el nombre del archivo PDF — el estudio puede no tener nota de enfermería")

        # Paso 2: POST a c_informe_view.aspx — genera el informe en el visor de FastReport
        r2 = s.post(SIESA_REPORT_VIEW_URL, data=form_fields, timeout=60)
        r2.raise_for_status()

        # Paso 3a: exportar a PDF desde el visor (el informe recién generado de ESTE estudio).
        # SIESA ya no siempre escribe el PDF en Temp: ahí queda el último que se imprimió con
        # ese usuario (puede ser otro paciente).
        pdf_visor = _exportar_pdf_visor(s, r2.text, form_fields)
        if pdf_visor and (not verificar or _pdf_corresponde_al_estudio(pdf_visor, estudio) is not False):
            return pdf_visor
        # El exportador PDF de SIESA puede devolver archivos vacíos: se convierte el HTML del
        # visor (que sí trae la nota de este estudio) a PDF con wkhtmltopdf
        try:
            pdf_html = _pdf_desde_html_visor(s, r2.text, form_fields)
        except Exception:
            pdf_html = None
        if pdf_html and (not verificar or _pdf_corresponde_al_estudio(pdf_html, estudio) is not False):
            return pdf_html
        for url in _urls_exportacion_pdf(r2.text):
            try:
                r_exp = s.get(url, timeout=60)
            except requests.RequestException:
                continue
            if r_exp.status_code != 200 or not r_exp.content.startswith(b"%PDF"):
                continue
            if not verificar or _pdf_corresponde_al_estudio(r_exp.content, estudio) is not False:
                return r_exp.content

        # Paso 3b: descargar el PDF desde la carpeta Temp de SIESA.
        # c_informe_view.aspx escribe el PDF en ZeusSalud/Archivos/CLIENTE/Temp/ y puede tardar:
        # si se descarga antes de que termine, queda el PDF del estudio ANTERIOR (otro paciente).
        # Por eso se verifica que el PDF sea del estudio pedido y se reintenta.
        pdf_url = f"http://192.168.1.209:8091/ZeusSalud/Archivos/CLIENTE/Temp/{nombre_pdf}"
        ultimo_error = ""
        for intento in range(4):
            time.sleep(5 if intento == 0 else 4)
            r3 = s.get(pdf_url, timeout=30)
            r3.raise_for_status()
            if not r3.content.startswith(b"%PDF"):
                ultimo_error = (f"El archivo descargado no es un PDF. URL: {pdf_url}. "
                                f"Content-Type: {r3.headers.get('Content-Type', '?')}. Inicio: {r3.content[:200]}")
                continue
            if not verificar:
                return r3.content
            corresponde = _pdf_corresponde_al_estudio(r3.content, estudio)
            if corresponde is not False:      # True, o None si no se puede verificar (sin texto)
                return r3.content
            ultimo_error = (f"El PDF descargado de SIESA no corresponde al estudio {estudio} "
                            f"(trae otro paciente; posible PDF del estudio anterior)")
        raise Exception(ultimo_error)

    try:
        return _do_fetch_enfermeria(session)
    except Exception:
        # (_siesa_session_cache ya está declarado global al inicio de la función)
        _siesa_session_cache = None
        session = _get_siesa_session()
        return _do_fetch_enfermeria(session)


def _campos_formulario_visor(html: str):
    """
    Campos del formulario del visor FastReport (WebForms) para hacer el postback de exportación
    y el nombre del botón de exportar. Devuelve (campos, nombre_select_export, nombre_boton).
    """
    def attr(tag, nombre):
        m = re.search(rf"""\b{nombre}\s*=\s*["']([^"']*)["']""", tag, re.IGNORECASE)
        return m.group(1) if m else None

    campos = {}
    for m in re.finditer(r"<input\b[^>]*>", html, re.IGNORECASE):
        tag = m.group(0)
        tipo = (attr(tag, "type") or "text").lower()
        nombre = attr(tag, "name")
        if nombre and tipo in ("hidden", "text"):
            campos[nombre] = (attr(tag, "value") or "").replace("&amp;", "&")

    select_export, boton = None, None
    for m in re.finditer(r"<select\b[^>]*>(.*?)</select>", html, re.IGNORECASE | re.DOTALL):
        tag_select = m.group(0)[:m.group(0).find(">") + 1]
        nombre = attr(tag_select, "name")
        if not nombre:
            continue
        opciones = re.findall(r"""<option\b[^>]*value=["']([^"']*)["']""", m.group(1), re.IGNORECASE)
        elegida = re.search(r"""<option\b[^>]*selected[^>]*value=["']([^"']*)["']""", m.group(1), re.IGNORECASE)
        campos[nombre] = elegida.group(1) if elegida else (opciones[0] if opciones else "")
        if select_export is None and "pdf" in [o.lower() for o in opciones]:
            select_export = nombre
            # El botón de exportar es el primer botón-imagen después del menú de formatos
            despues = html[m.end():]
            for mb in re.finditer(r"<input\b[^>]*>", despues, re.IGNORECASE):
                if (attr(mb.group(0), "type") or "").lower() in ("image", "submit") and attr(mb.group(0), "name"):
                    boton = attr(mb.group(0), "name")
                    break
    return campos, select_export, boton


def _exportar_pdf_visor(s: requests.Session, html: str, campos_informe: dict = None):
    """
    Exporta a PDF el informe que muestra el visor FastReport de SIESA haciendo el mismo postback
    que el botón "Exportar" con "Adobe Acrobat" elegido. Devuelve los bytes del PDF o None.
    """
    from urllib.parse import urljoin
    campos, select_export, boton = _campos_formulario_visor(html)
    if not select_export or not boton:
        return None
    m = re.search(r"""<form\b[^>]*action=["']([^"']*)["']""", html, re.IGNORECASE)
    accion = urljoin(SIESA_REPORT_VIEW_URL, m.group(1).replace("&amp;", "&")) if m else SIESA_REPORT_VIEW_URL
    # La página del visor vuelve a leer en cada postback los parámetros del informe (estudio,
    # rptName, conexión...): sin ellos SIESA responde 500 "Referencia a objeto no establecida"
    datos = dict(campos_informe or {})
    datos.update(campos)
    datos[select_export] = "pdf"
    datos["__EVENTTARGET"] = ""
    datos["__EVENTARGUMENT"] = ""
    datos[f"{boton}.x"] = "8"
    datos[f"{boton}.y"] = "8"
    try:
        # Sin seguir la redirección: la exportación puede borrarse tras la primera descarga
        r = s.post(accion, data=datos, timeout=90, headers={"Referer": accion}, allow_redirects=False)
    except requests.RequestException:
        return None
    if r.content.startswith(b"%PDF"):
        return r.content
    # SIESA redirige a FastReport.Export.aspx?ID=...: se prueba esa URL y la misma en Pages/
    # (donde responde el manejador de exportación del visor), en descarga y en línea
    for url in _urls_descarga_exportacion(r):
        try:
            r_pdf = s.get(url, timeout=60, headers={"Referer": accion})
        except requests.RequestException:
            continue
        if r_pdf.content.startswith(b"%PDF"):
            return r_pdf.content
    # Algunas versiones responden con la página y un enlace/script hacia FastReport.Export.aspx
    texto = r.text.replace("&amp;", "&")
    for mu in re.finditer(r"""(FastReport\.Export\.aspx\?[^"'\s<>]*frxexport[^"'\s<>]*)""", texto, re.IGNORECASE):
        try:
            r_pdf = s.get(urljoin(accion, mu.group(1)), timeout=60)
        except requests.RequestException:
            continue
        if r_pdf.content.startswith(b"%PDF"):
            return r_pdf.content
    return None


def _quitar_tabla_que_contiene(html: str, pos: int) -> str:
    """Quita la <table> más interna que contiene la posición dada (p. ej. la barra del visor)."""
    bajo = html.lower()
    inicio = bajo.rfind("<table", 0, pos)
    if inicio == -1:
        return html
    profundidad, i = 0, inicio + 1
    while True:
        abre = bajo.find("<table", i)
        cierra = bajo.find("</table>", i)
        if cierra == -1:
            return html
        if abre != -1 and abre < cierra:
            profundidad += 1
            i = abre + 1
        elif profundidad:
            profundidad -= 1
            i = cierra + 1
        else:
            return html[:inicio] + html[cierra + len("</table>"):]


def _pagina_visor_html(s: requests.Session, html: str) -> tuple:
    """(estilos del <head>, cuerpo de la página sin barra ni scripts, con imágenes incrustadas)."""
    from urllib.parse import urljoin
    cabeza = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.IGNORECASE | re.DOTALL)
    estilos = "".join(re.findall(r"<style\b.*?</style>", cabeza.group(1) if cabeza else "",
                                 re.IGNORECASE | re.DOTALL))
    cuerpo_m = re.search(r"<body\b[^>]*>(.*)</body>", html, re.IGNORECASE | re.DOTALL)
    cuerpo = cuerpo_m.group(1) if cuerpo_m else html
    # Quitar la barra de herramientas (tabla que contiene el menú Export)
    m = re.search(r"<select\b[^>]*title=[\"']Export", cuerpo, re.IGNORECASE)
    if m:
        cuerpo = _quitar_tabla_que_contiene(cuerpo, m.start())
    cuerpo = re.sub(r"<script\b.*?</script>", "", cuerpo, flags=re.IGNORECASE | re.DOTALL)
    cuerpo = re.sub(r"<input\b[^>]*>|<select\b.*?</select>", "", cuerpo, flags=re.IGNORECASE | re.DOTALL)
    cuerpo = re.sub(r"</?form\b[^>]*>", "", cuerpo, flags=re.IGNORECASE)

    # Incrustar las imágenes del informe (requieren la sesión de SIESA)
    def incrustar(mi):
        src = mi.group(2).replace("&amp;", "&")
        if src.startswith("data:"):
            return mi.group(0)
        try:
            ri = s.get(urljoin(SIESA_REPORT_VIEW_URL, src), timeout=30)
            if ri.status_code != 200 or not ri.content:
                return mi.group(0)
            tipo = (ri.headers.get("Content-Type") or "image/png").split(";")[0]
            return f'{mi.group(1)}data:{tipo};base64,{base64.b64encode(ri.content).decode()}{mi.group(3)}'
        except requests.RequestException:
            return mi.group(0)
    cuerpo = re.sub(r"""(<img\b[^>]*\bsrc=["'])([^"']+)(["'])""", incrustar, cuerpo, flags=re.IGNORECASE)
    return estilos, cuerpo


def _pdf_desde_html_visor(s: requests.Session, html: str, campos_informe: dict = None):
    """
    Convierte a PDF (wkhtmltopdf) el informe que muestra el visor FastReport, recorriendo sus
    páginas con el mismo postback del campo "página". Devuelve bytes del PDF o None.
    """
    texto = re.sub(r"<[^>]+>", " ", html)
    m_total = re.search(r"\bof\s+(\d+)\b", html_lib.unescape(texto))
    total = min(int(m_total.group(1)), 30) if m_total else 1

    estilos, cuerpo = _pagina_visor_html(s, html)
    cuerpos = [cuerpo]
    actual = html
    for pagina in range(2, total + 1):
        campos, _, _ = _campos_formulario_visor(actual)
        campo_pagina = next((k for k, v in campos.items()
                             if not k.startswith("__") and v.strip() == str(pagina - 1)), None)
        if not campo_pagina:
            break
        datos = dict(campos_informe or {})
        datos.update(campos)
        datos[campo_pagina] = str(pagina)
        datos["__EVENTTARGET"] = campo_pagina
        datos["__EVENTARGUMENT"] = ""
        r = s.post(SIESA_REPORT_VIEW_URL, data=datos, timeout=60, headers={"Referer": SIESA_REPORT_VIEW_URL})
        if r.status_code != 200:
            break
        actual = r.text
        cuerpos.append(_pagina_visor_html(s, actual)[1])

    documento = (
        '<html><head><meta charset="UTF-8">' + estilos + "</head><body>"
        + '<div style="page-break-after:always"></div>'.join(cuerpos)
        + "</body></html>"
    )
    tmp_html_path = tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as th:
            th.write(documento)
            tmp_html_path = th.name
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tp:
            tmp_path = tp.name
        res = subprocess.run(["wkhtmltopdf", "--encoding", "UTF-8", "--quiet", tmp_html_path, tmp_path],
                             capture_output=True, timeout=90)
        # wkhtmltopdf puede terminar con código 1 por un recurso que no cargó y aun así generar el PDF
        with open(tmp_path, "rb") as f:
            datos_pdf = f.read()
        return datos_pdf if datos_pdf.startswith(b"%PDF") else None
    finally:
        for p in (tmp_html_path, tmp_path):
            if p and os.path.exists(p):
                os.remove(p)


def _urls_descarga_exportacion(r) -> list:
    """URLs para descargar la exportación a la que redirige el postback del visor (…Export.aspx?ID=…)."""
    destinos = [h.headers.get("Location") or "" for h in r.history] + [r.headers.get("Location") or "", r.url]
    urls = []
    for destino in destinos:
        m = re.search(r"FastReport\.Export\.aspx\?(?:[^#]*&)?ID=([\w-]+)", destino, re.IGNORECASE)
        if not m:
            continue
        rid = m.group(1)
        for base in ("http://192.168.1.209:8091/ZeusSalud/Reportes/FastReport/Pages/",
                     "http://192.168.1.209:8091/ZeusSalud/Reportes/FastReport/"):
            for inline in ("False", "True"):
                u = f"{base}FastReport.Export.aspx?ID={rid}&displayinline={inline}"
                if u not in urls:
                    urls.append(u)
    return urls


def _urls_exportacion_pdf(html: str) -> list:
    """
    URLs candidatas para exportar a PDF el informe que muestra el visor de FastReport
    (c_informe_view.aspx). Se buscan en el HTML los enlaces de exportación y el id del informe.
    """
    from urllib.parse import urljoin
    html = html.replace("&amp;", "&")
    urls = []

    def agregar(u):
        u = urljoin(SIESA_REPORT_VIEW_URL, u)
        if u not in urls:
            urls.append(u)

    # Enlaces explícitos de exportación a PDF
    for m in re.finditer(r"""["']([^"'\s<>]*Export[^"'\s<>]*)["']""", html, re.IGNORECASE):
        u = m.group(1)
        if re.search(r"pdf", u, re.IGNORECASE):
            agregar(u)
    # Id del informe (frxreport / previewobject) → URL de exportación a PDF
    ids = []
    for patron in (r"frxreport=([\w-]+)", r"previewobject=([\w-]+)", r"ExportPDF=([\w-]+)"):
        for m in re.finditer(patron, html, re.IGNORECASE):
            if m.group(1) not in ids:
                ids.append(m.group(1))
    for rid in ids:
        agregar(f"{SIESA_EXPORT_BASE_URL}FastReport.Export.aspx?frxprint=pdf&frxreport={rid}")
        agregar(f"{SIESA_EXPORT_BASE_URL}FastReport.Export.aspx?frxexport=pdf&frxreport={rid}")
        agregar(f"{SIESA_EXPORT_BASE_URL}FastReport.Export.axd?ExportPDF={rid}")
        agregar(f"{SIESA_EXPORT_BASE_URL}FastReport.Export.axd?previewobject={rid}&export_pdf=1")
    return urls


def _pdf_corresponde_al_estudio(pdf_bytes: bytes, estudio: int):
    """
    True si el texto del PDF contiene el número de estudio o el documento del paciente de ese
    estudio (según ZeusSalud); False si tiene texto pero no los contiene (es de otro paciente);
    None si no se puede verificar (PDF sin texto o error de lectura).
    """
    import io
    import re
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            texto = " ".join((p.extract_text() or "") for p in pdf.pages[:2])
    except Exception:
        return None
    if len(texto.strip()) < 30:
        return None
    numeros = set(re.findall(r"\d+", re.sub(r"[.,](?=\d{3})", "", texto)))
    if str(estudio) in numeros:
        return True
    try:
        with connections["zeussalud"].cursor() as cursor:
            cursor.execute(
                "SELECT LTRIM(RTRIM(sp.num_id)) FROM sis_maes sm JOIN sis_paci sp ON sp.autoid = sm.autoid "
                "WHERE sm.con_estudio = %s", [estudio])
            fila = cursor.fetchone()
    except Exception:
        return None
    num_id = re.sub(r"\D", "", (fila[0] if fila else "") or "")
    if num_id and num_id in numeros:
        return True
    return False


def _estudios_enfermeria_por_fecha(fecha_inicio: str, fecha_fin: str) -> list:
    """
    Devuelve lista de con_estudio para estudios de administración de
    medicamentos (ufuncional = 12) en el rango de fechas dado.
    Solo los que tienen nota de enfermería: sin nota, SIESA no genera un PDF nuevo y en su
    carpeta Temp queda el último generado (de otro paciente).
    """
    with connections["zeussalud"].cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT sm.con_estudio
            FROM sis_maes sm
            JOIN sis_deta sd ON sd.estudio = sm.con_estudio
            WHERE CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
              AND sm.id_sede = 2
              AND sm.estado = 'A'
              AND sm.ufuncional = 12
              AND EXISTS (SELECT 1 FROM notas_enfermeria ne WHERE ne.ingreso = sm.con_estudio)
            ORDER BY sm.con_estudio
            """,
            [fecha_inicio, fecha_fin],
        )
        return [row[0] for row in cursor.fetchall()]


@api_view(["GET"])
def generar_pdf_enfermeria(request):
    """
    Genera o regenera el PDF de nota de enfermería para UN estudio puntual.

    GET /api/v2/gedocumental/generar-pdf-enfermeria/?estudio=11012
    GET /api/v2/gedocumental/generar-pdf-enfermeria/?estudio=11012&force=1
    """
    estudio_str = request.GET.get("estudio", "").strip()
    force       = request.GET.get("force", "0") == "1"

    if not estudio_str:
        return JsonResponse({"success": False, "detail": "Falta el parámetro 'estudio'."}, status=400)

    try:
        estudio = int(estudio_str)
    except ValueError:
        return JsonResponse({"success": False, "detail": "El parámetro 'estudio' debe ser numérico."}, status=400)

    if not force and _ya_existe_resultado(estudio):
        return JsonResponse(
            {"success": True, "detail": f"El PDF del estudio {estudio} ya existe.", "regenerado": False},
            status=200,
        )

    try:
        pdf_bytes = _fetch_pdf_enfermeria(estudio)
    except Exception as e:
        return JsonResponse(
            {"success": False, "detail": f"Error al obtener el PDF de SIESA: {e}"},
            status=502,
        )

    ruta_relativa, _, nombre = _guardar_pdf(estudio, pdf_bytes)
    _registrar_en_bd(estudio, nombre, ruta_relativa)

    return JsonResponse(
        {
            "success": True,
            "detail":     f"PDF del estudio {estudio} generado correctamente.",
            "archivo":    nombre,
            "ruta":       ruta_relativa,
            "regenerado": force,
        },
        status=201,
    )


@api_view(["GET"])
def generar_pdfs_enfermeria_lote(request):
    """
    Procesa en lote las notas de enfermería de administración de medicamentos.

    Query params:
      - fecha_inicio: YYYY-MM-DD
      - fecha_fin:    YYYY-MM-DD
      - force:        1 para regenerar aunque ya existan (opcional)
    """
    fecha_inicio = request.GET.get("fecha_inicio")
    fecha_fin    = request.GET.get("fecha_fin")
    force        = request.GET.get("force", "0") == "1"

    if not fecha_inicio or not fecha_fin:
        return JsonResponse(
            {"success": False, "detail": "Se requieren 'fecha_inicio' y 'fecha_fin'."},
            status=400,
        )

    try:
        estudios = _estudios_enfermeria_por_fecha(fecha_inicio, fecha_fin)
    except Exception as e:
        return JsonResponse(
            {"success": False, "detail": f"Error al consultar Zeus: {e}"},
            status=500,
        )

    resultados = {"generados": [], "omitidos": [], "errores": []}

    for con_estudio in estudios:
        if not force and _ya_existe_resultado(con_estudio):
            resultados["omitidos"].append(con_estudio)
            continue
        try:
            pdf_bytes     = _fetch_pdf_enfermeria(con_estudio)
            ruta_relativa, _, nombre = _guardar_pdf(con_estudio, pdf_bytes)
            _registrar_en_bd(con_estudio, nombre, ruta_relativa)
            resultados["generados"].append(con_estudio)
        except Exception as e:
            resultados["errores"].append({"estudio": con_estudio, "error": str(e)})

    return JsonResponse(
        {
            "success": True,
            "total":     len(estudios),
            "generados": len(resultados["generados"]),
            "omitidos":  len(resultados["omitidos"]),
            "errores":   len(resultados["errores"]),
            "detalle":   resultados,
        },
        status=200,
    )

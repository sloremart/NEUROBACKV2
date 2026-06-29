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

import hashlib
import os
import stat
from datetime import datetime

import pdfkit
import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connections
from rest_framework.decorators import api_view

from gedocumental.models import ArchivoFacturacion

SIESA_REPORT_URL = (
    "http://192.168.1.209:8091/ZeusSalud/Reportes/CLIENTE//html/"
    "reporte_paraclinicoFormato.php"
)
SIESA_LOGIN_BASE = "http://192.168.1.209:8091/ZeusSalud/ips/App/controlador/login/"
SIESA_LOGIN_URL  = SIESA_LOGIN_BASE + "login.php"
SIESA_CONFIG_URL = SIESA_LOGIN_BASE + "configuracionInicial.php"
SIESA_SEDE_ID    = 2   # SEDE 01
SIESA_SEDE_NOMBRE = "SEDE 01"

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

    # Paso 0: visitar la app principal para establecer ASP.NET_SessionId
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

    # Paso 3: recargar iniciando.php con sesión autenticada para cargar config del IPS
    session.get(SIESA_APP_URL, timeout=15)

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
        phpsessid = session.cookies.get("PHPSESSID", "")
        url = (
            f"{SIESA_REPORT_URL}"
            f"?formato=02&estudio={estudio}&id={id_admision}&ImprimirImagenes=0"
        )
        options = {
            "encoding": "UTF-8",
            "cookie": ("PHPSESSID", phpsessid),
        }
        return pdfkit.from_url(url, False, options=options)

    session = _get_siesa_session()
    try:
        return _do_fetch(session)
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
    """Registra el PDF generado en la tabla archivos."""
    ArchivoFacturacion.objects.create(
        Admision_id=estudio,
        Tipo="RESULTADO",
        NombreArchivo=nombre,
        RutaArchivo=ruta_relativa,
        NumeroAdmision=str(estudio),
        FechaCreacionArchivo=datetime.now(),
        FechaCreacionAntares=datetime.now(),
        RevisionPrimera=False,
        RevisionSegunda=False,
        RevisionTercera=False,
        Radicado=False,
    )


def _estudios_por_fecha(fecha_inicio: str, fecha_fin: str):
    """
    Devuelve lista de (con_estudio, autoid) de sis_maes en ZeusSalud
    para estudios de imágenes diagnósticas en el rango de fechas.
    id_sede=3 → SEDE 02 (TAC, Resonancia, RX, Mamografía, PET/CT)
    id_sede=2 → SEDE 01 (Ecografías y otros procedimientos imagen)
    """
    with connections["zeussalud"].cursor() as cursor:
        cursor.execute(
            """
            SELECT con_estudio, autoid
            FROM sis_maes
            WHERE CAST(fecha_ing AS DATE) BETWEEN %s AND %s
              AND id_sede IN (2, 3)
              AND estado = 'A'
            ORDER BY fecha_ing
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
    Diagnóstico: prueba el login de SIESA paso a paso y devuelve cookies y
    el status del reporte para el estudio indicado.
    GET /api/v2/gedocumental/debug-siesa/?estudio=3039
    """
    import hashlib as _hashlib

    usuario   = getattr(settings, "SIESA_USUARIO",    "NO_CONFIGURADO")
    clave     = getattr(settings, "SIESA_CLAVE",      "")
    bd_usuario  = getattr(settings, "SIESA_BD_USUARIO",  "NO_CONFIGURADO")
    bd_password = getattr(settings, "SIESA_BD_PASSWORD", "")
    bd_servidor = getattr(settings, "SIESA_BD_SERVIDOR", "NEUROBACK")
    bd_nombre   = getattr(settings, "SIESA_BD_NOMBRE",   "ZeusSalud_Neuro")
    clave_md5 = _hashlib.md5(clave.encode()).hexdigest()

    estudio = request.GET.get("estudio", "3039")
    log = []
    session = requests.Session()
    session.headers.update(SIESA_HEADERS)

    try:
        r0 = session.get(SIESA_APP_URL, timeout=15)
        log.append({"paso": "app_index", "status": r0.status_code, "cookies": dict(session.cookies)})
    except Exception as e:
        log.append({"paso": "app_index", "error": str(e)})

    try:
        r1 = session.post(SIESA_CONFIG_URL, data={
            "operacion": "verificarUsuarios", "k_conexion": "CLIENTE",
            "conexion": bd_nombre, "servidor": bd_servidor,
            "usuarioBd": bd_usuario, "passwordBD": bd_password,
            "file_conexion": "", "file_servidor": "", "file_usuarioBd": "", "file_passwordBD": "",
        }, timeout=15)
        log.append({"paso": "configuracionInicial", "status": r1.status_code, "body": r1.text[:300]})
    except Exception as e:
        log.append({"paso": "configuracionInicial", "error": str(e)})

    try:
        r2 = session.post(SIESA_LOGIN_URL, data={
            "operacion": "Login", "BaseDato": bd_nombre, "ServidorBD": bd_servidor,
            "UsuarioBD": bd_usuario, "PasswordBD": bd_password,
            "NombreUsuario": usuario, "PasswordUsuario": clave_md5,
        }, timeout=15)
        log.append({"paso": "login", "status": r2.status_code, "body": r2.text[:300]})
    except Exception as e:
        log.append({"paso": "login", "error": str(e)})

    try:
        r3 = session.post(SIESA_LOGIN_URL, data={
            "operacion": "SetSedePuntoAtencion",
            "IdPuntoAtencion": SIESA_SEDE_ID,
            "NombrePuntoAtencion": SIESA_SEDE_NOMBRE,
            "IdSede": SIESA_SEDE_ID,
        }, timeout=15)
        log.append({"paso": "sede", "status": r3.status_code, "body": r3.text[:300]})
    except Exception as e:
        log.append({"paso": "sede", "error": str(e)})

    id_param = request.GET.get("id", "1")
    cookies = dict(session.cookies)
    try:
        r4 = session.get(SIESA_REPORT_URL, params={
            "formato": "02", "estudio": estudio, "id": id_param, "ImprimirImagenes": "0",
        }, timeout=30)
        html = r4.text
        # Buscar datos del paciente en el HTML para confirmar si hay contenido
        import re
        datos = re.findall(r'<td[^>]*>([^<]{5,})</td>', html)[:20]
        log.append({
            "paso": "reporte",
            "status": r4.status_code,
            "content_length": len(html),
            "tiene_datos": len(datos) > 3,
            "muestra_celdas": datos[:10],
            "body_inicio": html[:500],
        })
    except Exception as e:
        log.append({"paso": "reporte", "error": str(e)})

    return JsonResponse({
        "config": {"usuario": usuario, "bd_usuario": bd_usuario, "bd_servidor": bd_servidor, "bd_nombre": bd_nombre},
        "cookies": cookies,
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

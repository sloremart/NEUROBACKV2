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
from io import BytesIO

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.db import connections
from rest_framework.decorators import api_view
from io import BytesIO
from xhtml2pdf import pisa

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _siesa_login() -> requests.Session:
    """Crea una sesión autenticada en ZeusSalud (tres pasos)."""
    session = requests.Session()

    usuario   = getattr(settings, "SIESA_USUARIO",    "")
    clave     = getattr(settings, "SIESA_CLAVE",      "")
    clave_md5 = hashlib.md5(clave.encode()).hexdigest()

    bd_servidor = getattr(settings, "SIESA_BD_SERVIDOR", "NEUROBACK")
    bd_nombre   = getattr(settings, "SIESA_BD_NOMBRE",   "ZeusSalud_Neuro")
    bd_usuario  = getattr(settings, "SIESA_BD_USUARIO",  "")
    bd_password = getattr(settings, "SIESA_BD_PASSWORD", "")

    # Paso 1: configuración inicial de conexión
    session.post(
        SIESA_CONFIG_URL,
        data={
            "operacion":     "verificarUsuarios",
            "k_conexion":    "CLIENTE",
            "conexion":      bd_nombre,
            "servidor":      bd_servidor,
            "usuarioBd":     bd_usuario,
            "passwordBD":    bd_password,
            "file_conexion": "",
            "file_servidor": "",
            "file_usuarioBd":  "",
            "file_passwordBD": "",
        },
        timeout=30,
    )

    # Paso 2: login con credenciales del usuario — responde con lista de sedes
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
        timeout=30,
    )

    # Paso 3: seleccionar sede/punto de atención
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

    return session


def _get_siesa_session() -> requests.Session:
    """Devuelve la sesión activa o crea una nueva si no existe."""
    global _siesa_session_cache
    if _siesa_session_cache is None:
        _siesa_session_cache = _siesa_login()
    return _siesa_session_cache


def _fetch_html_siesa(estudio: int, id_admision: int) -> str:
    """Descarga el HTML del reporte SIESA para un estudio."""
    params = {
        "formato": "02",
        "estudio": estudio,
        "id": id_admision,
        "ImprimirImagenes": "0",
    }
    session = _get_siesa_session()
    resp = session.get(SIESA_REPORT_URL, params=params, timeout=30)

    # Si SIESA devuelve 500, puede ser sesión expirada — reintentar con login nuevo
    if resp.status_code == 500:
        global _siesa_session_cache
        _siesa_session_cache = None
        session = _get_siesa_session()
        resp = session.get(SIESA_REPORT_URL, params=params, timeout=30)

    resp.raise_for_status()
    return resp.text


def _html_to_pdf(html_content: str, base_url: str = None) -> bytes:
    """Convierte HTML a bytes PDF usando xhtml2pdf (pisa)."""
    buffer = BytesIO()
    result = pisa.CreatePDF(html_content.encode("utf-8"), dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"Error al convertir HTML a PDF: {result.err}")
    return buffer.getvalue()


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
        html = _fetch_html_siesa(estudio, id_admision)
    except requests.RequestException as e:
        return JsonResponse(
            {"success": False, "detail": f"No se pudo obtener el reporte de SIESA: {e}"},
            status=502,
        )

    try:
        pdf_bytes = _html_to_pdf(html, base_url=SIESA_REPORT_URL)
    except Exception as e:
        return JsonResponse(
            {"success": False, "detail": f"Error al generar el PDF: {e}"},
            status=500,
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
            html = _fetch_html_siesa(con_estudio, autoid)
            pdf_bytes = _html_to_pdf(html, base_url=SIESA_REPORT_URL)
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

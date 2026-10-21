"""
Diagnóstico de la nota de enfermería que entrega SIESA para un estudio. NO guarda nada.
Muestra paso a paso qué responde SIESA (formulario, generación y descarga) y prueba también
con un nombre de archivo único, para saber si SIESA está generando el PDF o si en la carpeta
Temp queda el último impreso con ese usuario.

Uso:
  python manage.py diagnostico_pdf_enfermeria 32108
"""

import io
import re
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections

from gedocumental import views_siesa
from gedocumental.views_siesa import (SIESA_FORMULARIO_URL, SIESA_REPORT_VIEW_URL, _get_siesa_session,
                                      _fetch_pdf_enfermeria, _parse_hidden_inputs, _pdf_corresponde_al_estudio,
                                      _urls_exportacion_pdf)

URL_TEMP = "http://192.168.1.209:8091/ZeusSalud/Archivos/CLIENTE/Temp/"


def _texto_pdf(pdf):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            return " ".join((p.extract_text() or "") for p in doc.pages[:2])
    except Exception as exc:
        return f"(no se pudo leer: {exc})"


def _texto_html(html):
    texto = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", texto)).strip()


class Command(BaseCommand):
    help = "Descarga (sin guardar) la nota de enfermería de SIESA y muestra paso a paso qué responde SIESA"

    def add_arguments(self, parser):
        parser.add_argument('estudio', type=int)

    def _descargar(self, s, nombre, estudio):
        url = URL_TEMP + nombre
        time.sleep(5)
        r = s.get(url, timeout=30)
        self.stdout.write(f"  GET {url} -> HTTP {r.status_code}, {len(r.content)} bytes, "
                          f"Last-Modified: {r.headers.get('Last-Modified', '?')}")
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            self.stdout.write(f"  No es PDF. Inicio: {r.content[:200]!r}")
            return
        texto = _texto_pdf(r.content)
        self.stdout.write("  Texto (inicio): " + re.sub(r"\s+", " ", texto[:500]))
        self.stdout.write(f"  Verificación del sistema (¿es del estudio {estudio}?): "
                          f"{_pdf_corresponde_al_estudio(r.content, estudio)}")

    def handle(self, *args, **o):
        estudio = o['estudio']
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute(
                "SELECT sp.num_id, sp.primer_nom, sp.segundo_nom, sp.primer_ape, sp.segundo_ape "
                "FROM sis_maes sm JOIN sis_paci sp ON sp.autoid = sm.autoid WHERE sm.con_estudio = %s", [estudio])
            fila = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM notas_enfermeria WHERE ingreso = %s", [estudio])
            notas = cursor.fetchone()[0]
        self.stdout.write(f"Zeus, estudio {estudio}: {' '.join((x or '').strip() for x in fila[1:]) if fila else '?'}"
                          f" | documento {fila[0] if fila else '?'} | notas de enfermería: {notas}")

        usuario_session = getattr(settings, "SIESA_USUARIO_SESSION", "")
        self.stdout.write(f"SIESA_USUARIO_SESSION: {usuario_session!r}")
        views_siesa._siesa_session_cache = None
        s = _get_siesa_session()
        self.stdout.write(f"Cookies de sesión SIESA: {sorted(s.cookies.keys())}")

        # Paso 1: formulario
        r1 = s.post(SIESA_FORMULARIO_URL, data={
            "estudio": estudio, "ImpHC": 0, "ImpNotasEnfermeria": 1, "UsuarioSession": usuario_session,
            "pdf": 1, "generarNombreArchivo": 1, "reportName": "HistoriaClinicaGeneral",
        }, timeout=30)
        self.stdout.write(f"\nPASO 1 FormularioFastReport -> HTTP {r1.status_code}")
        try:
            datos = r1.json()
        except ValueError:
            self.stdout.write(f"  No es JSON: {r1.text[:500]}")
            return
        self.stdout.write(f"  Claves de la respuesta: {list(datos.keys())}")
        campos = _parse_hidden_inputs(datos.get("payload", ""))
        for k, v in campos.items():
            self.stdout.write(f"  {k} = {v[:150]}")
        nombre = campos.get("nombreArchivoPdf")
        if not nombre:
            self.stdout.write("  SIESA no devolvió nombreArchivoPdf")
            return

        # Paso 2: generación con el nombre que da SIESA
        r2 = s.post(SIESA_REPORT_VIEW_URL, data=campos, timeout=60)
        self.stdout.write(f"\nPASO 2 c_informe_view -> HTTP {r2.status_code}, {len(r2.content)} bytes")
        self.stdout.write("  Texto de la respuesta: " + _texto_html(r2.text)[:800])

        # Fragmentos del visor relacionados con exportar (para ver el formato real de SIESA)
        html = r2.text.replace("&amp;", "&")
        self.stdout.write("\nFragmentos del visor con 'export' / 'frx' / '.axd':")
        vistos = set()
        for m in re.finditer(r"[^\s<>]{0,120}(?:export|frx|\.axd)[^\s<>]{0,160}", html, re.IGNORECASE):
            frag = m.group(0)
            if frag not in vistos and len(vistos) < 25:
                vistos.add(frag)
                self.stdout.write(f"  {frag}")

        self.stdout.write("\nPASO 3a exportación a PDF desde el visor")
        urls = _urls_exportacion_pdf(r2.text)
        if not urls:
            self.stdout.write("  No se encontraron URLs de exportación en el visor")
        for url in urls:
            try:
                r = s.get(url, timeout=60)
            except Exception as exc:
                self.stdout.write(f"  {url} -> error {exc}")
                continue
            es_pdf = r.content.startswith(b"%PDF")
            self.stdout.write(f"  {url} -> HTTP {r.status_code}, {len(r.content)} bytes, PDF: {es_pdf}")
            if es_pdf:
                self.stdout.write("    Texto (inicio): " + re.sub(r"\s+", " ", _texto_pdf(r.content)[:400]))
                self.stdout.write(f"    ¿Es del estudio {estudio}? {_pdf_corresponde_al_estudio(r.content, estudio)}")

        self.stdout.write(f"\nPASO 3b descarga de la carpeta Temp ({nombre})")
        self._descargar(s, nombre, estudio)

        self.stdout.write("\nRESULTADO con la función del sistema (_fetch_pdf_enfermeria, con verificación):")
        try:
            pdf = _fetch_pdf_enfermeria(estudio)
            self.stdout.write(f"  OK, {len(pdf)} bytes, del estudio: {_pdf_corresponde_al_estudio(pdf, estudio)}")
        except Exception as exc:
            self.stdout.write(f"  Falla: {exc}")

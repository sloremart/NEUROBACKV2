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
                                      _urls_exportacion_pdf, _campos_formulario_visor,
                                      _urls_descarga_exportacion, _pdf_desde_html_visor, _tipo_imagen)

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
        parser.add_argument('--consulta', action='store_true',
                            help='Historia clínica de consulta (ImpHC=1) en vez de nota de enfermería')

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
        imp_hc, imp_notas = (1, 0) if o['consulta'] else (0, 1)
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
            "estudio": estudio, "ImpHC": imp_hc, "ImpNotasEnfermeria": imp_notas, "UsuarioSession": usuario_session,
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

        # Cómo arma el visor la exportación: menú de formatos, funciones JS y todo lo que diga "pdf"
        self.stdout.write("\nFragmentos del visor con 'pdf' / 'select' / 'onchange' / 'function frx':")
        vistos = set()
        for m in re.finditer(r".{0,200}(?:pdf|<select|onchange|function\s+frx|__doPostBack|frxexport).{0,300}",
                             html, re.IGNORECASE):
            frag = re.sub(r"\s+", " ", m.group(0))
            if frag not in vistos and len(vistos) < 30:
                vistos.add(frag)
                self.stdout.write(f"  >> {frag}")
        formularios = _parse_hidden_inputs(r2.text)
        self.stdout.write(f"\nCampos ocultos del visor: { {k: v[:60] for k, v in formularios.items()} }")

        self.stdout.write(f"Cookies tras el visor: {sorted(s.cookies.keys())}")
        campos_v, select_export, boton = _campos_formulario_visor(r2.text)
        self.stdout.write(f"\nPOSTBACK exportar: menú={select_export}, botón={boton}, "
                          f"campos={sorted(campos_v.keys())}")
        m_form = re.search(r"<form\b[^>]*>", r2.text, re.IGNORECASE)
        self.stdout.write(f"  Formulario: {m_form.group(0) if m_form else '?'}")
        m_boton = re.search(r"<select\b[^>]*title=.Export.[\s\S]{0,1500}", r2.text, re.IGNORECASE)
        if m_boton:
            self.stdout.write("  HTML tras el menú Export: " + re.sub(r"\s+", " ", m_boton.group(0))[:1500])
        if select_export and boton:
            datos = dict(campos, **campos_v)
            datos.update({select_export: "pdf", f"{boton}.x": "8", f"{boton}.y": "8"})
            rp = s.post(SIESA_REPORT_VIEW_URL, data=datos, timeout=90, headers={"Referer": SIESA_REPORT_VIEW_URL},
                        allow_redirects=False)
            es_pdf = rp.content.startswith(b"%PDF")
            self.stdout.write(f"  POST -> HTTP {rp.status_code}, {len(rp.content)} bytes, PDF: {es_pdf}, "
                              f"Content-Type: {rp.headers.get('Content-Type')}, "
                              f"Content-Disposition: {rp.headers.get('Content-Disposition')}, "
                              f"historial: {[h.status_code for h in rp.history]} {rp.url}")
            if es_pdf:
                self.stdout.write("    Texto (inicio): " + re.sub(r"\s+", " ", _texto_pdf(rp.content)[:400]))
                self.stdout.write(f"    ¿Es del estudio {estudio}? {_pdf_corresponde_al_estudio(rp.content, estudio)}")
            else:
                t = rp.text.replace("&amp;", "&")
                for mm in re.finditer(r".{0,150}(?:frxexport|Export\.aspx|window\.open|location).{0,250}", t, re.I):
                    self.stdout.write("    >> " + re.sub(r"\s+", " ", mm.group(0)))
                self.stdout.write("    Texto: " + _texto_html(rp.text)[:300])
            self.stdout.write(f"  Location: {rp.headers.get('Location')} | cookies: {sorted(s.cookies.keys())}")
            for url in _urls_descarga_exportacion(rp):
                time.sleep(1)
                rd = s.get(url, timeout=60, headers={"Referer": SIESA_REPORT_VIEW_URL}, allow_redirects=False)
                ok = rd.content.startswith(b"%PDF")
                self.stdout.write(f"  GET {url} -> HTTP {rd.status_code}, {len(rd.content)} bytes, PDF: {ok}, "
                                  f"headers: {dict(rd.headers)}")
                if ok:
                    self.stdout.write("    Texto (inicio): " + re.sub(r"\s+", " ", _texto_pdf(rd.content)[:300]))
                    self.stdout.write(f"    ¿Es del estudio {estudio}? {_pdf_corresponde_al_estudio(rd.content, estudio)}")
                elif rd.content:
                    self.stdout.write(f"    Contenido: {rd.content[:300]!r}")

        self.stdout.write("\nIMÁGENES DEL VISOR")
        from urllib.parse import urljoin
        for mi in re.finditer(r"""<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>""", r2.text, re.IGNORECASE):
            src = mi.group(1).replace("&amp;", "&")
            if "WebResource.axd" in src:
                continue   # iconos de la barra del visor
            try:
                ri = s.get(urljoin(SIESA_REPORT_VIEW_URL, src), timeout=30)
                self.stdout.write(f"  {src[:90]} -> HTTP {ri.status_code}, {len(ri.content)} bytes, "
                                  f"Content-Type: {ri.headers.get('Content-Type')}, inicio: {ri.content[:8]!r}, "
                                  f"tipo detectado: {_tipo_imagen(ri.content)}")
            except Exception as exc:
                self.stdout.write(f"  {src[:90]} -> error {exc}")
            self.stdout.write(f"    etiqueta: {mi.group(0)[:200]}")

        self.stdout.write("\nHTML DEL VISOR -> PDF (wkhtmltopdf)")
        try:
            from gedocumental.views_siesa import _extraer_hoja_visor, _pagina_visor_html
            estilos_p1, cuerpo_p1 = _pagina_visor_html(s, r2.text)
            _, ancho, alto = _extraer_hoja_visor(cuerpo_p1)
            self.stdout.write(f"  Hoja detectada en el visor: {ancho} x {alto} px | <style> en head: "
                              f"{estilos_p1.lower().count('<style')}, en body: {cuerpo_p1.lower().count('<style')}")
            if not ancho:
                for nombre_e, bloque in (("head", estilos_p1), ("body", cuerpo_p1)):
                    for me in re.finditer(r"<style\b[^>]*>(.*?)</style>", bloque, re.IGNORECASE | re.DOTALL):
                        reglas = re.sub(r"\s+", " ", me.group(1))
                        self.stdout.write(f"    <style> del {nombre_e} ({len(reglas)} car.): {reglas[:700]}")
                        for mr in re.finditer(r"[^{}]{0,80}\{[^}]*(?:width|height)[^}]*\}", reglas):
                            self.stdout.write(f"      regla con tamaño: {mr.group(0).strip()[:200]}")
                self.stdout.write("    Estructura (primeras etiquetas del cuerpo):")
                sin_estilo = re.sub(r"<style\b.*?</style>", "", cuerpo_p1, flags=re.IGNORECASE | re.DOTALL)
                for n_tag, mt in enumerate(re.finditer(r"<(?!/)[a-zA-Z][^>]*>", sin_estilo)):
                    if n_tag >= 45:
                        break
                    self.stdout.write("      " + re.sub(r"\s+", " ", mt.group(0))[:180])
            pdf_h = _pdf_desde_html_visor(s, r2.text, campos)
            if pdf_h:
                import io as _io
                from PyPDF2 import PdfReader
                self.stdout.write(f"  PDF: {len(pdf_h)} bytes, {len(PdfReader(_io.BytesIO(pdf_h)).pages)} páginas")
                self.stdout.write("  Texto (inicio): " + re.sub(r"\s+", " ", _texto_pdf(pdf_h)[:400]))
                self.stdout.write(f"  ¿Es del estudio {estudio}? {_pdf_corresponde_al_estudio(pdf_h, estudio)}")
            else:
                self.stdout.write("  No se generó PDF")
        except Exception as exc:
            self.stdout.write(f"  Error: {exc!r}")

        self.stdout.write("\nPASO 3a exportación a PDF desde el visor")
        urls = _urls_exportacion_pdf(r2.text)
        if not urls:
            self.stdout.write("  No se encontraron URLs de exportación en el visor")
        for url in urls:
            es_pdf = False
            for intento, headers in enumerate(({}, {"Referer": SIESA_REPORT_VIEW_URL})):
                if intento:
                    time.sleep(3)
                try:
                    r = s.get(url, timeout=60, headers=headers, allow_redirects=False)
                except Exception as exc:
                    self.stdout.write(f"  {url} -> error {exc}")
                    continue
                es_pdf = r.content.startswith(b"%PDF")
                self.stdout.write(f"  {url} [{'con Referer, 3 s después' if intento else 'directo'}] -> "
                                  f"HTTP {r.status_code}, {len(r.content)} bytes, PDF: {es_pdf}, "
                                  f"Content-Type: {r.headers.get('Content-Type')}, "
                                  f"Location: {r.headers.get('Location')}, "
                                  f"Content-Disposition: {r.headers.get('Content-Disposition')}")
                if not es_pdf and r.content:
                    self.stdout.write(f"    Contenido: {r.content[:600]!r}")
                if es_pdf:
                    break
            if es_pdf:
                self.stdout.write("    Texto (inicio): " + re.sub(r"\s+", " ", _texto_pdf(r.content)[:400]))
                self.stdout.write(f"    ¿Es del estudio {estudio}? {_pdf_corresponde_al_estudio(r.content, estudio)}")

        self.stdout.write(f"\nPASO 3b descarga de la carpeta Temp ({nombre})")
        self._descargar(s, nombre, estudio)

        self.stdout.write("\nRESULTADO con la función del sistema (_fetch_pdf_enfermeria, con verificación):")
        try:
            pdf = _fetch_pdf_enfermeria(estudio, imp_hc=imp_hc, imp_notas=imp_notas)
            self.stdout.write(f"  OK, {len(pdf)} bytes, del estudio: {_pdf_corresponde_al_estudio(pdf, estudio)}")
        except Exception as exc:
            self.stdout.write(f"  Falla: {exc}")

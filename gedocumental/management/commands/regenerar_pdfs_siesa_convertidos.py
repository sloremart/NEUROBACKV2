"""
Busca los RESULTADOS generados por el sistema (historias de consulta y notas de enfermería) que NO son
el PDF original de SIESA sino la conversión del HTML del visor (marcados por PyPDF2/wkhtmltopdf), y
los reemplaza por el PDF original que entrega SIESA por Pdf.php.

Las historias de consulta que no se puedan obtener en original se BORRAN (la conversión de
neurología sale con el texto letra por letra): la tarea programada las vuelve a generar en su
próxima corrida.

Uso:
  python manage.py regenerar_pdfs_siesa_convertidos                  # solo lista
  python manage.py regenerar_pdfs_siesa_convertidos --aplicar        # regenera
  python manage.py regenerar_pdfs_siesa_convertidos --aplicar --solo-consultas
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections

from gedocumental.models import ArchivoFacturacion
from gedocumental.views_siesa import (_fetch_pdf_consulta, _fetch_pdf_enfermeria, _guardar_pdf, _registrar_en_bd,
                                      _tipos_hc)

UFUNCIONAL_CONSULTA, UFUNCIONAL_ENFERMERIA = 1, 12


def _marca_pdf(ruta: str) -> str:
    """Productor y creador del PDF en minúsculas ('' si no se puede leer)."""
    try:
        from PyPDF2 import PdfReader
        with open(ruta, "rb") as f:
            meta = PdfReader(f).metadata or {}
            marca = f"{meta.get('/Producer', '')} {meta.get('/Creator', '')}".lower()
        return marca
    except Exception:
        return ""
    return f"{meta.get('/Producer', '')} {meta.get('/Creator', '')}".lower()


def _es_conversion(marca: str, consulta: bool) -> bool:
    """
    Historias de consulta: solo las que pasaron por la conversión del visor, que siempre termina con
    PyPDF2 (recorte a carta). Los resultados de imágenes de SIESA usan wkhtmltopdf pero nunca PyPDF2:
    no se tocan. Notas de enfermería: cualquier PDF armado por el sistema (PyPDF2 o wkhtmltopdf).
    """
    if not marca or "fastreport" in marca:
        return False
    if consulta:
        return "pypdf" in marca
    return any(x in marca for x in ("pypdf", "wkhtmltopdf", "qt "))


class Command(BaseCommand):
    help = "Reemplaza por el PDF original de SIESA los RESULTADOS que quedaron como conversión del HTML"

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Regenerar de verdad (sin esto solo lista)")
        parser.add_argument("--solo-consultas", dest="solo_consultas", action="store_true",
                            help="(por defecto) solo historias de consulta")
        parser.add_argument("--incluir-enfermeria", dest="incluir_enfermeria", action="store_true",
                            help="Incluir también las notas de enfermería convertidas")
        parser.add_argument("--estudios", help="Lista de admisiones separadas por coma (sin revisar las demás)")
        parser.add_argument("--dias", type=int, default=30,
                            help="Solo PDF creados en los últimos N días (por defecto 30)")
        parser.add_argument("--limite", type=int, default=0, help="Procesar como máximo N (0 = todos)")

    def handle(self, *args, **o):
        import gc
        from datetime import datetime, timedelta
        consulta_base = ArchivoFacturacion.objects.filter(Tipo="RESULTADO", Usuario__isnull=True)
        if o["estudios"]:
            consulta_base = consulta_base.filter(
                Admision_id__in=[int(x) for x in o["estudios"].replace(" ", "").split(",") if x])
        else:
            consulta_base = consulta_base.filter(
                FechaCreacionArchivo__gte=datetime.now() - timedelta(days=o["dias"]))
        candidatos = [a for a in consulta_base.only("IdArchivo", "Admision_id", "NombreArchivo", "RutaArchivo")
                      if a.NombreArchivo == f"{a.Admision_id}R.pdf"]
        ids = [a.Admision_id for a in candidatos]
        ufuncional = {}
        with connections["zeussalud"].cursor() as cursor:
            for i in range(0, len(ids), 500):
                lote = ids[i:i + 500]
                cursor.execute(
                    f"SELECT con_estudio, ufuncional FROM sis_maes WHERE con_estudio IN ({', '.join(['%s'] * len(lote))})",
                    lote)
                ufuncional.update({fila[0]: fila[1] for fila in cursor.fetchall()})
            # Consultas: solo las que tienen historia de neurología o fisiatría (las que genera el sistema)
            tipos_hc = _tipos_hc("todas")
            ids_consulta = [i for i in ids if ufuncional.get(i) == UFUNCIONAL_CONSULTA]
            con_hc = set()
            for i in range(0, len(ids_consulta), 500):
                lote = ids_consulta[i:i + 500]
                cursor.execute(
                    f"SELECT DISTINCT con_estudio FROM hcingres WHERE con_estudio IN ({', '.join(['%s'] * len(lote))}) "
                    f"AND LTRIM(RTRIM(tipo_histo)) IN ({', '.join(['%s'] * len(tipos_hc))})",
                    [*lote, *tipos_hc])
                con_hc.update(fila[0] for fila in cursor.fetchall())

        tipos = (UFUNCIONAL_CONSULTA, UFUNCIONAL_ENFERMERIA) if o["incluir_enfermeria"] else (UFUNCIONAL_CONSULTA,)
        convertidos = []
        for a in candidatos:
            uf = ufuncional.get(a.Admision_id)
            if uf not in tipos:
                continue
            consulta = uf == UFUNCIONAL_CONSULTA
            if consulta and a.Admision_id not in con_hc:
                continue
            try:
                ruta = a.RutaArchivo.path if a.RutaArchivo else ""
            except Exception:
                ruta = os.path.join(settings.MEDIA_ROOT, str(a.RutaArchivo))
            if ruta and os.path.isfile(ruta) and _es_conversion(_marca_pdf(ruta), consulta):
                convertidos.append((a, uf))

        # Primero las consultas (las historias de neurología/fisiatría son las que salen mal convertidas)
        convertidos.sort(key=lambda x: (x[1] != UFUNCIONAL_CONSULTA, x[0].Admision_id))
        if o["limite"]:
            convertidos = convertidos[:o["limite"]]
        self.stdout.write(f"RESULTADOS convertidos (no originales de SIESA): {len(convertidos)}")
        for a, uf in convertidos:
            self.stdout.write(f"  {a.Admision_id}  {'consulta' if uf == UFUNCIONAL_CONSULTA else 'enfermería'}")
        if not o["aplicar"]:
            self.stdout.write(self.style.WARNING("No se cambió nada. Para regenerar agregue --aplicar."))
            return

        ok, borrados, errores = 0, 0, 0
        for a, uf in convertidos:
            estudio = a.Admision_id
            try:
                if uf == UFUNCIONAL_CONSULTA:
                    pdf = _fetch_pdf_consulta(estudio)
                else:
                    pdf = _fetch_pdf_enfermeria(estudio)
                import io
                from PyPDF2 import PdfReader
                meta = PdfReader(io.BytesIO(pdf)).metadata or {}
                original = "fastreport" in f"{meta.get('/Producer', '')} {meta.get('/Creator', '')}".lower()
                ruta_relativa, _, nombre = _guardar_pdf(estudio, pdf)
                _registrar_en_bd(estudio, nombre, ruta_relativa)
                ok += 1
                self.stdout.write(f"  {estudio}: reemplazado por "
                                  + ("el PDF original de SIESA" if original else "una nueva conversión (respaldo)"))
            except Exception as exc:
                if uf == UFUNCIONAL_CONSULTA:
                    a.delete()   # la tarea programada lo vuelve a generar en original
                    borrados += 1
                    self.stdout.write(f"  {estudio}: sin original por ahora, se borró la conversión ({exc})")
                else:
                    errores += 1
                    self.stdout.write(f"  {estudio}: no se pudo regenerar, se deja como está ({exc})")
            pdf = None
            gc.collect()          # pdfplumber acumula memoria entre documentos
            self.stdout.flush()
        self.stdout.write(self.style.SUCCESS(
            f"Reemplazados: {ok} | Consultas borradas para regenerar luego: {borrados} | Errores: {errores}"))

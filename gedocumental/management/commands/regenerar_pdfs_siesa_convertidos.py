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
from gedocumental.views_siesa import _fetch_pdf_consulta, _fetch_pdf_enfermeria, _guardar_pdf, _registrar_en_bd

UFUNCIONAL_CONSULTA, UFUNCIONAL_ENFERMERIA = 1, 12


def _es_conversion(ruta: str):
    """True si el PDF lo armó el sistema (no es el original de FastReport/SIESA); None si no se puede leer."""
    try:
        from PyPDF2 import PdfReader
        meta = PdfReader(ruta).metadata or {}
    except Exception:
        return None
    marca = f"{meta.get('/Producer', '')} {meta.get('/Creator', '')}".lower()
    if "fastreport" in marca:
        return False
    return any(x in marca for x in ("pypdf2", "pypdf", "wkhtmltopdf", "qt "))


class Command(BaseCommand):
    help = "Reemplaza por el PDF original de SIESA los RESULTADOS que quedaron como conversión del HTML"

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Regenerar de verdad (sin esto solo lista)")
        parser.add_argument("--solo-consultas", dest="solo_consultas", action="store_true")

    def handle(self, *args, **o):
        candidatos = [a for a in ArchivoFacturacion.objects.filter(Tipo="RESULTADO", Usuario__isnull=True)
                      .only("IdArchivo", "Admision_id", "NombreArchivo", "RutaArchivo")
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

        tipos = (UFUNCIONAL_CONSULTA,) if o["solo_consultas"] else (UFUNCIONAL_CONSULTA, UFUNCIONAL_ENFERMERIA)
        convertidos = []
        for a in candidatos:
            uf = ufuncional.get(a.Admision_id)
            if uf not in tipos:
                continue
            try:
                ruta = a.RutaArchivo.path if a.RutaArchivo else ""
            except Exception:
                ruta = os.path.join(settings.MEDIA_ROOT, str(a.RutaArchivo))
            if ruta and os.path.isfile(ruta) and _es_conversion(ruta):
                convertidos.append((a, uf))

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
                ruta_relativa, _, nombre = _guardar_pdf(estudio, pdf)
                _registrar_en_bd(estudio, nombre, ruta_relativa)
                ok += 1
                self.stdout.write(f"  {estudio}: reemplazado por el original")
            except Exception as exc:
                if uf == UFUNCIONAL_CONSULTA:
                    a.delete()   # la tarea programada lo vuelve a generar en original
                    borrados += 1
                    self.stdout.write(f"  {estudio}: sin original por ahora, se borró la conversión ({exc})")
                else:
                    errores += 1
                    self.stdout.write(f"  {estudio}: no se pudo regenerar, se deja como está ({exc})")
        self.stdout.write(self.style.SUCCESS(
            f"Reemplazados: {ok} | Consultas borradas para regenerar luego: {borrados} | Errores: {errores}"))

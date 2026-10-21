"""
Diagnóstico de la nota de enfermería que entrega SIESA para un estudio. NO guarda nada.

Uso:
  python manage.py diagnostico_pdf_enfermeria 32108
"""

import io
import re

from django.core.management.base import BaseCommand
from django.db import connections

from gedocumental.views_siesa import _fetch_pdf_enfermeria, _pdf_corresponde_al_estudio


class Command(BaseCommand):
    help = "Descarga (sin guardar) la nota de enfermería de SIESA y muestra a qué paciente corresponde"

    def add_arguments(self, parser):
        parser.add_argument('estudio', type=int)

    def handle(self, *args, **o):
        estudio = o['estudio']
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute(
                "SELECT sp.num_id, sp.primer_nom, sp.segundo_nom, sp.primer_ape, sp.segundo_ape "
                "FROM sis_maes sm JOIN sis_paci sp ON sp.autoid = sm.autoid WHERE sm.con_estudio = %s", [estudio])
            fila = cursor.fetchone()
        self.stdout.write(f"Zeus, estudio {estudio}: {' '.join((x or '').strip() for x in fila[1:]) if fila else '?'}"
                          f" | documento {fila[0] if fila else '?'}")

        pdf = _fetch_pdf_enfermeria(estudio, verificar=False)
        self.stdout.write(f"PDF descargado: {len(pdf)} bytes")
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf)) as doc:
                texto = " ".join((p.extract_text() or "") for p in doc.pages[:2])
        except Exception as exc:
            texto = f"(no se pudo leer: {exc})"
        self.stdout.write("--- Texto del PDF (inicio) ---")
        self.stdout.write(texto[:900])
        self.stdout.write("---")
        self.stdout.write(f"¿Contiene el número de estudio {estudio}? {str(estudio) in texto}")
        num_id = re.sub(r"\D", "", (fila[0] if fila else "") or "")
        texto_compacto = re.sub(r"[.,\s]", "", texto)
        contiene_documento = bool(num_id) and num_id in texto_compacto
        self.stdout.write(f"¿Contiene el documento {num_id}? {contiene_documento}")
        self.stdout.write(f"Verificación del sistema: {_pdf_corresponde_al_estudio(pdf, estudio)}")

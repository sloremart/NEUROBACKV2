"""
Borra los RESULTADOS generados por el sistema que la IA marcó como "Paciente no corresponde"
(ej. PDF de enfermería de SIESA que salió con el paciente del estudio anterior).

Borra el registro, el PDF de GE Documental y la copia en la carpeta del portal de resultados
(/media/disco1/examenes), para que importar_resultados no lo vuelva a cargar. El hallazgo queda
cerrado como "Archivo eliminado" y, si el RESULTADO no se vuelve a generar, revisar_resultados
avisará "Ausencia del reporte" a la coordinación médica.

Uso:
  python manage.py limpiar_resultados_otro_paciente            # solo lista (no borra nada)
  python manage.py limpiar_resultados_otro_paciente --aplicar  # borra
"""

import os

from django.core.management.base import BaseCommand

from gedocumental.models import ArchivoFacturacion, RevisionIA

CARPETA_PORTAL = '/media/disco1/examenes'


class Command(BaseCommand):
    help = "Borra RESULTADOS generados por el sistema que son de otro paciente (hallazgo IA pendiente)"

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help="Borrar de verdad (sin esto solo lista)")

    def handle(self, *args, **o):
        ids = (RevisionIA.objects.filter(
                   Resuelta=False, TipoDocumento='RESULTADO', TipoHallazgo='INFORMACION',
                   Descripcion__startswith='Paciente no corresponde', IdArchivo__isnull=False)
               .values_list('IdArchivo', flat=True))
        candidatos = [
            a for a in ArchivoFacturacion.objects.filter(IdArchivo__in=ids, Tipo='RESULTADO', Usuario__isnull=True)
            if a.NombreArchivo == f"{a.Admision_id}R.pdf"       # generado por el sistema (SIESA)
        ]

        self.stdout.write(f"RESULTADOS de otro paciente generados por el sistema: {len(candidatos)}")
        for a in candidatos:
            rev = a.RevisionesIA.filter(Resuelta=False).order_by('-IdRevision').first()
            self.stdout.write(f"  {a.Admision_id}  {a.NombreArchivo}  | {rev.Descripcion[:110] if rev else ''}")

        if not o['aplicar']:
            self.stdout.write(self.style.WARNING("No se borró nada. Para borrar agregue --aplicar."))
            return

        borrados = 0
        for a in candidatos:
            copia = os.path.join(CARPETA_PORTAL, a.NombreArchivo)
            if os.path.isfile(copia):
                os.remove(copia)
            a.delete()   # borra el PDF de GE Documental y cierra el hallazgo (ARCHIVO_ELIMINADO)
            borrados += 1
            self.stdout.write(f"  borrado {a.Admision_id}")
        self.stdout.write(self.style.SUCCESS(f"Borrados: {borrados}"))

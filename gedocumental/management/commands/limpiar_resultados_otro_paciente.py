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

  # Incluye también los subidos por usuarios, filtrando por el paciente equivocado que traen:
  python manage.py limpiar_resultados_otro_paciente --documento-de "ALEXANDER SANABRIA NOVOA"
  python manage.py limpiar_resultados_otro_paciente --documento-de "ALEXANDER SANABRIA NOVOA" --aplicar
"""

import os

from django.core.management.base import BaseCommand

from gedocumental.models import ArchivoFacturacion, RevisionIA

CARPETA_PORTAL = '/media/disco1/examenes'


class Command(BaseCommand):
    help = "Borra RESULTADOS que la IA marcó como de otro paciente (hallazgo pendiente) y su copia en el portal"

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help="Borrar de verdad (sin esto solo lista)")
        parser.add_argument('--documento-de', dest='documento_de',
                            help="Nombre del paciente equivocado que trae el PDF; incluye los subidos por usuarios")

    def handle(self, *args, **o):
        revisiones = RevisionIA.objects.filter(
            Resuelta=False, TipoDocumento='RESULTADO', TipoHallazgo='INFORMACION',
            Descripcion__startswith='Paciente no corresponde', IdArchivo__isnull=False)
        if o['documento_de']:
            # Solo los que traen ese paciente equivocado, los haya subido el sistema o un usuario
            revisiones = revisiones.filter(Descripcion__icontains=f"documento '{o['documento_de'].strip()}")
            candidatos = list(ArchivoFacturacion.objects.filter(
                IdArchivo__in=revisiones.values_list('IdArchivo', flat=True), Tipo='RESULTADO'))
        else:
            candidatos = [
                a for a in ArchivoFacturacion.objects.filter(
                    IdArchivo__in=revisiones.values_list('IdArchivo', flat=True), Tipo='RESULTADO',
                    Usuario__isnull=True)
                if a.NombreArchivo == f"{a.Admision_id}R.pdf"       # generado por el sistema (SIESA)
            ]

        self.stdout.write(f"RESULTADOS de otro paciente a borrar: {len(candidatos)}")
        for a in candidatos:
            rev = a.RevisionesIA.filter(Resuelta=False).order_by('-IdRevision').first()
            subido = a.Usuario.nombre if a.Usuario_id and a.Usuario else 'sistema'
            self.stdout.write(f"  {a.Admision_id}  {a.NombreArchivo}  (subido por: {subido}) | "
                              f"{rev.Descripcion[:90] if rev else ''}")

        if not o['aplicar']:
            self.stdout.write(self.style.WARNING("No se borró nada. Para borrar agregue --aplicar."))
            return

        borrados = 0
        for a in candidatos:
            # Copias en el portal: la del propio archivo y la que genera SIESA ({estudio}R.pdf),
            # para que importar_resultados no vuelva a cargar el PDF del paciente equivocado
            for nombre in {a.NombreArchivo, f"{a.Admision_id}R.pdf"}:
                copia = os.path.join(CARPETA_PORTAL, nombre)
                if os.path.isfile(copia):
                    os.remove(copia)
            a.delete()   # borra el PDF de GE Documental y cierra el hallazgo (ARCHIVO_ELIMINADO)
            borrados += 1
            self.stdout.write(f"  borrado {a.Admision_id}")
        self.stdout.write(self.style.SUCCESS(f"Borrados: {borrados}"))

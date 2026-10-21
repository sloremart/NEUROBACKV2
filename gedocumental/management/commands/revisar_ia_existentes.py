"""
Revisa con IA archivos que ya estaban cargados y registra los hallazgos en revision_ia
(los mismos que verá el usuario en GE Documental y en el reporte Hallazgos IA).

Uso:
  python manage.py revisar_ia_existentes --admision 17206
  python manage.py revisar_ia_existentes --desde 2026-10-01 --hasta 2026-10-15 --limite 50
  python manage.py revisar_ia_existentes --admision 17206 --forzar   # revisa aunque ya tenga revisión

Por defecto omite los archivos que ya tienen alguna revisión IA.
Cada archivo es una llamada a OpenAI: use --limite para controlar el costo.
"""

from django.core.management.base import BaseCommand, CommandError

from gedocumental.models import ArchivoFacturacion
from gedocumental.services.ai_revision import TIPOS_SIN_REVISION_IA, obtener_contexto_admision, revisar_archivo


class Command(BaseCommand):
    help = "Revisa con IA archivos ya cargados y registra los hallazgos"

    def add_arguments(self, parser):
        parser.add_argument('--admision', type=int)
        parser.add_argument('--desde', help="Fecha de cargue desde (YYYY-MM-DD)")
        parser.add_argument('--hasta', help="Fecha de cargue hasta (YYYY-MM-DD)")
        parser.add_argument('--limite', type=int, default=20)
        parser.add_argument('--forzar', action='store_true', help="Revisar aunque ya tenga revisión IA")

    def handle(self, *args, **o):
        if not (o['admision'] or o['desde']):
            raise CommandError("Indique --admision o --desde para no revisar todo el histórico")

        archivos = ArchivoFacturacion.objects.exclude(Tipo__in=TIPOS_SIN_REVISION_IA)
        if o['admision']:
            archivos = archivos.filter(Admision_id=o['admision'])
        if o['desde']:
            archivos = archivos.filter(FechaCreacionArchivo__date__gte=o['desde'])
        if o['hasta']:
            archivos = archivos.filter(FechaCreacionArchivo__date__lte=o['hasta'])
        if not o['forzar']:
            archivos = archivos.filter(RevisionesIA__isnull=True)
        archivos = list(archivos.order_by('Admision_id', 'IdArchivo')[:o['limite']])

        self.stdout.write(f"Archivos a revisar: {len(archivos)}")
        contextos = {}
        resumen = {}
        for a in archivos:
            if a.Admision_id not in contextos:
                contextos[a.Admision_id] = obtener_contexto_admision(a.Admision_id)
            res = revisar_archivo(a, contextos[a.Admision_id])
            tipo = res['tipo'] if res else 'OMITIDO'
            resumen[tipo] = resumen.get(tipo, 0) + 1
            detalle = f" — {res['descripcion']}" if res and tipo != 'OK' else ''
            estilo = self.style.SUCCESS if tipo == 'OK' else self.style.WARNING
            self.stdout.write(estilo(f"  {a.Admision_id} {a.Tipo:<14} {tipo}{detalle}"))
            self.stdout.flush()  # avance visible aunque se ejecute sin terminal (docker exec / cron)

        self.stdout.write(f"Resumen: {resumen}")
        if resumen.get('OMITIDO'):
            self.stdout.write("OMITIDO = IA desactivada, sin API key, formato no revisable o archivo no encontrado en disco.")

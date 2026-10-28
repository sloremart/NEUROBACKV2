"""
Revisa con IA archivos que ya estaban cargados y registra los hallazgos en revision_ia
(los mismos que verá el usuario en GE Documental y en el reporte Hallazgos IA).

Uso:
  python manage.py revisar_ia_existentes --admision 17206
  python manage.py revisar_ia_existentes --desde 2026-10-01 --hasta 2026-10-15 --limite 50
  python manage.py revisar_ia_existentes --admision 17206 --forzar   # revisa aunque ya tenga revisión
  python manage.py revisar_ia_existentes --desde 2026-10-20 --solo-hallazgos  # solo los que tienen hallazgo pendiente

Por defecto omite los archivos que ya tienen alguna revisión IA.
Cada archivo es una llamada a OpenAI: use --limite para controlar el costo.
"""

from django.core.management.base import BaseCommand, CommandError

from gedocumental.models import ArchivoFacturacion, RevisionIA
from gedocumental.services.ai_revision import TIPOS_SIN_REVISION_IA, obtener_contexto_admision, revisar_archivo


class Command(BaseCommand):
    help = "Revisa con IA archivos ya cargados y registra los hallazgos"

    def add_arguments(self, parser):
        parser.add_argument('--admision', type=int)
        parser.add_argument('--desde', help="Fecha de cargue desde (YYYY-MM-DD)")
        parser.add_argument('--hasta', help="Fecha de cargue hasta (YYYY-MM-DD)")
        parser.add_argument('--limite', type=int, default=20)
        parser.add_argument('--forzar', action='store_true', help="Revisar aunque ya tenga revisión IA")
        parser.add_argument('--solo-hallazgos', action='store_true',
                            help="Revisar de nuevo solo los archivos con un hallazgo IA pendiente (no los que están OK)")
        parser.add_argument('--tipo', help="Solo este tipo de documento (COMPROBANTE, ORDEN, AUTORIZACION...)")
        parser.add_argument('--motivo', help="Solo hallazgos cuya descripción contenga este texto (ej. firma)")
        parser.add_argument('--con-firma', dest='con_firma', action='store_true',
                            help="Solo comprobantes en los que el sistema detecta la firma (revisión local, sin costo)")
        parser.add_argument('--listar', action='store_true', help="Solo listar lo que se revisaría (no llama a OpenAI)")

    def handle(self, *args, **o):
        if not (o['admision'] or o['desde'] or o['solo_hallazgos']):
            raise CommandError("Indique --admision, --desde o --solo-hallazgos para no revisar todo el histórico")

        archivos = ArchivoFacturacion.objects.exclude(Tipo__in=TIPOS_SIN_REVISION_IA)
        if o['admision']:
            archivos = archivos.filter(Admision_id=o['admision'])
        if o['desde']:
            archivos = archivos.filter(FechaCreacionArchivo__date__gte=o['desde'])
        if o['hasta']:
            archivos = archivos.filter(FechaCreacionArchivo__date__lte=o['hasta'])
        if o['tipo']:
            archivos = archivos.filter(Tipo=o['tipo'].upper())
        if o['solo_hallazgos']:
            con_hallazgo = (RevisionIA.objects.filter(Resuelta=False, IdArchivo__isnull=False)
                            .exclude(TipoHallazgo='OK'))
            if o['motivo']:
                con_hallazgo = con_hallazgo.filter(Descripcion__icontains=o['motivo'])
            archivos = archivos.filter(IdArchivo__in=con_hallazgo.values('IdArchivo'))
        elif not o['forzar']:
            archivos = archivos.filter(RevisionesIA__isnull=True)
        archivos = list(archivos.order_by('Admision_id', 'IdArchivo')[:o['limite']])
        if o['con_firma']:
            from gedocumental.services.ai_revision import _detectar_firma_pdf
            con_firma = []
            for a in archivos:
                try:
                    ruta = a.RutaArchivo.path
                except Exception:
                    continue
                if _detectar_firma_pdf(ruta):
                    con_firma.append(a)
            self.stdout.write(f"Con firma detectada por el sistema: {len(con_firma)} de {len(archivos)}")
            archivos = con_firma
        if o['listar']:
            self.stdout.write(f"Se revisarían {len(archivos)} archivos (no se llamó a OpenAI):")
            for a in archivos:
                self.stdout.write(f"  {a.Admision_id} {a.Tipo} {a.NombreArchivo}")
            return

        self.stdout.write(f"Archivos a revisar: {len(archivos)}")
        contextos = {}
        resumen = {}
        entrada = salida = llamadas = 0
        for a in archivos:
            if a.Admision_id not in contextos:
                contextos[a.Admision_id] = obtener_contexto_admision(a.Admision_id)
            res = revisar_archivo(a, contextos[a.Admision_id])
            if res:
                tok = res.get('tokens') or [0, 0]
                entrada, salida, llamadas = entrada + tok[0], salida + tok[1], llamadas + res.get('llamadas', 1)
            tipo = res['tipo'] if res else 'OMITIDO'
            resumen[tipo] = resumen.get(tipo, 0) + 1
            detalle = f" — {res['descripcion']}" if res and tipo != 'OK' else ''
            estilo = self.style.SUCCESS if tipo == 'OK' else self.style.WARNING
            self.stdout.write(estilo(f"  {a.Admision_id} {a.Tipo:<14} {tipo}{detalle}"))
            self.stdout.flush()  # avance visible aunque se ejecute sin terminal (docker exec / cron)

        self.stdout.write(f"Resumen: {resumen}")
        revisados = sum(v for k, v in resumen.items() if k != 'OMITIDO')
        if revisados:
            self.stdout.write(f"Consumo OpenAI: {llamadas} llamadas para {revisados} archivos | tokens de entrada "
                              f"{entrada:,} | de salida {salida:,} | promedio {entrada // revisados:,} por archivo")
        if resumen.get('OMITIDO'):
            self.stdout.write("OMITIDO = IA desactivada, sin API key, formato no revisable o archivo no encontrado en disco.")

"""
Cierra sin IA (sin costo de OpenAI) los hallazgos pendientes que ya no aplican: corregidos en ZeusSalud
o mal leídos por la IA (el PDF digital tiene el dato correcto). Ver gedocumental/services/reverificacion.py.
El cron revisar_resultados ya lo ejecuta; este comando es para probarlo o forzarlo.

Uso:
  python manage.py reverificar_hallazgos --listar                 # qué cerraría, sin guardar
  python manage.py reverificar_hallazgos                          # cierra lo que ya no aplica
  python manage.py reverificar_hallazgos --admision 33534 --forzar-pdf
  python manage.py reverificar_hallazgos --tipo ORDEN --requieren-ia   # escaneados que solo la IA puede releer
"""

from django.core.management.base import BaseCommand

from gedocumental.services.reverificacion import reverificar_hallazgos_sin_ia


class Command(BaseCommand):
    help = "Cierra sin IA los hallazgos corregidos en Zeus o mal leídos por la IA"

    def add_arguments(self, parser):
        parser.add_argument('--listar', action='store_true', help="Solo muestra qué cerraría; no guarda nada")
        parser.add_argument('--admision', type=int)
        parser.add_argument('--tipo', help="Tipo de documento (ORDEN, AUTORIZACION, COMPROBANTE, RESULTADO...)")
        parser.add_argument('--forzar-pdf', action='store_true',
                            help="Relee el PDF aunque se haya releído en las últimas 24 horas")
        parser.add_argument('--max-segundos', type=int, default=600, help="Tope de tiempo leyendo PDFs")
        parser.add_argument('--requieren-ia', action='store_true',
                            help="Muestra también los escaneados que solo la IA puede volver a leer")

    def handle(self, *args, **o):
        r = reverificar_hallazgos_sin_ia(admision=o['admision'], tipo_documento=o['tipo'],
                                         aplicar=not o['listar'], forzar_pdf=o['forzar_pdf'],
                                         max_segundos_pdf=o['max_segundos'])
        verbo = "Se cerraría" if o['listar'] else "Cerrado"
        for d in r['detalle']:
            if d['Resolucion']:
                self.stdout.write(f"  {verbo} {d['Resolucion']:<10} {d['Admision']} {d['TipoDocumento']:<12} "
                                  f"#{d['IdRevision']}: {d['Nota']}")
            elif o['requieren_ia']:
                self.stdout.write(f"  Sin cerrar           {d['Admision']} {d['TipoDocumento']:<12} "
                                  f"#{d['IdRevision']} ({d['Nota']}): {d['Descripcion'][:120]}")
        s = r['resumen']
        self.stdout.write(self.style.SUCCESS(
            f"Pendientes revisados: {s['revisados']} | corregidos en Zeus: {s['CORREGIDO']} | "
            f"descartados (la IA leyó mal): {s['DESCARTADO']} | PDF releídos: {s['pdf_leidos']} | "
            f"escaneados que requieren IA: {s['requieren_ia']} | archivo no encontrado: {s['sin_archivo']}"
            + (" | MODO LISTAR: no se guardó nada" if o['listar'] else "")))

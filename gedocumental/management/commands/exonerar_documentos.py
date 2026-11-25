"""
Admisiones que por directriz se cargan SIN orden, autorización ni comprobante: dejan de aparecer en "Documentos
faltantes" y no quedan como documentos incompletos en Cuentas Médicas ni en Gestión de facturación. El RESULTADO
se sigue exigiendo.

  python manage.py exonerar_documentos --admision 33782 33785 --usuario lmartinez --nota "Directriz FOMAG"
  python manage.py exonerar_documentos --listar
  python manage.py exonerar_documentos --quitar 33782
"""
from django.core.management.base import BaseCommand, CommandError

from gedocumental.models import AdmisionSinDocumentos


class Command(BaseCommand):
    help = "Marca admisiones que por directriz no llevan orden, autorización ni comprobante"

    def add_arguments(self, parser):
        parser.add_argument('--admision', nargs='+', type=int, default=[])
        parser.add_argument('--usuario', default='')
        parser.add_argument('--nota', default='')
        parser.add_argument('--quitar', nargs='+', type=int, default=[])
        parser.add_argument('--listar', action='store_true')

    def handle(self, *args, **o):
        if o['listar']:
            for x in AdmisionSinDocumentos.objects.order_by('AdmisionId'):
                self.stdout.write(f"{x.AdmisionId}  {x.Fecha:%Y-%m-%d %H:%M}  {x.Usuario}  {x.Nota}")
            return
        if o['quitar']:
            n, _ = AdmisionSinDocumentos.objects.filter(AdmisionId__in=o['quitar']).delete()
            self.stdout.write(f"Exoneraciones quitadas: {n}")
            return
        if not o['admision']:
            raise CommandError("Indique --admision, --quitar o --listar")
        if not o['nota']:
            raise CommandError("Indique --nota con la directriz (queda como soporte)")
        for adm in o['admision']:
            _, creada = AdmisionSinDocumentos.objects.update_or_create(
                AdmisionId=adm, defaults={'Usuario': o['usuario'], 'Nota': o['nota']})
            self.stdout.write(f"  {adm} {'exonerada' if creada else 'actualizada'}")

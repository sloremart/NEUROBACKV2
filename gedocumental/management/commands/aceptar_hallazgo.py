"""
Cierra a mano un hallazgo de la IA que SÍ es real pero se acepta así (ej. la orden del hospital trae el
nombre incompleto y no se puede pedir otra). Queda como ACEPTADO, con quién lo aceptó y por qué, y deja de
aparecer en "Hallazgos IA por corregir". Si después suben otro archivo, la nueva revisión manda.

Uso:
  python manage.py aceptar_hallazgo --admision 33391 --listar
  python manage.py aceptar_hallazgo --admision 33391 --tipo ORDEN --usuario "Lorena" \
      --nota "Orden del hospital con nombre incompleto; se acepta"
  python manage.py aceptar_hallazgo --id 12345 --usuario "Lorena" --nota "..."
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gedocumental.models import RevisionIA
from gedocumental.services.reverificacion import ACEPTADO


class Command(BaseCommand):
    help = "Acepta a mano (cierra como ACEPTADO) hallazgos pendientes de la IA"

    def add_arguments(self, parser):
        parser.add_argument('--admision', type=int)
        parser.add_argument('--tipo', help="Tipo de documento (ORDEN, AUTORIZACION...); por defecto todos")
        parser.add_argument('--id', type=int, help="IdRevision de un hallazgo puntual")
        parser.add_argument('--usuario', default='', help="Quién lo acepta (queda en la nota)")
        parser.add_argument('--nota', default='', help="Por qué se acepta")
        parser.add_argument('--listar', action='store_true', help="Solo muestra los pendientes; no guarda nada")

    def handle(self, *args, **o):
        if not o['admision'] and not o['id']:
            raise CommandError("Indique --admision o --id")
        qs = RevisionIA.objects.filter(Resuelta=False).exclude(TipoHallazgo='OK')
        if o['id']:
            qs = qs.filter(IdRevision=o['id'])
        if o['admision']:
            qs = qs.filter(AdmisionId=o['admision'])
        if o['tipo']:
            qs = qs.filter(TipoDocumento__iexact=o['tipo'])
        hallazgos = list(qs.order_by('IdRevision'))
        if not hallazgos:
            self.stdout.write(self.style.WARNING("No hay hallazgos pendientes con ese filtro"))
            return

        for h in hallazgos:
            self.stdout.write(f"  {h.AdmisionId} {h.TipoDocumento:<12} #{h.IdRevision}: {h.Descripcion[:150]}")
        if o['listar']:
            self.stdout.write(self.style.SUCCESS(f"{len(hallazgos)} pendiente(s) | MODO LISTAR: no se guardó nada"))
            return
        if not o['nota'].strip():
            raise CommandError("Indique --nota con el motivo por el que se acepta")

        nota = (f"Aceptado por {o['usuario'].strip()}: " if o['usuario'].strip() else "Aceptado: ") + o['nota'].strip()
        ahora = timezone.now()
        for h in hallazgos:
            h.Resuelta = True
            h.Resolucion = ACEPTADO
            h.FechaResolucion = ahora
            h.NotaResolucion = nota
            h.save(update_fields=['Resuelta', 'Resolucion', 'FechaResolucion', 'NotaResolucion'])
        self.stdout.write(self.style.SUCCESS(f"{len(hallazgos)} hallazgo(s) aceptado(s): {nota}"))

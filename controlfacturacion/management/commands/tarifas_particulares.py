"""
Tarifas del servicio PARTICULAR de los fisiatras (regla 2026-11-21). Se crean como tarifas 'Manual' (no se
borran al reimportar el Excel) y se pueden editar en la pestaña Tarifas.

  Willington Chona y Maritza Campo — paquete EMG + neuroconducción por extremidades:
      000006 (1 extremidad) $150.000 · 000005 (2) $180.000 · 000004 (4) $250.000
  Wilder Gómez — potenciales particulares: $150.000

Uso: python manage.py tarifas_particulares
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from controlfacturacion.models import TarifaHonorario
from controlfacturacion.services import honorarios as h
from controlfacturacion.views_auditoria import _medicos_activos, medicos_zeus

PAQUETES = [('6', 'PAQUETE 1 ELECTROMIOGRAFIA + 1 NEUROCONDUCCION (1 EXTREMIDAD)', 150000),
            ('5', 'PAQUETE 2 ELECTROMIOGRAFIAS + 2 NEUROCONDUCCIONES (2 EXTREMIDADES)', 180000),
            ('4', 'PAQUETE 4 ELECTROMIOGRAFIAS + 4 NEUROCONDUCCIONES (4 EXTREMIDADES)', 250000)]
POTENCIALES = [('954626', 'POTENCIALES EVOCADOS AUDITIVOS DE CORTA LATENCIA'),
               ('952303', 'POTENCIALES VISUALES EVOCADOS MULTIFOCALES'),
               ('952302', 'POTENCIALES VISUALES EVOCADOS MONOFOCALES'),
               ('891530', 'POTENCIALES EVOCADOS SOMATOSENSORIALES')]
TARIFAS = {
    'WILLINGTON CHONA': [(c, d, v) for c, d, v in PAQUETES],
    'MARITZA': [(c, d, v) for c, d, v in PAQUETES],
    'WILDER': [(c, d, 150000) for c, d in POTENCIALES],
}


class Command(BaseCommand):
    help = "Crea o actualiza las tarifas particulares de fisiatría (paquetes por extremidad y potenciales)"

    def handle(self, *args, **o):
        medicos, activos = medicos_zeus(), _medicos_activos()
        for nombre, tarifas in TARIFAS.items():
            cod = h.asociar_medico(nombre, medicos, activos)
            if cod is None:
                self.stderr.write(self.style.ERROR(f"No se encontró el médico '{nombre}' en Zeus"))
                continue
            completo = next(m['nombre'] for m in medicos if m['codigo'] == cod)
            for cups, descripcion, valor in tarifas:
                _, creada = TarifaHonorario.objects.update_or_create(
                    area='Manual', cod_medico=cod, cups=cups, grupo='PARTICULAR',
                    defaults={'encabezado': completo, 'medico': completo, 'descripcion': descripcion,
                              'tipo': 'FIJO', 'valor': Decimal(valor), 'por_unidad': False, 'activo': True,
                              'texto_original': 'Servicio particular (2026-11-21)'})
                self.stdout.write(f"{'Creada' if creada else 'Actualizada'}: {completo} · {cups} · ${valor:,}")

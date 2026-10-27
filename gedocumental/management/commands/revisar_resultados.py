"""
Control automático de los RESULTADOS (cargados por el sistema: importar_resultados,
generar_pdf_siesa, generar_pdfs). Pensado para ejecutarse por cron, después de
importar_resultados.

1. Revisa con IA los RESULTADOS nuevos o regenerados (los que no tienen revisión,
   o cuyo archivo es más reciente que su última revisión).
2. Registra el hallazgo "REPORTE: ausencia del reporte" en las admisiones que llevan
   DIAS_ESPERA_RESULTADO días o más desde el ingreso sin RESULTADO cargado.

Los hallazgos de archivos sin usuario y los de resultado faltante quedan a cargo de
USUARIO_COORDINACION_MEDICA_ID (se ven en el reporte Hallazgos IA).

Uso:
  python manage.py revisar_resultados                  # últimos 7 días de RESULTADOS + faltantes de 60 días
  python manage.py revisar_resultados --dias 30 --limite 200
  python manage.py revisar_resultados --solo-faltantes # no llama a OpenAI
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.models import Max, Min

from gedocumental.models import ArchivoFacturacion, RevisionIA
from gedocumental.services.ai_revision import (
    TIPOS_SIN_REVISION_IA, obtener_contexto_admision, reverificar_recaudo_con_zeus, revisar_archivo,
)
from gedocumental.services.reverificacion import reverificar_hallazgos_sin_ia

TIPO = 'RESULTADO'


class Command(BaseCommand):
    help = "Revisa con IA los RESULTADOS automáticos y registra los que faltan"

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=7, help="RESULTADOS cargados en los últimos N días")
        parser.add_argument('--limite', type=int, default=100, help="Máximo de RESULTADOS a revisar con IA")
        parser.add_argument('--ventana-faltantes', type=int, default=60,
                            help="Busca resultados faltantes en admisiones con documentos de los últimos N días")
        parser.add_argument('--solo-faltantes', action='store_true')

    def handle(self, *args, **o):
        # 0. Hallazgos que ya no aplican: corregidos en ZeusSalud o mal leídos por la IA (sin IA)
        r = reverificar_hallazgos_sin_ia()['resumen']
        self.stdout.write(f"Hallazgos cerrados sin IA: corregidos {r['CORREGIDO']}, descartados {r['DESCARTADO']} "
                          f"(PDF releídos {r['pdf_leidos']}, escaneados que requieren IA {r['requieren_ia']})")
        self.stdout.write(f"Hallazgos de recaudo corregidos en SIESA: {reverificar_recaudo_con_zeus()}")
        if not o['solo_faltantes']:
            self._revisar_resultados(o['dias'], o['limite'])
        self._registrar_faltantes(o['ventana_faltantes'])

    # ── 1. Revisión IA de RESULTADOS nuevos o regenerados ────────────────────
    def _revisar_resultados(self, dias, limite):
        desde = datetime.now() - timedelta(days=dias)
        candidatos = (
            ArchivoFacturacion.objects.filter(Tipo=TIPO, FechaCreacionArchivo__gte=desde)
            .annotate(ultima_revision=Max('RevisionesIA__Fecha'))
            .order_by('FechaCreacionArchivo')
        )
        pendientes = [a for a in candidatos if a.ultima_revision is None or a.ultima_revision < a.FechaCreacionArchivo]
        pendientes = pendientes[:limite]
        self.stdout.write(f"RESULTADOS por revisar con IA: {len(pendientes)}")

        resumen = {}
        for a in pendientes:
            res = revisar_archivo(a, obtener_contexto_admision(a.Admision_id))
            tipo = res['tipo'] if res else 'OMITIDO'
            resumen[tipo] = resumen.get(tipo, 0) + 1
            detalle = f" — {res['descripcion']}" if res and tipo != 'OK' else ''
            self.stdout.write(f"  {a.Admision_id} {tipo}{detalle}")
            self.stdout.flush()  # avance visible aunque se ejecute sin terminal (docker exec / cron)
        self.stdout.write(f"Resumen revisión: {resumen}")

    # ── 2. Admisiones sin RESULTADO después del plazo ────────────────────────
    def _registrar_faltantes(self, ventana):
        dias_espera = getattr(settings, 'DIAS_ESPERA_RESULTADO', 5)
        responsable = getattr(settings, 'USUARIO_COORDINACION_MEDICA_ID', None)
        ahora = datetime.now()

        # Admisiones que se están trabajando en GE Documental (con documentos distintos de FACTURA)
        admisiones = set(
            ArchivoFacturacion.objects.exclude(Tipo__in=['FACTURA', TIPO, *TIPOS_SIN_REVISION_IA])
            .values('Admision_id').annotate(primer=Min('FechaCreacionArchivo'))
            .filter(primer__gte=ahora - timedelta(days=ventana))
            .values_list('Admision_id', flat=True)
        )
        con_resultado = set(
            ArchivoFacturacion.objects.filter(Admision_id__in=admisiones, Tipo=TIPO)
            .values_list('Admision_id', flat=True)
        )

        # Cerrar los "ausencia del reporte" cuyo RESULTADO ya llegó (aunque la IA no lo haya revisado)
        cerrados = RevisionIA.objects.filter(
            TipoDocumento=TIPO, IdArchivo__isnull=True, Resuelta=False,
            AdmisionId__in=ArchivoFacturacion.objects.filter(Tipo=TIPO).values('Admision_id'),
        ).update(Resuelta=True, Resolucion='CORREGIDO', FechaResolucion=ahora)
        if cerrados:
            self.stdout.write(f"Resultados que ya llegaron (hallazgo cerrado): {cerrados}")
        ya_registradas = set(
            RevisionIA.objects.filter(AdmisionId__in=admisiones, TipoDocumento=TIPO,
                                      IdArchivo__isnull=True, Resuelta=False)
            .values_list('AdmisionId', flat=True)
        )
        sin_resultado = list(admisiones - con_resultado - ya_registradas)

        # Fecha de ingreso desde ZeusSalud
        fecha_ingreso = {}
        with connections['zeussalud'].cursor() as cursor:
            for i in range(0, len(sin_resultado), 500):
                bloque = sin_resultado[i:i + 500]
                marcadores = ', '.join(['%s'] * len(bloque))
                cursor.execute(
                    f"SELECT con_estudio, fecha_ing FROM sis_maes WHERE con_estudio IN ({marcadores})", bloque)
                fecha_ingreso.update({int(e): f for e, f in cursor.fetchall() if f})

        nuevos = []
        for adm in sin_resultado:
            ingreso = fecha_ingreso.get(adm)
            if not ingreso:
                continue
            dias = (ahora - ingreso).days
            if dias < dias_espera:
                continue
            nuevos.append(RevisionIA(
                IdArchivo=None,
                AdmisionId=adm,
                TipoDocumento=TIPO,
                UsuarioId=responsable,
                TipoHallazgo='REPORTE',
                Descripcion=f"Ausencia del reporte: {dias} días desde el ingreso ({ingreso:%Y-%m-%d}) sin RESULTADO cargado.",
                Modo='sistema',
                Resuelta=False,
            ))
        RevisionIA.objects.bulk_create(nuevos)
        self.stdout.write(
            f"Resultados faltantes (> {dias_espera} días): {len(nuevos)} nuevos | "
            f"{len(ya_registradas)} ya registrados | revisadas {len(admisiones)} admisiones"
        )

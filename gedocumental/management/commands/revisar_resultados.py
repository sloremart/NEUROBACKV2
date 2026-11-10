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

import re
from datetime import datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.models import Max, Min

from gedocumental.models import ArchivoFacturacion, RevisionIA
from gedocumental.services.ai_revision import (
    TIPOS_SIN_LLAMADA_IA, TIPOS_SIN_REVISION_IA, obtener_contexto_admision, reverificar_recaudo_con_zeus,
    _ia_disponible, revisar_archivo,
)
from gedocumental.services.reverificacion import reverificar_hallazgos_sin_ia
from gedocumental.views_siesa import (
    SinLecturaError, _fetch_pdf_siesa, _guardar_pdf, _registrar_en_bd, _copiar_a_examenes,
)

TIPO = 'RESULTADO'
# Minutos desde la carga antes de revisar un documento sin revisión IA (la revisión del cargue va primero)
MINUTOS_GRACIA = 15
# Hallazgo de admisión (sin archivo) por datos del paciente o de la orden incompletos en SIESA
TIPO_DATOS = 'DATOS_PACIENTE'
DATOS_INCOMPLETOS = 'Datos del paciente incompletos en SIESA'


class Command(BaseCommand):
    help = "Revisa con IA los RESULTADOS automáticos y registra los que faltan"

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=7, help="RESULTADOS cargados en los últimos N días")
        parser.add_argument('--limite', type=int, default=100, help="Máximo de RESULTADOS a revisar con IA")
        parser.add_argument('--ventana-faltantes', type=int, default=60,
                            help="Busca resultados faltantes en admisiones con documentos de los últimos N días")
        parser.add_argument('--solo-faltantes', action='store_true')
        parser.add_argument('--dias-siesa', type=int, default=7,
                            help="Días hacia atrás para buscar RESULTADOS de imagen posiblemente incompletos")

    def handle(self, *args, **o):
        # 0. Hallazgos que ya no aplican: corregidos en ZeusSalud o mal leídos por la IA (sin IA)
        r = reverificar_hallazgos_sin_ia()['resumen']
        self.stdout.write(f"Hallazgos cerrados sin IA: corregidos {r['CORREGIDO']}, descartados {r['DESCARTADO']} "
                          f"(PDF releídos {r['pdf_leidos']}, escaneados que requieren IA {r['requieren_ia']})")
        self.stdout.write(f"Hallazgos de recaudo corregidos en SIESA: {reverificar_recaudo_con_zeus()}")
        if not o['solo_faltantes']:
            self._regenerar_incompletos_siesa(o['dias_siesa'])
            self._revisar_resultados(o['dias'], o['limite'])
            self._revisar_sin_revision(o['dias'], o['limite'])
            self._revisar_procedimiento_cambiado(o['limite'])
        self._registrar_faltantes(o['ventana_faltantes'])
        self._registrar_sin_autorizacion(o['ventana_faltantes'])
        self._registrar_datos_incompletos(o['ventana_faltantes'])

    # ── 0. Regenerar RESULTADOS de imagen generados con informe incompleto ───
    def _regenerar_incompletos_siesa(self, dias):
        """
        Detecta RESULTADOS de estudios de imagen (CUPS 87/88/92) cuyo informe
        en detalleshc no tiene 'ATENDIDO POR:' (señal de que el médico no había
        terminado de dictar cuando se generó el PDF) y los regenera desde SIESA.
        """
        desde = datetime.now() - timedelta(days=dias)

        # IDs de admisiones con RESULTADO ya registrado en gedocumental
        admisiones_con_resultado = list(
            ArchivoFacturacion.objects.filter(Tipo=TIPO, FechaCreacionArchivo__gte=desde)
            .values_list('Admision_id', flat=True)
            .distinct()
        )
        if not admisiones_con_resultado:
            self.stdout.write("Regeneración SIESA: sin RESULTADOS recientes que verificar.")
            return

        # Buscar los que tienen informe incompleto en detalleshc
        placeholders = ', '.join(['%s'] * len(admisiones_con_resultado))
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT sd.estudio, MIN(sd.id) AS siesa_id
                FROM sis_deta sd
                WHERE sd.estudio IN ({placeholders})
                  AND LEFT(sd.cups, 2) IN ('87', '88', '92')
                  AND EXISTS (
                      SELECT 1 FROM detalleshc dh
                      WHERE dh.estudio = sd.estudio
                        AND (
                            dh.campo1 IS NULL
                            OR dh.campo1 NOT LIKE '%ATENDIDO POR:%'
                        )
                  )
                GROUP BY sd.estudio
                ORDER BY sd.estudio
                """,
                admisiones_con_resultado,
            )
            incompletos = cursor.fetchall()

        self.stdout.write(f"RESULTADOS imagen con informe incompleto (sin 'ATENDIDO POR:'): {len(incompletos)}")
        regenerados = errores = sin_lectura = 0

        for estudio, siesa_id in incompletos:
            try:
                pdf_bytes = _fetch_pdf_siesa(estudio, siesa_id)
                ruta_relativa, ruta_absoluta, nombre = _guardar_pdf(estudio, pdf_bytes)
                _registrar_en_bd(estudio, nombre, ruta_relativa)
                _copiar_a_examenes(ruta_absoluta, nombre)
                regenerados += 1
                self.stdout.write(f"  Regenerado: {estudio}")
            except SinLecturaError:
                sin_lectura += 1
                self.stdout.write(f"  {estudio}: aún sin lectura completa en SIESA")
            except Exception as e:
                errores += 1
                self.stdout.write(f"  {estudio}: error — {e}")
            self.stdout.flush()

        self.stdout.write(
            f"Regeneración completada — regenerados: {regenerados} | "
            f"aún incompletos en SIESA: {sin_lectura} | errores: {errores}"
        )

    # ── 1c. Hallazgos de procedimiento cuando la admisión se cambió en SIESA ─
    def _revisar_procedimiento_cambiado(self, limite):
        """
        Hallazgo "Procedimiento no coincide ... admisión 'X'" y la admisión ya no tiene X en Zeus (se cambió el
        procedimiento en SIESA): se revisa de nuevo el archivo con IA contra los datos actuales. Si ya cuadra, el
        hallazgo queda CORREGIDO; si no, REEMPLAZADO por el nuevo.
        """
        from gedocumental.services.reverificacion import procedimiento_cambio_en_zeus

        if not _ia_disponible():
            return
        pendientes = (RevisionIA.objects.filter(Resuelta=False, IdArchivo__isnull=False)
                      .exclude(TipoHallazgo='OK').select_related('IdArchivo').order_by('-Fecha'))
        contextos, revisados, resumen = {}, 0, {}
        for h in pendientes:
            if revisados >= limite:
                break
            if not re.search(r"admisi[oó]n(?:\s+esperaba)?\s*'", h.Descripcion or '', re.IGNORECASE):
                continue
            if h.AdmisionId not in contextos:
                contextos[h.AdmisionId] = obtener_contexto_admision(h.AdmisionId)
            if not procedimiento_cambio_en_zeus(h.Descripcion, contextos[h.AdmisionId]):
                continue
            res = revisar_archivo(h.IdArchivo, contextos[h.AdmisionId])
            revisados += 1
            tipo = res['tipo'] if res else 'OMITIDO'
            resumen[tipo] = resumen.get(tipo, 0) + 1
            self.stdout.write(f"  {h.AdmisionId} {h.TipoDocumento} procedimiento cambiado en SIESA → {tipo}")
        self.stdout.write(f"Hallazgos de procedimiento cambiado en SIESA revisados de nuevo: {revisados} {resumen or ''}")

    # ── 1b. Documentos subidos que quedaron sin revisión IA ──────────────────
    def _revisar_sin_revision(self, dias, limite):
        """
        Archivos cargados por los usuarios que no tienen revisión IA (la IA estaba apagada, sin clave, o la
        llamada falló al subirlos): se revisan aquí para que ninguno quede sin revisar. Se espera
        MINUTOS_GRACIA para no repetir la revisión que se hace al subir el archivo.
        """
        ahora = datetime.now()
        pendientes = (
            ArchivoFacturacion.objects.exclude(Tipo__in=[TIPO, *TIPOS_SIN_LLAMADA_IA])
            .filter(FechaCreacionArchivo__gte=ahora - timedelta(days=dias),
                    FechaCreacionArchivo__lte=ahora - timedelta(minutes=MINUTOS_GRACIA),
                    RevisionesIA__isnull=True)
            .order_by('-FechaCreacionArchivo')   # primero lo recién subido
        )
        total = pendientes.count()
        if total and not _ia_disponible():   # avisa por Telegram si está apagada o sin clave
            self.stderr.write(self.style.ERROR(f"ALERTA: {total} documentos sin revisión IA y la IA no está disponible"))
            return
        self.stdout.write(f"Documentos sin revisión IA: {total}")
        resumen, contextos = {}, {}
        for a in pendientes[:limite]:
            if a.Admision_id not in contextos:
                contextos[a.Admision_id] = obtener_contexto_admision(a.Admision_id)
            res = revisar_archivo(a, contextos[a.Admision_id])
            tipo = res['tipo'] if res else 'OMITIDO'
            resumen[tipo] = resumen.get(tipo, 0) + 1
            self.stdout.write(f"  {a.Admision_id} {a.Tipo} {tipo}")
            self.stdout.flush()
        if resumen:
            self.stdout.write(f"Resumen documentos sin revisión: {resumen}")

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
                # Las admisiones particulares no se validan: sin hallazgo de reporte faltante
                cursor.execute(
                    f"""SELECT sm.con_estudio, sm.fecha_ing FROM sis_maes sm
                        LEFT JOIN contratos co ON co.codigo = sm.contrato
                        WHERE sm.con_estudio IN ({marcadores})
                          AND UPPER(ISNULL(co.alias, '')) NOT LIKE '%%PARTICULAR%%'""", bloque)
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

    # ── 3. Admisiones sin número de autorización en SIESA ────────────────────
    def _registrar_sin_autorizacion(self, ventana):
        """
        En los contratos que exigen autorización (validacion_paciente.contratos_con_autorizacion; no aplica a
        particulares, FOMAG, Sanitas MRC...), registra el hallazgo "No cuenta con número de autorización en
        SIESA" a cargo de quien cargó la autorización (o el primer documento) de la admisión, para que salga
        en su bandeja de Hallazgos IA. Se cierra como CORREGIDO cuando el número se diligencia en SIESA.
        """
        from gedocumental.services.validacion_paciente import SIN_AUTORIZACION, estado_autorizacion

        ahora = datetime.now()
        cargues = {
            r['Admision_id']: r for r in
            ArchivoFacturacion.objects.exclude(Tipo__in=['FACTURA', TIPO, *TIPOS_SIN_REVISION_IA])
            .values('Admision_id').annotate(primer=Min('FechaCreacionArchivo'), usuario=Min('Usuario_id'))
            .filter(primer__gte=ahora - timedelta(days=ventana))
        }
        quien_autorizacion = dict(
            ArchivoFacturacion.objects.filter(Admision_id__in=cargues, Tipo='AUTORIZACION')
            .values('Admision_id').annotate(u=Min('Usuario_id')).values_list('Admision_id', 'u')
        )
        abiertos = RevisionIA.objects.filter(IdArchivo__isnull=True, TipoHallazgo='AUTORIZACION', Resuelta=False,
                                             Descripcion__startswith=SIN_AUTORIZACION)
        # Los abiertos se verifican siempre, aunque la admisión ya esté fuera de la ventana
        estados = estado_autorizacion(set(cargues) | set(abiertos.values_list('AdmisionId', flat=True)))
        sin = {adm for adm, e in estados.items() if e == 'SIN'}
        # Ya tienen número en SIESA → CORREGIDO; MRC, particular o admisión antigua → DESCARTADO (no aplica)
        cerrados = abiertos.filter(AdmisionId__in=[a for a, e in estados.items() if e == 'CON']).update(
            Resuelta=True, Resolucion='CORREGIDO', FechaResolucion=ahora,
            NotaResolucion='El número de autorización ya está diligenciado en SIESA.')
        cerrados += abiertos.filter(AdmisionId__in=[a for a, e in estados.items() if e == 'NO_APLICA']).update(
            Resuelta=True, Resolucion='DESCARTADO', FechaResolucion=ahora,
            NotaResolucion='No aplica: contrato sin número de autorización (MRC, particular...) o admisión antigua.')
        ya = set(abiertos.values_list('AdmisionId', flat=True))
        nuevos = [RevisionIA(
            IdArchivo=None, AdmisionId=adm, TipoDocumento='AUTORIZACION',
            UsuarioId=quien_autorizacion.get(adm) or cargues[adm]['usuario'],
            TipoHallazgo='AUTORIZACION', Modo='sistema', Resuelta=False,
            Descripcion=f"{SIN_AUTORIZACION}: diligencie el número de autorización de la EPS en la admisión.",
        ) for adm in (sin & set(cargues)) - ya]
        RevisionIA.objects.bulk_create(nuevos)
        self.stdout.write(f"Sin número de autorización en SIESA: {len(nuevos)} nuevos | {len(ya)} ya registrados | "
                          f"{cerrados} corregidos")

    # ── 4. Datos del paciente / de la orden incompletos en SIESA ─────────────
    def _registrar_datos_incompletos(self, ventana):
        """
        Los faltantes obligatorios de validacion_paciente (ocupación, zona, médico/IPS/fecha que ordena, nivel
        educativo...) dejan la admisión "con hallazgos" en la bandeja de Cuentas Médicas. Se registran como hallazgo
        a cargo de quien cargó los documentos, para que le salga en su bandeja de Hallazgos IA; la descripción se
        actualiza si cambian los campos y se cierra (CORREGIDO) cuando ya están completos en SIESA.
        No aplica a particulares (sus documentos no se validan) ni al número de autorización (tiene su hallazgo).
        """
        from gedocumental.services.validacion_paciente import SIN_AUTORIZACION, validar_datos_pacientes_bulk

        ahora = datetime.now()
        cargues = {
            r['Admision_id']: r for r in
            ArchivoFacturacion.objects.exclude(Tipo__in=['FACTURA', TIPO, *TIPOS_SIN_REVISION_IA])
            .values('Admision_id').annotate(primer=Min('FechaCreacionArchivo'), usuario=Min('Usuario_id'))
            .filter(primer__gte=ahora - timedelta(days=ventana))
        }
        abiertos = {h.AdmisionId: h for h in RevisionIA.objects.filter(
            IdArchivo__isnull=True, TipoDocumento=TIPO_DATOS, Resuelta=False)}
        ids = list(set(cargues) | set(abiertos))
        validaciones = validar_datos_pacientes_bulk(ids)
        particulares = set()
        with connections['zeussalud'].cursor() as cursor:
            for i in range(0, len(ids), 500):
                bloque = ids[i:i + 500]
                cursor.execute(
                    f"""SELECT sm.con_estudio FROM sis_maes sm JOIN contratos co ON co.codigo = sm.contrato
                        WHERE sm.con_estudio IN ({', '.join(['%s'] * len(bloque))})
                          AND UPPER(co.alias) LIKE '%%PARTICULAR%%'""", bloque)
                particulares.update(int(e) for (e,) in cursor.fetchall())

        nuevos, actualizados, cerrados = [], 0, 0
        for adm in ids:
            val = validaciones.get(adm)
            if val is None:
                continue   # Zeus no respondió: no se decide nada
            # Con el motivo cuando no es solo "sin diligenciar" (ej. "Fecha de ordenamiento: Posterior al ingreso")
            campos = [f['campo'] if str(f['motivo']).startswith('Sin diligenciar') else f"{f['campo']} ({f['motivo']})"
                      for f in val['faltantes'] if f['obligatorio'] and f['campo'] != SIN_AUTORIZACION]
            h = abiertos.get(adm)
            if not campos or adm in particulares:
                if h:
                    h.Resuelta, h.FechaResolucion = True, ahora
                    h.Resolucion = 'DESCARTADO' if adm in particulares else 'CORREGIDO'
                    h.NotaResolucion = ('Admisión particular: no se valida.' if adm in particulares
                                        else 'Los datos ya están completos en SIESA.')
                    h.save(update_fields=['Resuelta', 'Resolucion', 'FechaResolucion', 'NotaResolucion'])
                    cerrados += 1
                continue
            descripcion = (f"{DATOS_INCOMPLETOS}: {', '.join(campos)}. "
                           "Actualícelos en la ficha del paciente / admisión en SIESA.")
            if h:
                if h.Descripcion != descripcion:
                    h.Descripcion = descripcion
                    h.save(update_fields=['Descripcion'])
                    actualizados += 1
            elif adm in cargues:
                nuevos.append(RevisionIA(
                    IdArchivo=None, AdmisionId=adm, TipoDocumento=TIPO_DATOS, UsuarioId=cargues[adm]['usuario'],
                    TipoHallazgo='INFORMACION', Modo='sistema', Resuelta=False, Descripcion=descripcion))
        RevisionIA.objects.bulk_create(nuevos)
        self.stdout.write(f"Datos del paciente incompletos en SIESA: {len(nuevos)} nuevos | {actualizados} "
                          f"actualizados | {cerrados} cerrados")

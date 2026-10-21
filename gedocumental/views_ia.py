"""
Reporte de hallazgos de la revisión automática con IA (tabla revision_ia).

GET api/v2/gedocumental/hallazgos-ia/
  ?fecha_inicio=YYYY-MM-DD&fecha_fin=YYYY-MM-DD   (por fecha de la revisión)
  &estado=pendiente|resuelto|todos                 (default: pendiente)
  &tipo=INFORMACION                                (tipo de hallazgo)
  &usuario_id=3                                    (quien subió el archivo)
  &admision=17206
"""

from collections import Counter
from datetime import datetime

from django.db import connections
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from gedocumental.models import RevisionIA
from gedocumental.views import get_admisiones_zeus_bulk
from login.models import CustomUser


@api_view(['GET'])
def hallazgos_ia(request):
    try:
        p = request.query_params
        revisiones = RevisionIA.objects.all()

        if p.get('fecha_inicio'):
            revisiones = revisiones.filter(Fecha__date__gte=p['fecha_inicio'])
        if p.get('fecha_fin'):
            revisiones = revisiones.filter(Fecha__date__lte=p['fecha_fin'])
        if p.get('usuario_id'):
            revisiones = revisiones.filter(UsuarioId=p['usuario_id'])
        if p.get('admision'):
            revisiones = revisiones.filter(AdmisionId=p['admision'])

        # Resumen sobre todo lo revisado en el rango (incluye OK), para medir la tasa de hallazgos
        resumen_qs = revisiones.aggregate(
            revisados=Count('IdRevision'),
            con_hallazgo=Count('IdRevision', filter=~Q(TipoHallazgo='OK')),
            pendientes=Count('IdRevision', filter=~Q(TipoHallazgo='OK') & Q(Resuelta=False)),
        )

        hallazgos = revisiones.exclude(TipoHallazgo='OK')
        estado = p.get('estado', 'pendiente')
        if estado == 'pendiente':
            hallazgos = hallazgos.filter(Resuelta=False)
        elif estado == 'resuelto':
            hallazgos = hallazgos.filter(Resuelta=True)
        if p.get('tipo'):
            hallazgos = hallazgos.filter(TipoHallazgo=p['tipo'])

        hallazgos = list(hallazgos.order_by('-Fecha')[:2000])

        admision_ids = list({h.AdmisionId for h in hallazgos})
        with connections['zeussalud'].cursor() as cursor:
            admisiones = get_admisiones_zeus_bulk(cursor, admision_ids)
        usuarios = dict(
            CustomUser.objects.filter(id__in={h.UsuarioId for h in hallazgos if h.UsuarioId})
            .values_list('id', 'nombre')
        )

        ahora = datetime.now()
        data = []
        for h in hallazgos:
            adm = admisiones.get(h.AdmisionId)
            # Tiempo abierto: pendiente → hasta ahora; resuelto → hasta la corrección
            fin = h.FechaResolucion if h.Resuelta and h.FechaResolucion else ahora
            horas_abierto = max(0, int((fin - h.Fecha).total_seconds() // 3600))
            data.append({
                'IdRevision':      h.IdRevision,
                'IdArchivo':       h.IdArchivo_id,
                'Admision':        h.AdmisionId,
                'Paciente':        ' '.join((adm[3] or '').split()) if adm else '',
                'Identificacion':  adm[1] if adm else '',
                'Entidad':         (adm[2] or '').strip() if adm else '',
                'TipoDocumento':   h.TipoDocumento,
                'TipoHallazgo':    h.TipoHallazgo,
                'Descripcion':     h.Descripcion,
                'Modo':            h.Modo,
                'Usuario':         usuarios.get(h.UsuarioId, ''),
                'UsuarioId':       h.UsuarioId,
                'Fecha':           h.Fecha.isoformat(),
                'Resuelta':        h.Resuelta,
                'Resolucion':      h.Resolucion,
                'FechaResolucion': h.FechaResolucion.isoformat() if h.FechaResolucion else None,
                'HorasAbierto':    horas_abierto,
            })

        return Response({
            'success': True,
            'resumen': {
                **resumen_qs,
                'admisiones_afectadas': len(admision_ids),
                'por_tipo': dict(Counter(h.TipoHallazgo for h in hallazgos)),
            },
            'data': data,
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'success': False, 'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─────────────────────────────────────────────────────────────────────────────
# Bandeja de Cuentas Médicas: admisiones con documentos pendientes de aprobar,
# clasificadas según la pre-auditoría (documentos requeridos + IA). La FACTURA la carga
# Cuentas Médicas, por eso no cuenta para la pre-auditoría.
#
# GET api/v2/gedocumental/bandeja-cuentas-medicas/
#   ?fecha_inicio=YYYY-MM-DD&fecha_fin=YYYY-MM-DD   (fecha de cargue de documentos)
#   &estado=listas|documentos_incompletos|esperando_resultado|con_hallazgos|sin_revision_ia|todas
#       listas                 = pre-auditadas: tiene ORDEN, AUTORIZACION, COMPROBANTE y RESULTADO,
#                                todos los documentos (sin FACTURA) revisados por la IA y sin
#                                hallazgos pendientes (default)
#       documentos_incompletos = le falta ORDEN, AUTORIZACION o COMPROBANTE
#       esperando_resultado    = tiene los demás documentos, pero aún no el RESULTADO
#       con_hallazgos       = al menos un documento con hallazgo IA pendiente
#       sin_revision_ia     = sin hallazgos, pero la IA no revisó todos sus documentos
#   &paciente=completo|incompleto                    (opcional)
#   &entidad=EPSC34                                  (opcional, código de entidad)
# ─────────────────────────────────────────────────────────────────────────────
# Documentos sin los cuales una admisión no puede quedar pre-auditada
DOCUMENTOS_REQUERIDOS = ('ORDEN', 'AUTORIZACION', 'COMPROBANTE', 'RESULTADO')


@api_view(['GET'])
def bandeja_cuentas_medicas(request):
    from django.db.models import Max, Min
    from gedocumental.models import ArchivoFacturacion, ObservacionesArchivos
    from gedocumental.services.validacion_paciente import validar_datos_pacientes_bulk

    try:
        p = request.query_params
        pendientes_cm = ArchivoFacturacion.objects.filter(RevisionPrimera=False)
        if p.get('fecha_inicio'):
            pendientes_cm = pendientes_cm.filter(FechaCreacionArchivo__date__gte=p['fecha_inicio'])
        if p.get('fecha_fin'):
            pendientes_cm = pendientes_cm.filter(FechaCreacionArchivo__date__lte=p['fecha_fin'])
        # Documentos que se pre-auditan (todo menos la FACTURA)
        evaluados = pendientes_cm.exclude(Tipo='FACTURA')

        por_admision = {
            r['Admision_id']: r for r in pendientes_cm.values('Admision_id').annotate(
                ultimo_cargue=Max('FechaCreacionArchivo'),
                usuario_id=Min('Usuario_id'),
            )
        }
        ids = list(por_admision.keys())
        documentos = dict(
            evaluados.values('Admision_id').annotate(n=Count('IdArchivo')).values_list('Admision_id', 'n')
        )

        # Hallazgos IA pendientes sobre documentos (no cuenta el aviso de "resultado faltante",
        # que es para la coordinación médica)
        ia_pendientes = dict(
            RevisionIA.objects.filter(AdmisionId__in=ids, Resuelta=False, IdArchivo__isnull=False)
            .exclude(TipoHallazgo='OK')
            .values('AdmisionId').annotate(n=Count('IdRevision')).values_list('AdmisionId', 'n')
        )
        ia_revisados = dict(
            RevisionIA.objects.filter(AdmisionId__in=ids, IdArchivo__in=evaluados.values('IdArchivo'))
            .values('AdmisionId').annotate(n=Count('IdArchivo', distinct=True)).values_list('AdmisionId', 'n')
        )
        tipos_cargados = {}
        for adm_id, tipo in ArchivoFacturacion.objects.filter(
                Admision_id__in=ids, Tipo__in=list(DOCUMENTOS_REQUERIDOS) + ['FACTURA']
        ).values_list('Admision_id', 'Tipo'):
            tipos_cargados.setdefault(adm_id, set()).add(tipo)
        # Devueltas por Cuentas Médicas (observación de CM en un archivo aún no aprobado)
        devueltas = set(
            ObservacionesArchivos.objects.filter(
                ObservacionCuentasMedicas=True, IdArchivo__Admision_id__in=ids, IdArchivo__RevisionPrimera=False,
            ).values_list('IdArchivo__Admision_id', flat=True)
        )

        admisiones = {}
        contratos = {}  # nombre del contrato/EPS como aparece en Radicación
        with connections['zeussalud'].cursor() as cursor:
            for i in range(0, len(ids), 500):
                bloque = ids[i:i + 500]
                admisiones.update(get_admisiones_zeus_bulk(cursor, bloque))
                marcadores = ', '.join(['%s'] * len(bloque))
                cursor.execute(
                    f"""SELECT sm.con_estudio, LTRIM(RTRIM(ISNULL(c.alias, c.nombre)))
                        FROM sis_maes sm LEFT JOIN contratos c ON sm.contrato = c.codigo
                        WHERE sm.con_estudio IN ({marcadores})""",
                    bloque,
                )
                contratos.update({int(e): n for e, n in cursor.fetchall() if n})
        validaciones = validar_datos_pacientes_bulk(ids)
        usuarios = dict(
            CustomUser.objects.filter(id__in={r['usuario_id'] for r in por_admision.values() if r['usuario_id']})
            .values_list('id', 'nombre')
        )

        ahora = datetime.now()
        estado = p.get('estado', 'listas')
        filtro_paciente = p.get('paciente')
        filtro_entidad = (p.get('entidad') or '').strip()
        data = []
        cuenta = {'listas': 0, 'documentos_incompletos': 0, 'esperando_resultado': 0, 'con_hallazgos': 0,
                  'sin_revision_ia': 0, 'paciente_incompleto': 0}
        for adm_id, r in por_admision.items():
            adm = admisiones.get(adm_id)
            entidad = (adm[2] or '').strip() if adm else ''
            if filtro_entidad and entidad != filtro_entidad:
                continue
            docs = documentos.get(adm_id, 0)
            pendientes = ia_pendientes.get(adm_id, 0)
            revisados = ia_revisados.get(adm_id, 0)
            cargados = tipos_cargados.get(adm_id, set())
            tiene_resultado = 'RESULTADO' in cargados
            faltantes_doc = [t for t in DOCUMENTOS_REQUERIDOS if t not in cargados]
            val = validaciones.get(adm_id)
            completo = val['completo'] if val else None
            fecha_ing = adm[6] if adm else None

            if pendientes:
                estado_ia = 'con_hallazgos'
            elif [t for t in faltantes_doc if t != 'RESULTADO']:
                estado_ia = 'documentos_incompletos'
            elif not tiene_resultado:
                estado_ia = 'esperando_resultado'
            elif revisados >= docs:
                estado_ia = 'listas'
            else:
                estado_ia = 'sin_revision_ia'
            cuenta[estado_ia] += 1
            if completo is False:
                cuenta['paciente_incompleto'] += 1

            if estado != 'todas' and estado != estado_ia:
                continue
            if filtro_paciente == 'completo' and completo is not True:
                continue
            if filtro_paciente == 'incompleto' and completo is not False:
                continue

            data.append({
                'Admision':         adm_id,
                'Paciente':         ' '.join((adm[3] or '').split()) if adm else '',
                'Identificacion':   adm[1] if adm else '',
                'Entidad':          entidad,
                'Contrato':         contratos.get(adm_id) or entidad,
                'Documentos':       docs,
                'RevisadosIA':      revisados,
                'EstadoIA':         estado_ia,
                'HallazgosIA':      pendientes,
                'TieneResultado':   tiene_resultado,
                'DocumentosFaltantes': faltantes_doc,
                'TieneFactura':     'FACTURA' in cargados,
                'DiasDesdeIngreso': (ahora - fecha_ing).days if fecha_ing else None,
                'PacienteCompleto': completo,
                'DatosFaltantes':   [f['campo'] for f in val['faltantes'] if f['obligatorio']] if val else [],
                'DevueltaCM':       adm_id in devueltas,
                'UsuarioCargue':    usuarios.get(r['usuario_id'], ''),
                'UltimoCargue':     r['ultimo_cargue'].isoformat() if r['ultimo_cargue'] else None,
                'HorasEsperando':   max(0, int((ahora - r['ultimo_cargue']).total_seconds() // 3600)) if r['ultimo_cargue'] else 0,
            })

        data.sort(key=lambda x: x['UltimoCargue'] or '')  # las más antiguas primero
        return Response({'success': True, 'resumen': cuenta, 'data': data}, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({'success': False, 'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

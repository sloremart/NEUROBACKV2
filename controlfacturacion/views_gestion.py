"""
Gestión de casos de facturación — 3 bandejas operativas.

Endpoints:
  GET  /api/v2/gestion-facturacion/realizados/       → bandeja 1
  GET  /api/v2/gestion-facturacion/radicados/        → bandeja 2
  GET  /api/v2/gestion-facturacion/devoluciones/     → bandeja 3
  POST /api/v2/gestion-facturacion/devoluciones/     → registrar nueva devolución
  GET  /api/v2/gestion-facturacion/caso/<id>/        → detalle de un caso
  POST /api/v2/gestion-facturacion/caso/<id>/actualizar/ → cambiar estado/responsable/radicado/obs
"""

from datetime import datetime, date

from django.db import connections
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view

from gedocumental.models import ArchivoFacturacion
from .models import CasoGestion


# ── helpers ──────────────────────────────────────────────────────────────────

def _datos_zeus(admision_ids: list[int]) -> dict:
    """
    Consulta ZeusSalud por autoid (= Admision_id en gedocumental).
    Devuelve dict keyed by autoid.
    """
    if not admision_ids:
        return {}
    # IDs enteros de BD — seguros para interpolar directamente
    ids_str = ', '.join(str(int(x)) for x in admision_ids)
    with connections['zeussalud'].cursor() as cur:
        cur.execute(
            f"""
            SELECT
                sm.autoid,
                sm.con_estudio,
                LTRIM(RTRIM(COALESCE(sp.primer_nom,'') + ' ' + COALESCE(sp.segundo_nom,'') + ' '
                      + COALESCE(sp.primer_ape,'') + ' ' + COALESCE(sp.segundo_ape,''))) AS paciente,
                LTRIM(RTRIM(COALESCE(sp.num_id,'')))   AS documento,
                LTRIM(RTRIM(COALESCE(se.nombre,sm.EPSPaciente))) AS eps,
                CAST(sm.fecha_ing AS DATE)              AS fecha_ing,
                sm.tipoUsuario                          AS regimen,
                sm.Prefijo                              AS prefijo,
                sm.EPSPaciente                          AS cod_eps,
                sm.ufuncional                           AS ufuncional,
                LTRIM(RTRIM(COALESCE(uf.descripcion, CAST(sm.ufuncional AS VARCHAR(10)), 'Sin servicio'))) AS servicio_nombre,
                SUM(COALESCE(sd.total,0))               AS valor_total
            FROM sis_maes sm
            LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
            LEFT JOIN sis_empre se ON sm.EPSPaciente = se.codigo
            LEFT JOIN sis_deta sd ON sd.estudio = sm.con_estudio
            LEFT JOIN Ufuncionales uf ON uf.id = sm.ufuncional
            WHERE sm.autoid IN ({ids_str})
            GROUP BY sm.autoid, sm.con_estudio, sp.primer_nom, sp.segundo_nom,
                     sp.primer_ape, sp.segundo_ape, sp.num_id,
                     sm.EPSPaciente, se.nombre, sm.fecha_ing,
                     sm.tipoUsuario, sm.Prefijo, sm.ufuncional, uf.descripcion
            """
        )
        cols = [d[0] for d in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


_UFUNCIONAL_MAP = {
    1:  "Consulta Fisiatría 1ª vez",
    2:  "Radiología (RX)",
    3:  "Tomografía (TAC)",
    4:  "Resonancia Magnética",
    5:  "Mamografía",
    6:  "Ecografía",
    7:  "Consulta Fisiatría Control",
    8:  "Consulta Neurología 1ª vez",
    9:  "Consulta Neurología Control",
    10: "Consulta Neuropediatría 1ª vez",
    11: "Consulta Neuropediatría Control",
    12: "Administración de Medicamentos",
    13: "Polisomnografía",
    14: "EEG / Videotelemetría",
    15: "Procedimientos Fisiatría",
    16: "Procedimientos Neurología",
    17: "Soporte Sedación",
    18: "PET / CT",
}

def _servicio_nombre(ufuncional, nombre_bd=None) -> str:
    if nombre_bd:
        return nombre_bd
    if ufuncional is None:
        return "Sin servicio"
    return _UFUNCIONAL_MAP.get(int(ufuncional), f"Servicio {ufuncional}")


def _caso_o_none(admision_id: int, bandeja: str) -> CasoGestion | None:
    return CasoGestion.objects.filter(admision_id=admision_id, bandeja=bandeja).first()


def _enrich(admision_id: int, bandeja: str, zeus: dict, archivos: dict) -> dict:
    """Combina datos Zeus + gedocumental + CasoGestion en un dict para el frontend."""
    z = zeus.get(admision_id, {})
    caso = _caso_o_none(admision_id, bandeja)
    arcs = archivos.get(admision_id, {})

    dias = None
    if caso:
        dias = (timezone.now() - caso.fecha_ingreso_bandeja).days
    elif z.get('fecha_ing'):
        fi = z['fecha_ing']
        dias = (date.today() - fi).days if isinstance(fi, date) else None

    uf = z.get('ufuncional')
    servicio = _servicio_nombre(uf, z.get('servicio_nombre'))

    return {
        'admision_id':           admision_id,
        'con_estudio':           z.get('con_estudio', admision_id),
        'servicio':              servicio,
        'ufuncional':            uf,
        'paciente':              z.get('paciente', ''),
        'documento':             z.get('documento', ''),
        'eps':                   z.get('eps', ''),
        'fecha_ingreso':         z.get('fecha_ing').isoformat() if z.get('fecha_ing') else None,
        'regimen':               z.get('regimen', ''),
        'valor_total':           float(z.get('valor_total') or 0),
        'dias_pendiente':        dias,
        # archivos presentes
        'tiene_resultado':       bool(arcs.get('RESULTADO')),
        'tiene_orden':           bool(arcs.get('ORDEN')),
        'tiene_autorizacion':    bool(arcs.get('AUTORIZACION')),
        'tiene_comprobante':     bool(arcs.get('COMPROBANTE')),
        'tiene_factura':         bool(arcs.get('FACTURA')),
        'factura_nombre':        (arcs.get('FACTURA') or [None])[0],
        'radicado':              arcs.get('radicado', False),
        # gestión
        'caso_id':               caso.pk if caso else None,
        'estado':                caso.estado if caso else 'pendiente',
        'responsable_id':        caso.responsable_id if caso else None,
        'responsable_nombre':    caso.responsable.get_full_name() if (caso and caso.responsable) else '',
        'numero_radicado':       caso.numero_radicado if caso else '',
        'fecha_radicado':        caso.fecha_radicado.isoformat() if (caso and caso.fecha_radicado) else None,
        'motivo_devolucion':     caso.motivo_devolucion if caso else '',
        'observacion':           caso.observacion if caso else '',
        'fecha_ingreso_bandeja': caso.fecha_ingreso_bandeja.isoformat() if caso else None,
    }


def _archivos_por_admision(admision_ids: list[int]) -> dict:
    """
    Devuelve dict {admision_id: {TIPO: [nombre,...], 'radicado': bool}}
    """
    result: dict = {}
    qs = (
        ArchivoFacturacion.objects
        .filter(Admision_id__in=admision_ids)
        .values('Admision_id', 'Tipo', 'NombreArchivo', 'Radicado')
    )
    for row in qs:
        a = result.setdefault(row['Admision_id'], {})
        a.setdefault(row['Tipo'], []).append(row['NombreArchivo'])
        if row['Radicado']:
            a['radicado'] = True
    return result


# ── Bandeja 1: Realizados sin factura ────────────────────────────────────────

def _zeus_autoids_en_rango(autoids: list[int], fecha_inicio: str, fecha_fin: str,
                           excluir_particulares: bool = False) -> list[int]:
    """
    Filtra una lista de autoids cruzando con sis_maes.
    Si no se pasan fechas usa los últimos 90 días.
    Siempre excluye admisiones que no existen en Zeus.
    """
    from datetime import timedelta
    if not autoids:
        return []
    ids_str = ', '.join(str(int(x)) for x in autoids)

    if not fecha_inicio:
        fecha_inicio = (date.today() - timedelta(days=90)).isoformat()
    if not fecha_fin:
        fecha_fin = date.today().isoformat()

    extra = ""
    if excluir_particulares:
        extra = "AND sm.Prefijo != 'MGL' AND sm.EPSPaciente != 'SAN02'"

    with connections['zeussalud'].cursor() as cur:
        cur.execute(
            f"""
            SELECT sm.autoid
            FROM sis_maes sm
            WHERE sm.autoid IN ({ids_str})
              AND CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
              {extra}
            """,
            [fecha_inicio, fecha_fin],
        )
        return [r[0] for r in cur.fetchall()]


@api_view(['GET'])
def bandeja_realizados(request):
    """Estudios con RESULTADO en gedocumental pero sin FACTURA."""
    fecha_inicio = request.GET.get('fecha_inicio', '')
    fecha_fin    = request.GET.get('fecha_fin', '')

    con_resultado = set(
        ArchivoFacturacion.objects.filter(Tipo='RESULTADO')
        .values_list('Admision_id', flat=True).distinct()
    )
    con_factura = set(
        ArchivoFacturacion.objects.filter(Tipo='FACTURA')
        .values_list('Admision_id', flat=True).distinct()
    )
    candidatos = list(con_resultado - con_factura)

    if not candidatos:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    # Cruzar con ZeusSalud: solo admisiones que existen y en el rango de fechas
    autoids = _zeus_autoids_en_rango(candidatos, fecha_inicio, fecha_fin)

    if not autoids:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    zeus     = _datos_zeus(autoids)
    archivos = _archivos_por_admision(autoids)

    existentes = set(
        CasoGestion.objects.filter(admision_id__in=autoids, bandeja='realizados')
        .values_list('admision_id', flat=True)
    )
    nuevos = [CasoGestion(admision_id=aid, bandeja='realizados')
              for aid in autoids if aid not in existentes]
    if nuevos:
        CasoGestion.objects.bulk_create(nuevos, ignore_conflicts=True)

    data = [_enrich(aid, 'realizados', zeus, archivos) for aid in autoids]
    data.sort(key=lambda x: x['dias_pendiente'] or 0, reverse=True)
    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


# ── Bandeja 2: Facturados sin radicar ────────────────────────────────────────

@api_view(['GET'])
def bandeja_radicados(request):
    """Estudios con FACTURA en gedocumental pero Radicado=False. Excluye particulares."""
    fecha_inicio = request.GET.get('fecha_inicio', '')
    fecha_fin    = request.GET.get('fecha_fin', '')

    candidatos = list(
        ArchivoFacturacion.objects.filter(Tipo='FACTURA', Radicado=False)
        .values_list('Admision_id', flat=True).distinct()
    )

    if not candidatos:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    # Cruzar con Zeus, excluir particulares (MGL/SAN02)
    autoids = _zeus_autoids_en_rango(candidatos, fecha_inicio, fecha_fin,
                                     excluir_particulares=True)

    if not autoids:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    zeus     = _datos_zeus(autoids)
    archivos = _archivos_por_admision(autoids)

    existentes = set(
        CasoGestion.objects.filter(admision_id__in=autoids, bandeja='radicados')
        .values_list('admision_id', flat=True)
    )
    nuevos = [CasoGestion(admision_id=aid, bandeja='radicados')
              for aid in autoids if aid not in existentes]
    if nuevos:
        CasoGestion.objects.bulk_create(nuevos, ignore_conflicts=True)

    data = [_enrich(aid, 'radicados', zeus, archivos) for aid in autoids]
    data.sort(key=lambda x: x['dias_pendiente'] or 0, reverse=True)

    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


# ── Bandeja 3: Devoluciones ───────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def bandeja_devoluciones(request):
    if request.method == 'POST':
        return _registrar_devolucion(request)

    casos = CasoGestion.objects.filter(bandeja='devoluciones').order_by('-fecha_ingreso_bandeja')
    admision_ids = list(casos.values_list('admision_id', flat=True))

    zeus    = _datos_zeus(admision_ids)
    archivos = _archivos_por_admision(admision_ids)

    data = [_enrich(aid, 'devoluciones', zeus, archivos) for aid in admision_ids]
    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


def _registrar_devolucion(request):
    body = request.data
    admision_id = body.get('admision_id')
    if not admision_id:
        return JsonResponse({'success': False, 'detail': 'admision_id requerido'}, status=400)

    caso, _ = CasoGestion.objects.get_or_create(
        admision_id=admision_id,
        bandeja='devoluciones',
        defaults={
            'estado':           'pendiente',
            'motivo_devolucion': body.get('motivo_devolucion', ''),
            'observacion':       body.get('observacion', ''),
        },
    )
    if body.get('motivo_devolucion'):
        caso.motivo_devolucion = body['motivo_devolucion']
    if body.get('observacion'):
        caso.observacion = body['observacion']
    caso.save()

    return JsonResponse({'success': True, 'caso_id': caso.pk})


# ── Detalle + actualización de un caso ───────────────────────────────────────

@api_view(['GET'])
def detalle_caso(request, admision_id: int):
    """Detalle completo de un caso: datos Zeus + todos sus archivos."""
    zeus    = _datos_zeus([admision_id])
    archivos = _archivos_por_admision([admision_id])

    arcs = archivos.get(admision_id, {})
    z    = zeus.get(admision_id, {})

    # Archivos por tipo con sus IDs para poder abrirlos
    qs = ArchivoFacturacion.objects.filter(Admision_id=admision_id).order_by('Tipo', 'IdArchivo')
    archivos_detalle = []
    for a in qs:
        archivos_detalle.append({
            'id':     a.IdArchivo,
            'tipo':   a.Tipo,
            'nombre': a.NombreArchivo,
            'fecha':  a.FechaCreacionArchivo.isoformat() if a.FechaCreacionArchivo else None,
        })

    return JsonResponse({
        'success': True,
        'admision_id': admision_id,
        'zeus': {
            'paciente':    z.get('paciente', ''),
            'documento':   z.get('documento', ''),
            'eps':         z.get('eps', ''),
            'fecha_ing':   z.get('fecha_ing').isoformat() if z.get('fecha_ing') else None,
            'regimen':     z.get('regimen', ''),
            'valor_total': float(z.get('valor_total') or 0),
        },
        'archivos': archivos_detalle,
    })


@api_view(['POST'])
def actualizar_caso(request, admision_id: int):
    """
    Actualiza estado, responsable, radicado, observación de un caso.
    Body: { bandeja, estado?, responsable_id?, numero_radicado?, fecha_radicado?,
            motivo_devolucion?, observacion? }
    """
    body   = request.data
    bandeja = body.get('bandeja')
    if not bandeja:
        return JsonResponse({'success': False, 'detail': 'bandeja requerida'}, status=400)

    caso, _ = CasoGestion.objects.get_or_create(admision_id=admision_id, bandeja=bandeja)

    if 'estado' in body:
        caso.estado = body['estado']
        if body['estado'] == 'resuelto' and not caso.fecha_resolucion:
            caso.fecha_resolucion = timezone.now()
            caso.usuario_resolvio = request.user if request.user.is_authenticated else None

    if 'responsable_id' in body:
        caso.responsable_id = body['responsable_id'] or None

    if 'numero_radicado' in body:
        caso.numero_radicado = body['numero_radicado']
        if body['numero_radicado'] and not caso.fecha_radicado:
            caso.fecha_radicado = date.today()
            caso.usuario_radico = request.user if request.user.is_authenticated else None

    if 'fecha_radicado' in body and body['fecha_radicado']:
        caso.fecha_radicado = body['fecha_radicado']

    if 'motivo_devolucion' in body:
        caso.motivo_devolucion = body['motivo_devolucion']

    if 'observacion' in body:
        caso.observacion = body['observacion']

    caso.save()
    return JsonResponse({'success': True, 'caso_id': caso.pk, 'estado': caso.estado})

"""
Gestión de casos de facturación — bandejas operativas y represamientos.

Endpoints:
  GET  /api/v2/gestion-facturacion/realizados/       → bandeja 1
  GET  /api/v2/gestion-facturacion/radicados/        → bandeja 2
  GET  /api/v2/gestion-facturacion/devoluciones/     → bandeja 3
  POST /api/v2/gestion-facturacion/devoluciones/     → registrar nueva devolución
  GET  /api/v2/gestion-facturacion/caso/<id>/        → detalle de un caso
  POST /api/v2/gestion-facturacion/caso/<id>/actualizar/ → cambiar estado/responsable/radicado/obs
  GET  /api/v2/gestion-facturacion/represamiento-admisiones/
  GET  /api/v2/gestion-facturacion/represamiento-cuentas/

Notas:
  - Admision_id en gedocumental = sis_maes.con_estudio en ZeusSalud (NO sis_maes.autoid, que es el paciente).
  - Sin rango de fechas, las bandejas arrancan en la fecha en que empezó la revisión con IA
    (settings.GESTION_FECHA_INICIO, por defecto 2026-10-20).
  - MRC y PARTICULAR no se radican por admisión: no aparecen en las bandejas de radicación.
  - Consultar (GET) no crea registros: el CasoGestion se crea al gestionar el caso (actualizar/devolución).
"""

import re
from collections import defaultdict
from datetime import date, datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.db.models import Min
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view

from gedocumental.models import ArchivoFacturacion
from gedocumental.services.ai_revision import _reparar_texto
from gedocumental.views_ia import procedimientos_por_admision
from .models import CasoGestion


FECHA_INICIO_DEFECTO = getattr(settings, 'GESTION_FECHA_INICIO', '2026-10-20')
# Contratos que no se radican por admisión (el alias en Zeus contiene alguno de estos textos)
CONTRATOS_SIN_RADICACION = ('MRC', 'PARTICULAR')
# Hallazgos que corrige el admisionista (documentos que carga y datos del paciente en SIESA).
# El RESULTADO es de la coordinación médica; HC y adicionales no se revisan.
TIPOS_ADMISIONES = ('ORDEN', 'AUTORIZACION', 'COMPROBANTE', 'DATOS_PACIENTE')
TIPO_COORDINACION = 'RESULTADO'
DIAS_VENCIDO = 30
_BLOQUE = 1000


# ── helpers ──────────────────────────────────────────────────────────────────

def _rango(request) -> tuple[str, str]:
    fecha_inicio = request.GET.get('fecha_inicio') or FECHA_INICIO_DEFECTO
    fecha_fin = request.GET.get('fecha_fin') or date.today().isoformat()
    return fecha_inicio, fecha_fin


def _a_fecha(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return None


def _dias_desde(valor) -> int | None:
    f = _a_fecha(valor)
    return (date.today() - f).days if f else None


def _bloques(ids):
    ids = [int(x) for x in ids if x]
    for i in range(0, len(ids), _BLOQUE):
        yield ids[i:i + _BLOQUE]


def _datos_zeus(admision_ids) -> dict:
    """Datos de ZeusSalud por con_estudio (= Admision_id en gedocumental). Dict keyed by con_estudio."""
    resultado: dict = {}
    with connections['zeussalud'].cursor() as cur:
        for bloque in _bloques(admision_ids):
            ids_str = ', '.join(str(x) for x in bloque)   # enteros: seguros para interpolar
            cur.execute(
                f"""
                SELECT
                    sm.con_estudio,
                    LTRIM(RTRIM(COALESCE(sp.primer_nom,'') + ' ' + COALESCE(sp.segundo_nom,'') + ' '
                          + COALESCE(sp.primer_ape,'') + ' ' + COALESCE(sp.segundo_ape,''))) AS paciente,
                    LTRIM(RTRIM(COALESCE(sp.num_id,'')))                   AS documento,
                    LTRIM(RTRIM(COALESCE(se.nombre, sm.EPSPaciente, '')))  AS eps,
                    LTRIM(RTRIM(COALESCE(NULLIF(LTRIM(RTRIM(ct.alias)), ''), ct.nombre, ''))) AS contrato,
                    LTRIM(RTRIM(COALESCE(m.especialidad, '')))             AS especialidad,
                    CAST(sm.fecha_ing AS DATE)                              AS fecha_ing,
                    sm.tipoUsuario                                          AS regimen,
                    sm.EPSPaciente                                          AS cod_eps,
                    sm.ufuncional                                           AS ufuncional,
                    LTRIM(RTRIM(COALESCE(uf.descripcion, ''))) AS servicio_nombre,
                    (SELECT SUM(COALESCE(sd.total, 0)) FROM sis_deta sd
                      WHERE sd.estudio = sm.con_estudio)                    AS valor_total
                FROM sis_maes sm
                LEFT JOIN sis_paci sp     ON sm.autoid = sp.autoid
                LEFT JOIN contratos ct    ON sm.contrato = ct.codigo
                LEFT JOIN sis_empre se    ON sm.EPSPaciente = se.codigo
                LEFT JOIN Ufuncionales uf ON uf.id = sm.ufuncional
                LEFT JOIN Medicos m       ON m.codigo = sm.cod_medico
                WHERE sm.con_estudio IN ({ids_str})
                """
            )
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                fila = dict(zip(cols, r))
                fila['paciente'] = _reparar_texto(' '.join((fila['paciente'] or '').split()))
                fila['eps'] = _reparar_texto(fila['eps'] or '')
                fila['contrato'] = _reparar_texto(fila['contrato'] or '')
                resultado[int(r[0])] = fila

    # Procedimientos (CUPS) de cada admisión: desglosan el servicio (consulta 1ª vez/control por especialidad)
    procedimientos = procedimientos_por_admision(list(resultado.keys())) if resultado else {}
    for aid, fila in resultado.items():
        fila['procedimientos'] = procedimientos.get(aid, '')
        fila['servicio_nombre'] = _servicio_detallado(fila)
    return resultado


# Consultas: CUPS 8902xx = primera vez, 8903xx = control; los dos últimos dígitos dan la especialidad
_ESPECIALIDAD_CONSULTA = {
    '74': 'NEUROLOGÍA',
    '75': 'NEUROPEDIATRÍA',
    '64': 'FISIATRÍA',
}


def _servicio_detallado(fila: dict) -> str:
    """Servicio de la admisión lo más desglosado posible (consulta externa por especialidad y 1ª vez/control)."""
    base = (fila.get('servicio_nombre') or '').strip()
    if fila.get('ufuncional') != 1 and 'CONSULTA' not in base.upper():
        return base or _servicio_nombre(fila.get('ufuncional'))
    codigos = re.findall(r'(?:^|;\s*)(890[23]\d\d)\b', fila.get('procedimientos') or '')
    if codigos:
        c = codigos[0]
        especialidad = _ESPECIALIDAD_CONSULTA.get(c[-2:]) or (fila.get('especialidad') or '').upper() or 'ESPECIALISTA'
        return f"CONSULTA {especialidad} {'1ª VEZ' if c[3] == '2' else 'CONTROL'}"
    if fila.get('especialidad'):
        esp = fila['especialidad'].upper()
        nombre = ('NEUROPEDIATRÍA' if 'PEDIATR' in esp else 'NEUROLOGÍA' if 'NEUROLOG' in esp
                  else 'FISIATRÍA' if 'REHABILITACI' in esp or 'FISIATR' in esp else esp)
        return f"CONSULTA {nombre}"
    return base or 'CONSULTA EXTERNA'


def _zeus_en_rango(admision_ids, fecha_inicio: str, fecha_fin: str,
                   excluir_sin_radicacion: bool = False) -> list[int]:
    """
    Admisiones (con_estudio) que existen en Zeus con fecha de ingreso en el rango.
    excluir_sin_radicacion: quita los contratos MRC y PARTICULAR (no se radican por admisión).
    """
    extra = ''
    if excluir_sin_radicacion:
        extra = ' '.join(
            f"AND UPPER(ISNULL(ct.alias, '') + ' ' + ISNULL(ct.nombre, '')) NOT LIKE '%%{c}%%'"
            for c in CONTRATOS_SIN_RADICACION
        )
    encontrados: list[int] = []
    with connections['zeussalud'].cursor() as cur:
        for bloque in _bloques(admision_ids):
            ids_str = ', '.join(str(x) for x in bloque)
            cur.execute(
                f"""
                SELECT sm.con_estudio
                FROM sis_maes sm
                LEFT JOIN contratos ct ON sm.contrato = ct.codigo
                WHERE sm.con_estudio IN ({ids_str})
                  AND CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
                  {extra}
                """,
                [fecha_inicio, fecha_fin],
            )
            encontrados.extend(int(r[0]) for r in cur.fetchall())
    return encontrados


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


def _nombre_usuario(u) -> str:
    if not u:
        return ''
    return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username


def _casos_por_admision(admision_ids, bandeja: str) -> dict:
    return {
        c.admision_id: c
        for c in CasoGestion.objects.filter(admision_id__in=admision_ids, bandeja=bandeja)
        .select_related('responsable')
    }


def _enrich(admision_id: int, zeus: dict, archivos: dict, caso: CasoGestion | None,
            dias: int | None) -> dict:
    """Combina datos Zeus + gedocumental + CasoGestion en un dict para el frontend."""
    z = zeus.get(admision_id, {})
    arcs = archivos.get(admision_id, {})
    uf = z.get('ufuncional')

    return {
        'admision_id':           admision_id,
        'con_estudio':           admision_id,
        'servicio':              _servicio_nombre(uf, z.get('servicio_nombre')),
        'ufuncional':            uf,
        'paciente':              z.get('paciente', ''),
        'documento':             z.get('documento', ''),
        'eps':                   z.get('eps', ''),
        'contrato':              z.get('contrato', ''),
        'procedimientos':        z.get('procedimientos', ''),
        'fecha_ingreso':         z['fecha_ing'].isoformat() if z.get('fecha_ing') else None,
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
        'responsable_nombre':    _nombre_usuario(caso.responsable) if caso else '',
        'numero_radicado':       caso.numero_radicado if caso else '',
        'fecha_radicado':        caso.fecha_radicado.isoformat() if (caso and caso.fecha_radicado) else None,
        'motivo_devolucion':     caso.motivo_devolucion if caso else '',
        'observacion':           caso.observacion if caso else '',
        'fecha_ingreso_bandeja': caso.fecha_ingreso_bandeja.isoformat() if caso else None,
    }


def _archivos_por_admision(admision_ids) -> dict:
    """Devuelve dict {admision_id: {TIPO: [nombre,...], 'radicado': bool}}"""
    result: dict = {}
    qs = (
        ArchivoFacturacion.objects
        .filter(Admision_id__in=list(admision_ids))
        .values('Admision_id', 'Tipo', 'NombreArchivo', 'Radicado')
    )
    for row in qs:
        a = result.setdefault(row['Admision_id'], {})
        a.setdefault((row['Tipo'] or '').upper(), []).append(row['NombreArchivo'])
        if row['Radicado']:
            a['radicado'] = True
    return result


def _fecha_factura(admision_ids) -> dict:
    """{admision_id: fecha en que se cargó la primera FACTURA}"""
    return dict(
        ArchivoFacturacion.objects.filter(Tipo='FACTURA', Admision_id__in=list(admision_ids))
        .values('Admision_id').annotate(f=Min('FechaCreacionArchivo')).values_list('Admision_id', 'f')
    )


def _facturas_sin_radicar(fecha_inicio: str, fecha_fin: str) -> list[int]:
    """Admisiones con FACTURA sin radicar, existentes en Zeus en el rango, sin MRC ni particulares."""
    candidatos = set(
        ArchivoFacturacion.objects.filter(Tipo='FACTURA', Radicado=False,
                                          FechaCreacionArchivo__date__gte=fecha_inicio)
        .values_list('Admision_id', flat=True)
    )
    # Si algún archivo de la admisión quedó radicado, la admisión ya se radicó
    radicadas = set(
        ArchivoFacturacion.objects.filter(Admision_id__in=candidatos, Radicado=True)
        .values_list('Admision_id', flat=True)
    )
    return _zeus_en_rango(candidatos - radicadas, fecha_inicio, fecha_fin, excluir_sin_radicacion=True)


# ── Bandeja 1: Realizados sin factura ────────────────────────────────────────

@api_view(['GET'])
def bandeja_realizados(request):
    """Estudios con RESULTADO en gedocumental pero sin FACTURA (días desde el ingreso en Zeus)."""
    fecha_inicio, fecha_fin = _rango(request)

    # El RESULTADO se carga después del ingreso: basta mirar los cargados desde fecha_inicio
    con_resultado = set(
        ArchivoFacturacion.objects.filter(Tipo='RESULTADO', FechaCreacionArchivo__date__gte=fecha_inicio)
        .values_list('Admision_id', flat=True)
    )
    con_factura = set(
        ArchivoFacturacion.objects.filter(Tipo='FACTURA', Admision_id__in=con_resultado)
        .values_list('Admision_id', flat=True)
    )
    ids = _zeus_en_rango(con_resultado - con_factura, fecha_inicio, fecha_fin)
    if not ids:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    zeus = _datos_zeus(ids)
    archivos = _archivos_por_admision(ids)
    casos = _casos_por_admision(ids, 'realizados')

    data = [_enrich(aid, zeus, archivos, casos.get(aid), _dias_desde(zeus.get(aid, {}).get('fecha_ing')))
            for aid in ids]
    data.sort(key=lambda x: x['dias_pendiente'] or 0, reverse=True)
    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


# ── Bandeja 2: Facturados sin radicar ────────────────────────────────────────

@api_view(['GET'])
def bandeja_radicados(request):
    """Estudios con FACTURA sin radicar (días desde el cargue de la factura). Sin MRC ni particulares."""
    fecha_inicio, fecha_fin = _rango(request)
    ids = _facturas_sin_radicar(fecha_inicio, fecha_fin)
    if not ids:
        return JsonResponse({'success': True, 'data': [], 'total': 0})

    zeus = _datos_zeus(ids)
    archivos = _archivos_por_admision(ids)
    casos = _casos_por_admision(ids, 'radicados')
    fechas_factura = _fecha_factura(ids)

    data = [_enrich(aid, zeus, archivos, casos.get(aid), _dias_desde(fechas_factura.get(aid)))
            for aid in ids]
    data.sort(key=lambda x: x['dias_pendiente'] or 0, reverse=True)
    return JsonResponse({'success': True, 'data': data, 'total': len(data)})


# ── Bandeja 3: Devoluciones ───────────────────────────────────────────────────

@api_view(['GET', 'POST'])
def bandeja_devoluciones(request):
    if request.method == 'POST':
        return _registrar_devolucion(request)

    casos = {
        c.admision_id: c
        for c in CasoGestion.objects.filter(bandeja='devoluciones').select_related('responsable')
    }
    ids = list(casos.keys())
    zeus = _datos_zeus(ids)
    archivos = _archivos_por_admision(ids)

    data = [_enrich(aid, zeus, archivos, c, _dias_desde(c.fecha_ingreso_bandeja)) for aid, c in casos.items()]
    data.sort(key=lambda x: x['dias_pendiente'] or 0, reverse=True)
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
    z = _datos_zeus([admision_id]).get(admision_id, {})

    qs = ArchivoFacturacion.objects.filter(Admision_id=admision_id).order_by('Tipo', 'IdArchivo')
    archivos_detalle = [{
        'id':     a.IdArchivo,
        'tipo':   a.Tipo,
        'nombre': a.NombreArchivo,
        'fecha':  a.FechaCreacionArchivo.isoformat() if a.FechaCreacionArchivo else None,
    } for a in qs]

    return JsonResponse({
        'success': True,
        'admision_id': admision_id,
        'zeus': {
            'paciente':    z.get('paciente', ''),
            'documento':   z.get('documento', ''),
            'eps':         z.get('eps', ''),
            'contrato':    z.get('contrato', ''),
            'servicio':    z.get('servicio_nombre', ''),
            'procedimientos': z.get('procedimientos', ''),
            'fecha_ing':   z['fecha_ing'].isoformat() if z.get('fecha_ing') else None,
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
    body = request.data
    bandeja = body.get('bandeja')
    if bandeja not in dict(CasoGestion.BANDEJA_CHOICES):
        return JsonResponse({'success': False, 'detail': 'bandeja inválida'}, status=400)

    caso, _ = CasoGestion.objects.get_or_create(admision_id=admision_id, bandeja=bandeja)

    if 'estado' in body:
        caso.estado = body['estado']
        if body['estado'] == 'resuelto' and not caso.fecha_resolucion:
            caso.fecha_resolucion = timezone.now()
            caso.usuario_resolvio = request.user if request.user.is_authenticated else None
        elif body['estado'] != 'resuelto':
            caso.fecha_resolucion = None
            caso.usuario_resolvio = None

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


# ── Represamientos ────────────────────────────────────────────────────────────

@api_view(['GET'])
def represamiento_admisiones(request):
    """
    Represamiento en admisiones: admisiones con hallazgos sin resolver en los documentos que carga el
    admisionista (ORDEN, AUTORIZACION, COMPROBANTE) o en los datos del paciente en SIESA, agrupado por
    el admisionista responsable (RevisionIA.UsuarioId).
    Los hallazgos del RESULTADO (ausencia del reporte, resultado de otro paciente...) son de la
    coordinación médica: no se cuentan aquí, se informan aparte en totales.coordinacion_medica.
    El filtro de fecha aplica sobre la fecha del hallazgo (sin rango: desde el inicio de la IA).
    """
    from gedocumental.models import RevisionIA

    fecha_inicio, fecha_fin = _rango(request)
    pendientes = (
        RevisionIA.objects
        .filter(Resuelta=False, Fecha__date__gte=fecha_inicio, Fecha__date__lte=fecha_fin)
        .exclude(TipoHallazgo='OK')
    )

    coordinacion = pendientes.filter(TipoDocumento=TIPO_COORDINACION).values('AdmisionId').distinct().count()

    hallazgos_por_admision: dict = defaultdict(list)
    usuario_por_admision: dict = {}
    primer_hallazgo: dict = {}
    for h in (pendientes.filter(TipoDocumento__in=TIPOS_ADMISIONES)
              .order_by('Fecha')
              .values('AdmisionId', 'TipoDocumento', 'TipoHallazgo', 'UsuarioId', 'Fecha')):
        aid = h['AdmisionId']
        hallazgos_por_admision[aid].append(f"{h['TipoDocumento']}: {h['TipoHallazgo']}")
        usuario_por_admision.setdefault(aid, h['UsuarioId'])
        primer_hallazgo.setdefault(aid, h['Fecha'])

    ids = list(hallazgos_por_admision.keys())
    if not ids:
        return JsonResponse({'data': [], 'totales': {'coordinacion_medica': coordinacion}})

    User = get_user_model()
    nombres = {u.id: _nombre_usuario(u)
               for u in User.objects.filter(id__in={u for u in usuario_por_admision.values() if u})}

    archivos_estado: dict = defaultdict(set)
    for aid, tipo in (ArchivoFacturacion.objects
                      .filter(Admision_id__in=ids, Tipo__in=['RESULTADO', 'FACTURA'])
                      .values_list('Admision_id', 'Tipo').distinct()):
        archivos_estado[aid].add(tipo)

    zeus = _datos_zeus(ids)

    grupos: dict = {}
    for aid in ids:
        z = zeus.get(aid, {})
        uid = usuario_por_admision.get(aid) or 0
        g = grupos.setdefault(uid, {
            'usuario_id': uid or None, 'usuario_nombre': nombres.get(uid, 'Sin usuario'),
            'admisiones': 0, 'valor_total': 0.0, 'hallazgos_count': 0,
            'dias_promedio': 0.0, 'vencidos': 0, 'casos': [],
        })
        h = hallazgos_por_admision[aid]
        dias = _dias_desde(primer_hallazgo.get(aid))
        valor = float(z.get('valor_total') or 0)
        g['admisiones'] += 1
        g['valor_total'] += valor
        g['hallazgos_count'] += len(h)
        if dias is not None and dias > DIAS_VENCIDO:
            g['vencidos'] += 1
        g['casos'].append({
            'admision_id':     aid,
            'con_estudio':     aid,
            'paciente':        z.get('paciente', ''),
            'eps':             z.get('eps', ''),
            'contrato':        z.get('contrato', ''),
            'procedimientos':  z.get('procedimientos', ''),
            'servicio':        _servicio_nombre(z.get('ufuncional'), z.get('servicio_nombre')),
            'valor_total':     valor,
            'fecha_ing':       z['fecha_ing'].isoformat() if z.get('fecha_ing') else None,
            'hallazgos':       h,
            'dias':            dias,
            'tiene_resultado': 'RESULTADO' in archivos_estado[aid],
            'tiene_factura':   'FACTURA' in archivos_estado[aid],
        })

    for g in grupos.values():
        g['casos'].sort(key=lambda c: c['dias'] or 0, reverse=True)
        dias = [c['dias'] for c in g['casos'] if c['dias'] is not None]
        g['dias_promedio'] = round(sum(dias) / len(dias), 1) if dias else 0
        g['con_hallazgos'] = len(g['casos'])
        g['sin_resultado'] = sum(1 for c in g['casos'] if not c['tiene_resultado'])
        g['con_resultado'] = sum(1 for c in g['casos'] if c['tiene_resultado'])
        g['con_factura'] = sum(1 for c in g['casos'] if c['tiene_factura'])

    data = sorted(grupos.values(), key=lambda x: x['admisiones'], reverse=True)
    return JsonResponse({
        'data': data,
        'totales': {
            'admisiones': sum(g['admisiones'] for g in data),
            'valor':      sum(g['valor_total'] for g in data),
            'hallazgos':  sum(g['hallazgos_count'] for g in data),
            'usuarios':   len(data),
            'coordinacion_medica': coordinacion,
        },
    })


@api_view(['GET'])
def represamiento_cuentas_medicas(request):
    """
    Represamiento en radicación: admisiones con FACTURA sin radicar (sin MRC ni particulares),
    agrupadas por responsable (CasoGestion de radicados, o el auditor de Cuentas Médicas de la factura).
    Los días se cuentan desde el cargue de la factura; vencidos = más de DIAS_VENCIDO días.
    """
    fecha_inicio, fecha_fin = _rango(request)
    ids = _facturas_sin_radicar(fecha_inicio, fecha_fin)
    if not ids:
        return JsonResponse({'data': [], 'totales': {}})

    zeus = _datos_zeus(ids)
    casos_map = _casos_por_admision(ids, 'radicados')
    fechas_factura = _fecha_factura(ids)
    facturas_resp = {
        f.Admision_id: f.UsuarioCuentasMedicas
        for f in ArchivoFacturacion.objects
            .filter(Tipo='FACTURA', Admision_id__in=ids, UsuarioCuentasMedicas__isnull=False)
            .select_related('UsuarioCuentasMedicas')
    }

    grupos: dict = {}
    for aid in ids:
        z = zeus.get(aid, {})
        caso = casos_map.get(aid)
        responsable = (caso.responsable if caso and caso.responsable else None) or facturas_resp.get(aid)
        key = responsable.id if responsable else 0
        g = grupos.setdefault(key, {
            'responsable_id': key or None,
            'responsable_nombre': _nombre_usuario(responsable) if responsable else 'Sin asignar',
            'admisiones': 0, 'valor_total': 0.0,
            'al_dia': 0, 'vencidos': 0, 'dias_promedio': 0.0,
            'casos': [],
        })
        valor = float(z.get('valor_total') or 0)
        dias = _dias_desde(fechas_factura.get(aid))
        g['admisiones'] += 1
        g['valor_total'] += valor
        if dias is not None and dias > DIAS_VENCIDO:
            g['vencidos'] += 1
        else:
            g['al_dia'] += 1
        g['casos'].append({
            'admision_id':     aid,
            'con_estudio':     aid,
            'paciente':        z.get('paciente', ''),
            'eps':             z.get('eps', ''),
            'contrato':        z.get('contrato', ''),
            'procedimientos':  z.get('procedimientos', ''),
            'servicio':        _servicio_nombre(z.get('ufuncional'), z.get('servicio_nombre')),
            'valor_total':     valor,
            'fecha_ing':       z['fecha_ing'].isoformat() if z.get('fecha_ing') else None,
            'fecha_factura':   _a_fecha(fechas_factura.get(aid)).isoformat() if fechas_factura.get(aid) else None,
            'numero_radicado': caso.numero_radicado if caso else '',
            'estado':          caso.estado if caso else 'pendiente',
            'dias':            dias,
        })

    for g in grupos.values():
        g['casos'].sort(key=lambda c: c['dias'] or 0, reverse=True)
        dias = [c['dias'] for c in g['casos'] if c['dias'] is not None]
        g['dias_promedio'] = round(sum(dias) / len(dias), 1) if dias else 0

    data = sorted(grupos.values(), key=lambda x: x['valor_total'], reverse=True)
    return JsonResponse({
        'data': data,
        'totales': {
            'admisiones':   sum(g['admisiones'] for g in data),
            'valor':        sum(g['valor_total'] for g in data),
            'vencidos':     sum(g['vencidos'] for g in data),
            'responsables': len(data),
        },
    })

"""
Auditoría de honorarios médicos — /api/v2/gestion-facturacion/auditoria/...

  GET  resumen/?periodo=YYYY-MM                → médicos del mes con su total de honorarios
  GET  medico/<cod>/?periodo=                  → líneas del médico (cod 0 = sin médico asignado)
  POST linea/                                  → OK / Glosa / ajuste / observación (una o varias líneas)
  POST cerrar/                                 → cierra o reabre la auditoría de un médico
  GET  exportar/?periodo=&cod_medico=&formato= → Excel COM-F-002-V1 (general | fisiatria)
  GET  tarifas/  · POST tarifas/importar/ · POST tarifas/<id>/ · POST tarifas/asignar-medico/
  GET  medicos/
  POST lumier/cargar/ · GET lumier/hallazgos/?periodo= · POST lumier/resolver/
  GET  sin-facturar/?periodo= [&exportar=1]     → servicios sin número de factura en SIESA
  GET  sin-tarifa/?periodo=                     → CUPS facturados por cada médico que no tienen tarifa
  GET  indicadores/?desde=YYYY-MM&hasta=YYYY-MM  → glosas y pagos por médico (de las líneas auditadas)

Reglas en controlfacturacion/services/honorarios.py.
"""

import io
import os
import re
from collections import defaultdict
from copy import copy
from decimal import Decimal, InvalidOperation

from django.db import connections, transaction
from django.http import HttpResponse, JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from gedocumental.services.ai_revision import _reparar_texto
from .models import AuditoriaCierre, AuditoriaLinea, LumierEstudio, ResolucionLumier, TarifaHonorario
from .services import honorarios as h

PLANTILLAS = os.path.join(os.path.dirname(__file__), 'plantillas')
SIN_MEDICO = 0
MESES = ['ENERO', 'FEBRERO', 'MARZO', 'ABRIL', 'MAYO', 'JUNIO', 'JULIO', 'AGOSTO', 'SEPTIEMBRE',
         'OCTUBRE', 'NOVIEMBRE', 'DICIEMBRE']
# Columnas del formato de Fisiatría
COLUMNAS_FISIATRIA = [('EMG', ('930860', '930801', '930820')), ('VCN', ('891509',)),
                      ('REF H', ('891515',)), ('ONDA F', ('891514',))]


# ── helpers ──────────────────────────────────────────────────────────────────

def _periodo(request) -> str:
    p = request.GET.get('periodo') or (request.data.get('periodo') if request.method == 'POST' else '') or ''
    return p if re.fullmatch(r'\d{4}-\d{2}', str(p)) else h.periodo_anterior()


def _usuario(request) -> str:
    u = getattr(request, 'user', None)
    if u is None or not getattr(u, 'is_authenticated', False):
        return ''
    return (getattr(u, 'nombre', '') or u.get_full_name() or u.get_username() or '').strip()


def _dec(valor) -> Decimal | None:
    if valor in (None, ''):
        return None
    try:
        return Decimal(str(valor).replace(',', ''))
    except InvalidOperation:
        return None


def _num(v):
    return float(v) if v is not None else None


def medicos_zeus() -> list[dict]:
    with connections['zeussalud'].cursor() as cur:
        cur.execute("SELECT codigo, nombre, especialidad FROM Medicos")
        return [{'codigo': int(r[0]), 'nombre': ' '.join(_reparar_texto(r[1] or '').split()),
                 'especialidad': ' '.join(_reparar_texto(r[2] or '').split())} for r in cur.fetchall()]


def _medicos_activos() -> set:
    with connections['zeussalud'].cursor() as cur:
        cur.execute("""SELECT DISTINCT TRY_CAST(cod_medico AS INT) FROM sis_deta
                       WHERE fecha_servicio >= DATEADD(month, -6, GETDATE()) AND ISNULL(cod_medico, '') <> ''""")
        return {r[0] for r in cur.fetchall() if r[0]}


def _lineas_zeus(periodo: str) -> list[dict]:
    inicio, fin = h.rango_periodo(periodo)
    with connections['zeussalud'].cursor() as cur:
        cur.execute(
            """
            SELECT sd.id, sd.estudio, CAST(sd.fecha_servicio AS DATE),
                   LTRIM(RTRIM(ISNULL(sd.cod_servicio, ''))), sd.descripcion, sd.cantidad, sd.total,
                   TRY_CAST(sd.cod_medico AS INT),
                   LTRIM(RTRIM(COALESCE(sp.primer_nom,'') + ' ' + COALESCE(sp.segundo_nom,'') + ' '
                         + COALESCE(sp.primer_ape,'') + ' ' + COALESCE(sp.segundo_ape,''))),
                   LTRIM(RTRIM(ISNULL(sp.tipo_id, ''))), LTRIM(RTRIM(ISNULL(sp.num_id, ''))),
                   LTRIM(RTRIM(COALESCE(NULLIF(LTRIM(RTRIM(ct.alias)), ''), ct.nombre, ''))),
                   CASE WHEN ISNULL(sm.Prefijo, '') <> ''
                        THEN LTRIM(RTRIM(sm.Prefijo)) + ' ' + LTRIM(RTRIM(ISNULL(sm.NumeroFactura, '')))
                        ELSE '' END
            FROM sis_deta sd
            JOIN sis_maes sm       ON sm.con_estudio = sd.estudio
            LEFT JOIN sis_paci sp  ON sm.autoid = sp.autoid
            LEFT JOIN contratos ct ON sm.contrato = ct.codigo
            WHERE sd.fecha_servicio >= %s AND sd.fecha_servicio < %s
              AND sd.TipoItem = 'Procedimiento'
              AND ISNULL(sd.ItemRevertido, 0) = 0
              AND ISNULL(sm.estado, '') <> 'N'
            """,
            [inicio.isoformat(), fin.isoformat()],
        )
        filas = cur.fetchall()
    lineas = []
    for (sid, estudio, fecha, cod, desc, cant, total, medico, paciente, tipo_id, num_id, contrato,
         factura) in filas:
        if h.excluido(cod):
            continue
        lineas.append({
            'sis_deta_id': int(sid), 'estudio': int(estudio), 'fecha': fecha,
            'cod_servicio': h.norm_cups(cod), 'descripcion': _reparar_texto(desc or '').strip(),
            'cantidad': float(cant or 1), 'valor_facturado': Decimal(str(total or 0)),
            'cod_medico_zeus': int(medico) if medico else SIN_MEDICO,
            'paciente': _reparar_texto(' '.join((paciente or '').split())),
            'tipo_id': tipo_id or '', 'documento': re.sub(r'\D', '', num_id or ''),
            'contrato': _reparar_texto(contrato or ''), 'factura': factura or '',
        })
    return lineas


def _agenda_zeus(periodo: str, cods: set) -> dict:
    """
    {cod_medico: [bloques]} de la agenda de Zeus (programacion_medico_detalle) en el mes. Cuenta los cupos
    programados (no bloqueados, o bloqueados pero con cita).
    """
    if not cods:
        return {}
    inicio, fin = h.rango_periodo(periodo)
    marcas = ', '.join(['%s'] * len(cods))
    with connections['zeussalud'].cursor() as cur:
        cur.execute(
            f"""SELECT d.Medico, CAST(d.Fecha AS DATE), d.IdProgramacionMedico, ISNULL(pm.intervalo, 0),
                       MIN(CONVERT(VARCHAR(5), d.Fecha, 108)), MAX(CONVERT(VARCHAR(5), d.Fecha, 108)), COUNT(*)
                FROM programacion_medico_detalle d
                JOIN programacion_medico pm ON pm.id = d.IdProgramacionMedico
                WHERE d.Medico IN ({marcas}) AND d.Fecha >= %s AND d.Fecha < %s
                  AND ISNULL(d.SinProgramacion, 0) = 0
                  AND (ISNULL(d.Bloqueado, 0) = 0 OR d.IdCita IS NOT NULL)
                GROUP BY d.Medico, CAST(d.Fecha AS DATE), d.IdProgramacionMedico, pm.intervalo""",
            [*sorted(cods), inicio.isoformat(), fin.isoformat()])
        filas = cur.fetchall()
    agenda = defaultdict(list)
    for medico, dia, _, intervalo, ini, fin_, cupos in filas:
        agenda[int(medico)].append({'dia': dia, 'inicio': ini, 'fin': fin_, 'intervalo': int(intervalo),
                                    'cupos': int(cupos)})
    return agenda


def _lineas_agenda(periodo: str, valor_hora: dict) -> list[dict]:
    """Una línea por día de agenda de cada médico pagado por hora: horas × valor de la hora."""
    lineas = []
    for cod, bloques in _agenda_zeus(periodo, set(valor_hora)).items():
        for d in h.horas_agenda(bloques):
            texto = ' y '.join(f"{b['inicio']}–{b['fin']}" for b in d['bloques'])
            horas = d['horas']
            lineas.append({
                'sis_deta_id': h.id_agenda(d['dia'], cod), 'estudio': 0, 'fecha': d['dia'],
                'cod_servicio': 'AGENDA', 'descripcion': f"{texto} ({float(horas):g} h)",
                'cantidad': float(horas), 'valor_facturado': Decimal(0), 'cod_medico_zeus': cod,
                'paciente': 'HORAS DE AGENDA', 'tipo_id': '', 'documento': '', 'contrato': '', 'factura': '',
                'tipo': 'AGENDA', 'bloques': [{'inicio': b['inicio'], 'fin': b['fin'], 'horas': float(b['horas'])}
                                               for b in d['bloques']],
                '_valor_hora': valor_hora[cod],
            })
    return lineas


def _clave_md(sid):
    return f"MD:{sid}"


def construir(periodo: str) -> dict:
    """Líneas del mes con honorario calculado, decisiones del auditor y hallazgos de Lumier."""
    # CUPS que no se le cuentan a un médico en particular (ej. 890426 / 890202 al Dr. Carrasquilla)
    lineas = [l for l in _lineas_zeus(periodo) if not h.excluido_medico(l['cod_servicio'], l['cod_medico_zeus'])]
    medicos = {m['codigo']: m for m in medicos_zeus()}
    # Puntos de bloqueo del mes de cada médico que cobra de una bolsa (Dr. Ortega)
    puntos_bloqueo = defaultdict(float)
    for l in lineas:
        if l['cod_medico_zeus'] in h.BOLSA_BLOQUEOS and h.es_bloqueo(l['cod_servicio']):
            puntos_bloqueo[l['cod_medico_zeus']] += l['cantidad']

    # Procedimientos de cada admisión (para la sedación escalonada)
    por_estudio = defaultdict(set)
    for l in lineas:
        if h.cups_base(l['cod_servicio']) != '998702':
            por_estudio[l['estudio']].add(h.cups_base(l['cod_servicio']))

    # Cruce con Lumier
    estudios = list(LumierEstudio.objects.filter(periodo=periodo))
    lumier_cargado = bool(estudios)
    imagen = [l for l in lineas if h.es_imagen(l['cod_servicio'])]
    pareja, otro_cups, sin_pareja = h.cruzar_lumier(imagen, estudios) if lumier_cargado else ({}, {}, [])
    resoluciones = {r.clave: r for r in ResolucionLumier.objects.filter(periodo=periodo)}

    tarifas = defaultdict(list)
    for t in TarifaHonorario.objects.filter(activo=True, cod_medico__isnull=False):
        tarifas[t.cod_medico].append(t)
    # Médicos pagados por horas de agenda: valor de la hora = su tarifa "(HORA)" de entidades
    valor_hora = {}
    for cod, ts in tarifas.items():
        de_hora = [t for t in ts if h.es_tarifa_hora(t) and t.tipo == 'FIJO' and t.valor]
        if de_hora:
            valor_hora[cod] = max(t.valor for t in de_hora)
    for l in lineas:
        l['tipo'] = 'SERVICIO'
    lineas += _lineas_agenda(periodo, valor_hora)
    decisiones = {d.sis_deta_id: d for d in AuditoriaLinea.objects.filter(
        sis_deta_id__in=[l['sis_deta_id'] for l in lineas])} if lineas else {}

    hallazgos = []
    for l in lineas:
        sid, alertas = l['sis_deta_id'], []
        l['cod_medico'] = l['cod_medico_zeus']
        l['lumier'] = None
        base_hallazgo = {'sis_deta_id': sid, 'estudio': l['estudio'], 'fecha': l['fecha'],
                         'paciente': l['paciente'], 'documento': l['documento'], 'cups': l['cod_servicio'],
                         'examen': l['descripcion'],
                         'medico_zeus': medicos.get(l['cod_medico_zeus'], {}).get('nombre', 'Sin médico'),
                         'cod_medico_zeus': l['cod_medico_zeus']}
        emparejados = pareja.get(sid) or otro_cups.get(sid)
        if emparejados:
            e = emparejados[0]
            l['lumier'] = {'profesional': e.profesional, 'cod_medico': e.cod_medico,
                           'cups': e.cups, 'examen': e.examen, 'estudios': len(emparejados)}
            if sid in otro_cups:
                clave = f"CD:{sid}"
                hallazgos.append(dict(base_hallazgo, clave=clave, tipo='CUPS_DIFERENTE',
                                      medico_lumier=e.profesional, cod_medico_lumier=e.cod_medico,
                                      cups_lumier=e.cups, examen_lumier=e.examen))
                if clave not in resoluciones:
                    alertas.append(f"Lumier registra otro examen: {e.cups} {e.examen}")
            medicos_lumier = {x.cod_medico for x in emparejados}
            if medicos_lumier != {l['cod_medico_zeus']}:
                # Quien realmente lee el examen es el de Lumier: se le paga a él automáticamente (regla
                # 2026-11-23); solo si el auditor decide "Pagar a Zeus" se queda con el médico de Zeus
                res = resoluciones.get(_clave_md(sid))
                if res and res.decision == 'ZEUS':
                    pass
                elif e.cod_medico:
                    l['cod_medico'] = e.cod_medico
                    l['medico_agenda'] = medicos.get(l['cod_medico_zeus'], {}).get('nombre', 'Sin médico')
                else:
                    alertas.append(f"Lumier: leído por {e.profesional}, que no se encontró en Zeus; pendiente validar")
                hallazgos.append(dict(base_hallazgo, clave=_clave_md(sid), tipo='MEDICO_DIFERENTE',
                                      medico_lumier=', '.join(sorted({x.profesional for x in emparejados})),
                                      cod_medico_lumier=e.cod_medico))
        elif lumier_cargado and h.es_imagen(l['cod_servicio']):
            hallazgos.append(dict(base_hallazgo, clave=f"SZ:{sid}", tipo='SOLO_ZEUS', medico_lumier=''))
            if f"SZ:{sid}" not in resoluciones:
                alertas.append('No está en Lumier')

        l['por_horas'] = False
        if l['tipo'] == 'AGENDA':
            tarifa = None
            calculado = h._redondear(Decimal(str(l['cantidad'])) * l['_valor_hora'])
            l['tarifa'] = f"${int(l['_valor_hora']):,} por hora".replace(',', '.')
        elif l['cod_medico'] == SIN_MEDICO:
            alertas.append('Sin médico en Zeus')
            tarifa, calculado, l['tarifa'] = None, Decimal(0), ''
        elif h.es_bloqueo(l['cod_servicio']) and h.honorario_bolsa_bloqueo(
                l['cod_medico'], l['cantidad'], puntos_bloqueo.get(l['cod_medico'])):
            # Bloqueo pagado de la bolsa mensual del médico (Dr. Ortega)
            calculado, l['tarifa'] = h.honorario_bolsa_bloqueo(l['cod_medico'], l['cantidad'],
                                                               puntos_bloqueo[l['cod_medico']])
            tarifa = True   # tiene tarifa (no es "sin tarifa")
        else:
            tarifa, alertas_t = h.elegir_tarifa(tarifas.get(l['cod_medico'], []), l['cod_servicio'], l['contrato'],
                                                l['cantidad'])
            alertas += alertas_t
            if tarifa is not None and h.es_tarifa_hora(tarifa):
                # Consulta de un médico pagado por horas: se paga en la línea de agenda del día
                calculado, l['tarifa'], l['por_horas'] = Decimal(0), 'Incluida en las horas de agenda', True
            elif tarifa is not None:
                calculado, alertas_c = h.calcular_honorario(tarifa, l['cantidad'], l['valor_facturado'],
                                                            len(por_estudio.get(l['estudio'], ())),
                                                            l['descripcion'])
                alertas += alertas_c
                l['tarifa'] = _texto_tarifa(tarifa)
            else:
                calculado, l['tarifa'] = Decimal(0), ''
        if not l['factura'] and l['tipo'] != 'AGENDA':
            alertas.append('Sin factura en Zeus')
        d = decisiones.get(sid)
        l['grupo'] = h.grupo_contrato(l['contrato']) if l['tipo'] != 'AGENDA' else 'AGENDA'
        l['categoria'] = h.categoria(l['cod_servicio'], l['descripcion'])
        l['honorario_calculado'] = calculado
        l['estado'] = d.estado if d else 'PENDIENTE'
        l['valor_ajustado'] = d.valor_ajustado if d else None
        l['valor_glosa'] = d.valor_glosa if d else None
        l['observacion'] = d.observacion if d else ''
        l['auditado_por'] = d.usuario if d else ''
        l['honorario'] = h.honorario_final(calculado, l['estado'], l['valor_ajustado'], l['valor_glosa'])
        base = l['valor_ajustado'] if l['valor_ajustado'] is not None else calculado
        l['valor_glosado'] = (base - l['honorario']) if l['estado'] == 'GLOSA' else Decimal(0)
        l['alertas'] = alertas
        l['sin_tarifa'] = tarifa is None and l['tipo'] != 'AGENDA' and not l['por_horas']

    for e in sin_pareja:
        hallazgos.append({
            'clave': f"SL:{e.pk}", 'tipo': 'SOLO_LUMIER', 'sis_deta_id': None, 'estudio': None,
            'fecha': e.fecha, 'paciente': e.paciente, 'documento': e.documento, 'cups': e.cups,
            'examen': e.examen, 'medico_zeus': '', 'medico_lumier': e.profesional,
            'cod_medico_lumier': e.cod_medico,
        })
    for x in hallazgos:
        x.setdefault('cod_medico_zeus', None)
        x.setdefault('cod_medico_lumier', None)
        # Nombre del médico de Lumier como está en Zeus (Lumier lo escribe distinto: 'William  García  Rossi')
        x['medico_lumier_zeus'] = medicos.get(x['cod_medico_lumier'], {}).get('nombre', '') if x['cod_medico_lumier'] else ''
        r = resoluciones.get(x['clave'])
        x['decision'] = r.decision if r else ''
        x['nota'] = r.nota if r else ''
        x['validado_por'] = r.usuario if r else ''
        x['automatico'] = False
        if not r and x['tipo'] == 'MEDICO_DIFERENTE' and x['cod_medico_lumier']:
            # Se paga a Lumier sin intervención (el radiólogo de Lumier es quien leyó el examen)
            x.update(decision='LUMIER', automatico=True, validado_por='Automático',
                     nota='Se paga al radiólogo que leyó en Lumier')
    return {'lineas': lineas, 'medicos': medicos, 'hallazgos': hallazgos, 'lumier_cargado': lumier_cargado,
            'lumier_estudios': len(estudios), 'valor_hora': valor_hora}


def _texto_tarifa(t) -> str:
    if t.tipo == 'PORCENTAJE':
        return f"{t.porcentaje.normalize()}% del valor facturado"
    if t.tipo == 'ESCALONADO':
        return 'Escalonado ' + ' / '.join(f"{int(x):,}".replace(',', '.') for x in t.escalas.split(',') if x)
    valor = f"${int(t.valor or 0):,}".replace(',', '.')
    if t.tipo == 'POR_PUNTO':
        return f"{valor} por punto" + (f" (máx. {t.max_puntos})" if t.max_puntos else '')
    return valor + (' por unidad' if t.por_unidad else '')


def _linea_json(l: dict) -> dict:
    return {
        'sis_deta_id': l['sis_deta_id'], 'estudio': l['estudio'],
        'fecha': l['fecha'].isoformat() if l['fecha'] else None,
        'paciente': l['paciente'], 'tipo_id': l['tipo_id'], 'documento': l['documento'],
        'cups': l['cod_servicio'], 'descripcion': l['descripcion'], 'cantidad': l['cantidad'],
        'contrato': l['contrato'], 'grupo': l['grupo'], 'factura': l['factura'],
        'valor_facturado': _num(l['valor_facturado']), 'tarifa': l['tarifa'],
        'honorario_calculado': _num(l['honorario_calculado']), 'valor_ajustado': _num(l['valor_ajustado']),
        'honorario': _num(l['honorario']), 'estado': l['estado'], 'observacion': l['observacion'],
        'auditado_por': l['auditado_por'], 'alertas': l['alertas'], 'sin_tarifa': l['sin_tarifa'],
        'cod_medico': l['cod_medico'], 'cod_medico_zeus': l['cod_medico_zeus'],
        'lumier': l['lumier'], 'tipo': l['tipo'], 'por_horas': l['por_horas'], 'bloques': l.get('bloques'),
        'valor_glosa': _num(l['valor_glosa']), 'valor_glosado': _num(l['valor_glosado']),
        'medico_agenda': l.get('medico_agenda', ''), 'categoria': l.get('categoria', 'PROCEDIMIENTOS'),
    }


def _cerrada(periodo, cod_medico) -> AuditoriaCierre | None:
    return AuditoriaCierre.objects.filter(periodo=periodo, cod_medico=cod_medico).first()


# ── resumen y detalle ────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def resumen(request):
    periodo = _periodo(request)
    datos = construir(periodo)
    cierres = {c.cod_medico: c for c in AuditoriaCierre.objects.filter(periodo=periodo)}
    medico_de_linea = {l['sis_deta_id']: l['cod_medico_zeus'] for l in datos['lineas']}
    hall_pend = defaultdict(int)
    for x in datos['hallazgos']:
        if not x['decision']:
            # El hallazgo se avisa a los dos médicos implicados (el de Zeus y el de Lumier)
            for cod in {x.get('cod_medico_lumier'), medico_de_linea.get(x['sis_deta_id'])}:
                if cod is not None:
                    hall_pend[cod] += 1
    filas = defaultdict(lambda: {'lineas': 0, 'honorario': Decimal(0), 'pendientes': 0, 'ok': 0, 'glosas': 0,
                                 'con_alertas': 0, 'sin_tarifa': 0, 'facturado': Decimal(0),
                                 'glosado': Decimal(0)})
    for l in datos['lineas']:
        f = filas[l['cod_medico']]
        f['lineas'] += 1
        f['honorario'] += l['honorario']
        f['facturado'] += l['valor_facturado']
        f['pendientes'] += l['estado'] == 'PENDIENTE'
        f['ok'] += l['estado'] == 'OK'
        f['glosas'] += l['estado'] == 'GLOSA'
        f['glosado'] += l['valor_glosado']
        f['con_alertas'] += bool(l['alertas'])
        f['sin_tarifa'] += l['sin_tarifa']
    salida = []
    for cod, f in filas.items():
        m = datos['medicos'].get(cod, {})
        c = cierres.get(cod)
        salida.append({
            'cod_medico': cod, 'medico': m.get('nombre') or 'SIN MÉDICO ASIGNADO',
            'especialidad': m.get('especialidad', ''), 'lineas': f['lineas'],
            'honorario': _num(f['honorario']), 'facturado': _num(f['facturado']),
            'pendientes': f['pendientes'], 'ok': f['ok'], 'glosas': f['glosas'], 'glosado': _num(f['glosado']),
            'con_alertas': f['con_alertas'], 'sin_tarifa': f['sin_tarifa'],
            'hallazgos_lumier': hall_pend.get(cod, 0),
            'cerrada': bool(c), 'cerrada_por': c.usuario if c else '',
            'fecha_cierre': c.fecha.isoformat() if c else None,
        })
    salida.sort(key=lambda x: (x['cod_medico'] == SIN_MEDICO, -x['honorario']))
    pendientes_lumier = sum(1 for x in datos['hallazgos'] if not x['decision'])
    return JsonResponse({
        'periodo': periodo, 'medicos': salida,
        'total_honorarios': sum(x['honorario'] for x in salida),
        'lumier': {'cargado': datos['lumier_cargado'], 'estudios': datos['lumier_estudios'],
                   'hallazgos': len(datos['hallazgos']), 'pendientes': pendientes_lumier},
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def detalle_medico(request, cod_medico: int):
    periodo = _periodo(request)
    datos = construir(periodo)
    lineas = [l for l in datos['lineas'] if l['cod_medico'] == cod_medico]
    lineas.sort(key=lambda l: (l['fecha'] or h.date.min, l['paciente'], l['cod_servicio']))
    m = datos['medicos'].get(cod_medico, {})
    c = _cerrada(periodo, cod_medico)
    return JsonResponse({
        'periodo': periodo, 'cod_medico': cod_medico,
        'medico': m.get('nombre') or 'SIN MÉDICO ASIGNADO', 'especialidad': m.get('especialidad', ''),
        'cerrada': bool(c), 'cerrada_por': c.usuario if c else '',
        'lineas': [_linea_json(l) for l in lineas],
        'por_horas': cod_medico in datos['valor_hora'],
        'valor_hora': _num(datos['valor_hora'].get(cod_medico)),
        'tiene_formato_fisiatria': any(h.cups_base(l['cod_servicio']) in sum((list(c) for _, c in COLUMNAS_FISIATRIA), [])
                                       for l in lineas),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def guardar_linea(request):
    """{periodo, cod_medico, ids:[sis_deta_id...] | sis_deta_id, estado?, observacion?, valor_ajustado?}"""
    periodo = _periodo(request)
    cod_medico = request.data.get('cod_medico')
    if cod_medico is not None and _cerrada(periodo, int(cod_medico)):
        return JsonResponse({'error': 'La auditoría de este médico está cerrada. Reábrala para editar.'}, status=400)
    ids = request.data.get('ids') or ([request.data['sis_deta_id']] if request.data.get('sis_deta_id') else [])
    estado = request.data.get('estado')
    if estado and estado not in ('PENDIENTE', 'OK', 'GLOSA'):
        return JsonResponse({'error': 'Estado no válido'}, status=400)
    if estado == 'GLOSA' and len(ids) == 1 and not (request.data.get('observacion') or '').strip():
        existente = AuditoriaLinea.objects.filter(sis_deta_id=ids[0]).first()
        if not (existente and existente.observacion.strip()):
            return JsonResponse({'error': 'La glosa necesita una observación'}, status=400)
    # Foto de cada línea (lo que vio el auditor): {sis_deta_id: {honorario_calculado, cups, descripcion, contrato}}
    fotos = request.data.get('lineas') or {}
    usuario = _usuario(request)
    with transaction.atomic():
        for sid in ids:
            d, _ = AuditoriaLinea.objects.get_or_create(sis_deta_id=int(sid), defaults={'periodo': periodo})
            d.periodo = periodo
            if cod_medico is not None:
                d.cod_medico = int(cod_medico)
            if estado:
                d.estado = estado
            if 'observacion' in request.data:
                d.observacion = (request.data.get('observacion') or '').strip()
            if 'valor_ajustado' in request.data:
                d.valor_ajustado = _dec(request.data.get('valor_ajustado'))
            if d.estado != 'GLOSA':
                d.valor_glosa = None
            elif 'valor_glosa' in request.data:
                d.valor_glosa = _dec(request.data.get('valor_glosa'))   # vacío = se glosa todo
            foto = fotos.get(str(sid)) or {}
            if foto:
                d.honorario_calculado = _dec(foto.get('honorario_calculado'))
                d.cups = str(foto.get('cups') or '')[:20]
                d.descripcion = str(foto.get('descripcion') or '')[:255]
                d.contrato = str(foto.get('contrato') or '')[:150]
            if d.honorario_calculado is not None:
                d.honorario_final = h.honorario_final(d.honorario_calculado, d.estado, d.valor_ajustado,
                                                      d.valor_glosa)
            d.usuario = usuario
            d.save()
    return JsonResponse({'ok': True, 'actualizadas': len(ids)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cerrar(request):
    periodo = _periodo(request)
    cod_medico = int(request.data.get('cod_medico'))
    if request.data.get('reabrir'):
        AuditoriaCierre.objects.filter(periodo=periodo, cod_medico=cod_medico).delete()
        return JsonResponse({'ok': True, 'cerrada': False})
    datos = construir(periodo)
    lineas = [l for l in datos['lineas'] if l['cod_medico'] == cod_medico]
    pendientes = sum(1 for l in lineas if l['estado'] == 'PENDIENTE')
    if pendientes:
        return JsonResponse({'error': f'Quedan {pendientes} líneas pendientes por auditar'}, status=400)
    total = sum((l['honorario'] for l in lineas), Decimal(0))
    # Foto definitiva de las líneas (indicadores)
    with transaction.atomic():
        for l in lineas:
            AuditoriaLinea.objects.filter(sis_deta_id=l['sis_deta_id']).update(
                cod_medico=cod_medico, honorario_calculado=l['honorario_calculado'], honorario_final=l['honorario'],
                cups=l['cod_servicio'][:20], descripcion=l['descripcion'][:255], contrato=l['contrato'][:150])
    AuditoriaCierre.objects.update_or_create(periodo=periodo, cod_medico=cod_medico,
                                             defaults={'total': total, 'usuario': _usuario(request)})
    return JsonResponse({'ok': True, 'cerrada': True, 'total': _num(total)})


# ── exportar al formato COM-F-002-V1 ─────────────────────────────────────────

def _copiar_estilo(origen, destino):
    destino.font, destino.border, destino.fill = copy(origen.font), copy(origen.border), copy(origen.fill)
    destino.number_format, destino.alignment = origen.number_format, copy(origen.alignment)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def exportar(request):
    import openpyxl
    periodo = _periodo(request)
    cod_medico = int(request.GET.get('cod_medico'))
    formato = request.GET.get('formato', 'general')
    datos = construir(periodo)
    # Sin las glosas totales ni las consultas que se pagan en las horas de agenda (valen 0)
    lineas = [l for l in datos['lineas'] if l['cod_medico'] == cod_medico and not l['por_horas']
              and (l['estado'] != 'GLOSA' or l['honorario'] > 0)]
    lineas.sort(key=lambda l: (l['fecha'] or h.date.min, l['paciente']))
    m = datos['medicos'].get(cod_medico, {})
    anio, mes = periodo.split('-')
    nombre_mes = MESES[int(mes) - 1]

    if formato == 'fisiatria':
        wb = openpyxl.load_workbook(os.path.join(PLANTILLAS, 'honorarios_fisiatria.xlsx'))
        ws = wb.active
        ws['A4'] = f"PROFESIONAL: {m.get('nombre', '')}"
        ws['A5'] = f"MES DE SERVICIOS: {nombre_mes}"
        ws['D5'] = f"AÑO: {anio}"
        # Una fila por paciente: en EMG / VCN / REF H / ONDA F va la CANTIDAD y en TOTAL el honorario
        # (regla 2026-11-21). Los paquetes particulares (000004/5/6) suman sus electromiografías y
        # neuroconducciones.
        cantidades = defaultdict(lambda: defaultdict(Decimal))
        totales = defaultdict(lambda: defaultdict(Decimal))
        info = {}
        for l in lineas:
            sanitas = l['grupo'] == 'SANITAS'
            partes = _columnas_fisiatria(l)
            if not partes:
                continue
            clave = l['documento'] or l['paciente']
            if clave not in info or (l['fecha'] or h.date.max) < (info[clave]['fecha'] or h.date.max):
                info[clave] = l
            for col, cant in partes:
                cantidades[clave][(sanitas, col)] += cant
            totales[clave][sanitas] += l['honorario']
        estilo = [ws.cell(row=7, column=c) for c in range(1, 15)]
        orden = sorted(info, key=lambda k: (info[k]['fecha'] or h.date.min, info[k]['paciente']))
        for n, clave in enumerate(orden, start=1):
            r = 6 + n
            l = info[clave]
            fila = [n, l['fecha'], l['paciente'], l['documento']]
            for sanitas in (False, True):
                parte = [float(cantidades[clave].get((sanitas, i), 0)) or None for i in range(4)]
                fila += parte + [float(totales[clave].get(sanitas, 0)) or None]
            for c, v in enumerate(fila, start=1):
                celda = ws.cell(row=r, column=c, value=v)
                _copiar_estilo(estilo[c - 1], celda)
                if c == 2:
                    celda.number_format = 'DD/MM/YYYY'
                elif c in (9, 14):
                    celda.number_format = '"$"#,##0'
                elif c >= 5:
                    celda.number_format = '0'
        total_r = 7 + len(orden)
        ws.cell(row=total_r, column=3, value='TOTAL').font = copy(ws['A6'].font)
        for c in range(5, 15):
            letra = openpyxl.utils.get_column_letter(c)
            celda = ws.cell(row=total_r, column=c, value=f"=SUM({letra}7:{letra}{total_r - 1})")
            celda.number_format = '"$"#,##0' if c in (9, 14) else '0'
            celda.font = copy(ws['A6'].font)
    else:
        wb = openpyxl.load_workbook(os.path.join(PLANTILLAS, 'honorarios_general.xlsx'))
        ws = wb.active
        ws['A4'] = f"PROFESIONAL: {m.get('nombre', '')}"
        ws['E4'] = f"ÁREA: {m.get('especialidad', '')}"
        ws['A5'] = f"MES DE SERVICIOS: {nombre_mes}"
        ws['D5'] = f"AÑO: {anio}"
        n_filas = max(len(lineas), 1)
        if n_filas > 2:   # la plantilla trae 2 filas de datos (7 y 8) y el total en la 9
            ws.insert_rows(9, n_filas - 2)
        estilo = [ws.cell(row=7, column=c) for c in range(1, 8)]
        alto = ws.row_dimensions[7].height
        for n, l in enumerate(lineas, start=1):
            r = 6 + n
            fila = [l['documento'], n, l['fecha'], l['paciente'], f"{l['cod_servicio']} {l['descripcion']}",
                    l['cantidad'], float(l['honorario'])]
            for c, v in enumerate(fila, start=1):
                celda = ws.cell(row=r, column=c, value=v)
                _copiar_estilo(estilo[c - 1], celda)
                celda.alignment = copy(estilo[c - 1].alignment)
                if c == 3:
                    celda.number_format = 'DD/MM/YYYY'
                elif c == 7:
                    celda.number_format = '"$"#,##0'
            ws.row_dimensions[r].height = min(alto or 15, 30)
        total_r = 7 + n_filas
        ws.cell(row=total_r, column=7, value=f"=SUM(G7:G{total_r - 1})")
        texto = ws.cell(row=total_r + 3, column=1)
        if texto.value and str(texto.value).startswith('Yo'):
            texto.value = (f"Yo {m.get('nombre', '')} identificado con documento de identidad No ________ dejo "
                           f"constancia, que los servicios relacionados, corresponden a la totalidad de las "
                           f"actividades que desarrollé durante el mes {nombre_mes.lower()} del año {anio}.")
    buffer = io.BytesIO()
    wb.save(buffer)
    nombre = re.sub(r'[^A-Za-z0-9]+', '_', m.get('nombre', 'medico')).strip('_')
    respuesta = HttpResponse(buffer.getvalue(),
                             content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = (f'attachment; filename="Honorarios_{nombre}_{periodo}'
                                        f'{"_fisiatria" if formato == "fisiatria" else ""}.xlsx"')
    return respuesta


def _columnas_fisiatria(l) -> list[tuple[int, Decimal]]:
    """[(columna EMG=0/VCN=1/REF H=2/ONDA F=3, cantidad)] de una línea para el formato de Fisiatría."""
    base = h.cups_base(l['cod_servicio'])
    col = next((i for i, (_, cups) in enumerate(COLUMNAS_FISIATRIA) if base in cups), None)
    if col is not None:
        return [(col, Decimal(str(l['cantidad'] or 1)))]
    # Paquete particular: "PAQUETE 2 ELECTROMIOGRAFIAS + 2 NEUROCONDUCCIONES"
    texto = h.plano(l['descripcion'])
    if 'PAQUETE' in texto:
        partes = []
        for col, patron in ((0, r'(\d+)\s*ELECTROMIOGRAF'), (1, r'(\d+)\s*NEUROCONDUC')):
            m = re.search(patron, texto)
            if m:
                partes.append((col, Decimal(m.group(1))))
        return partes
    return []


# ── servicios sin factura en SIESA ───────────────────────────────────────────

def _sin_facturar(periodo: str) -> list[dict]:
    datos = construir(periodo)
    salida = []
    for l in datos['lineas']:
        if l['tipo'] == 'AGENDA' or l['factura']:
            continue
        salida.append({
            'sis_deta_id': l['sis_deta_id'], 'estudio': l['estudio'], 'fecha': l['fecha'],
            'entidad': l['contrato'] or 'SIN CONTRATO', 'cups': l['cod_servicio'], 'descripcion': l['descripcion'],
            'paciente': l['paciente'], 'tipo_id': l['tipo_id'], 'documento': l['documento'],
            'cantidad': l['cantidad'], 'valor': l['valor_facturado'],
            'medico': datos['medicos'].get(l['cod_medico'], {}).get('nombre', 'Sin médico'),
        })
    salida.sort(key=lambda x: (x['entidad'], x['cups'], x['fecha'] or h.date.min, x['paciente']))
    return salida


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sin_facturar(request):
    periodo = _periodo(request)
    filas = _sin_facturar(periodo)
    if request.GET.get('exportar'):
        return _exportar_sin_facturar(periodo, filas)
    return JsonResponse({'periodo': periodo, 'filas': [
        dict(f, fecha=f['fecha'].isoformat() if f['fecha'] else None, valor=_num(f['valor'])) for f in filas]})


def _exportar_sin_facturar(periodo: str, filas: list[dict]) -> HttpResponse:
    """Excel por entidad, procedimiento y fecha + hoja de resumen por entidad y procedimiento."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sin facturar'
    encabezado = ['Entidad', 'CUPS', 'Procedimiento', 'Fecha', 'Estudio', 'Paciente', 'Documento', 'Médico',
                  'Cantidad', 'Valor']
    negrita, fondo = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor='2D1B69')
    ws.append(encabezado)
    for c in ws[1]:
        c.font, c.fill, c.alignment = negrita, fondo, Alignment(horizontal='center')
    for f in filas:
        ws.append([f['entidad'], f['cups'], f['descripcion'], f['fecha'], f['estudio'], f['paciente'],
                   f"{f['tipo_id']} {f['documento']}".strip(), f['medico'], f['cantidad'], float(f['valor'])])
    for fila in ws.iter_rows(min_row=2):
        fila[3].number_format = 'DD/MM/YYYY'
        fila[9].number_format = '"$"#,##0'
    for letra, ancho in zip('ABCDEFGHIJ', (34, 11, 50, 12, 9, 34, 16, 30, 9, 13)):
        ws.column_dimensions[letra].width = ancho
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = 'A2'

    resumen = wb.create_sheet('Resumen')
    resumen.append(['Entidad', 'CUPS', 'Procedimiento', 'Servicios', 'Valor'])
    for c in resumen[1]:
        c.font, c.fill = negrita, fondo
    grupos = defaultdict(lambda: [0, Decimal(0), ''])
    for f in filas:
        g = grupos[(f['entidad'], f['cups'])]
        g[0] += 1
        g[1] += f['valor']
        g[2] = f['descripcion']
    entidad_actual = None
    for (entidad, cups), (n, valor, desc) in sorted(grupos.items()):
        if entidad_actual is not None and entidad != entidad_actual:
            resumen.append([])
        entidad_actual = entidad
        resumen.append([entidad, cups, desc, n, float(valor)])
        resumen.cell(row=resumen.max_row, column=5).number_format = '"$"#,##0'
    for letra, ancho in zip('ABCDE', (34, 11, 50, 11, 14)):
        resumen.column_dimensions[letra].width = ancho

    buffer = io.BytesIO()
    wb.save(buffer)
    respuesta = HttpResponse(buffer.getvalue(),
                             content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    respuesta['Content-Disposition'] = f'attachment; filename="Sin_facturar_SIESA_{periodo}.xlsx"'
    return respuesta


# ── CUPS sin tarifa ──────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sin_tarifa(request):
    periodo = _periodo(request)
    datos = construir(periodo)
    grupos = {}
    for l in datos['lineas']:
        if not l['sin_tarifa'] or l['cod_medico'] == SIN_MEDICO:
            continue
        clave = (l['cod_medico'], l['cod_servicio'])
        g = grupos.setdefault(clave, {
            'cod_medico': l['cod_medico'],
            'medico': datos['medicos'].get(l['cod_medico'], {}).get('nombre', ''),
            'cups': l['cod_servicio'], 'descripcion': l['descripcion'], 'lineas': 0, 'facturado': Decimal(0),
            'contratos': set(), 'motivo': next((a for a in l['alertas'] if 'tarifa' in a.lower()), 'Sin tarifa'),
        })
        g['lineas'] += 1
        g['facturado'] += l['valor_facturado']
        g['contratos'].add(l['contrato'])
    salida = [dict(g, facturado=_num(g['facturado']), contratos=sorted(g['contratos'])) for g in grupos.values()]
    salida.sort(key=lambda g: (g['medico'], -g['lineas']))
    return JsonResponse({'periodo': periodo, 'cups': salida})


# ── indicadores ──────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def indicadores(request):
    """Glosas y pagos por médico, por mes y por motivo, con las líneas ya auditadas (OK o glosa)."""
    hasta = request.GET.get('hasta') or h.periodo_anterior()
    desde = request.GET.get('desde') or hasta
    if not (re.fullmatch(r'\d{4}-\d{2}', desde) and re.fullmatch(r'\d{4}-\d{2}', hasta)):
        return JsonResponse({'error': 'Periodo no válido'}, status=400)
    lineas = AuditoriaLinea.objects.filter(periodo__gte=desde, periodo__lte=hasta).exclude(estado='PENDIENTE')
    nombres = {m['codigo']: m for m in medicos_zeus()}
    por_medico = defaultdict(lambda: {'lineas': 0, 'ok': 0, 'glosas': 0, 'glosado': Decimal(0),
                                      'pagado': Decimal(0), 'calculado': Decimal(0)})
    por_mes = defaultdict(lambda: {'pagado': Decimal(0), 'glosado': Decimal(0), 'glosas': 0, 'lineas': 0})
    motivos = defaultdict(lambda: {'glosas': 0, 'glosado': Decimal(0)})
    cups_glosados = defaultdict(lambda: {'glosas': 0, 'glosado': Decimal(0), 'descripcion': ''})
    for d in lineas:
        calculado = d.honorario_calculado if d.honorario_calculado is not None else Decimal(0)
        final = (d.honorario_final if d.honorario_final is not None
                 else h.honorario_final(calculado, d.estado, d.valor_ajustado, d.valor_glosa))
        base = d.valor_ajustado if d.valor_ajustado is not None else calculado
        glosado = (base - final) if d.estado == 'GLOSA' else Decimal(0)
        m = por_medico[d.cod_medico or 0]
        m['lineas'] += 1
        m['ok'] += d.estado == 'OK'
        m['glosas'] += d.estado == 'GLOSA'
        m['glosado'] += glosado
        m['pagado'] += final
        m['calculado'] += calculado
        mes = por_mes[d.periodo]
        mes['lineas'] += 1
        mes['pagado'] += final
        if d.estado == 'GLOSA':
            mes['glosas'] += 1
            mes['glosado'] += glosado
            motivo = ' '.join((d.observacion or 'Sin motivo').split()).capitalize()[:120]
            motivos[motivo]['glosas'] += 1
            motivos[motivo]['glosado'] += glosado
            if d.cups:
                c = cups_glosados[d.cups]
                c['glosas'] += 1
                c['glosado'] += glosado
                c['descripcion'] = d.descripcion
    medicos_salida = []
    for cod, m in por_medico.items():
        info = nombres.get(cod, {})
        medicos_salida.append({
            'cod_medico': cod, 'medico': info.get('nombre') or 'SIN MÉDICO', 'especialidad': info.get('especialidad', ''),
            'lineas': m['lineas'], 'ok': m['ok'], 'glosas': m['glosas'], 'glosado': _num(m['glosado']),
            'pagado': _num(m['pagado']), 'calculado': _num(m['calculado']),
            'pct_glosas': round(100 * m['glosas'] / m['lineas'], 1) if m['lineas'] else 0,
        })
    return JsonResponse({
        'desde': desde, 'hasta': hasta, 'medicos': medicos_salida,
        'meses': [dict(periodo=p, pagado=_num(v['pagado']), glosado=_num(v['glosado']), glosas=v['glosas'],
                       lineas=v['lineas']) for p, v in sorted(por_mes.items())],
        'motivos': sorted([dict(motivo=k, glosas=v['glosas'], glosado=_num(v['glosado'])) for k, v in motivos.items()],
                          key=lambda x: -x['glosas'])[:15],
        'cups': sorted([dict(cups=k, descripcion=v['descripcion'], glosas=v['glosas'], glosado=_num(v['glosado']))
                        for k, v in cups_glosados.items()], key=lambda x: -x['glosas'])[:15],
        'cierres': AuditoriaCierre.objects.filter(periodo__gte=desde, periodo__lte=hasta).count(),
    })


# ── tarifas ──────────────────────────────────────────────────────────────────

def _tarifa_json(t: TarifaHonorario) -> dict:
    return {'id': t.id, 'area': t.area, 'encabezado': t.encabezado, 'cod_medico': t.cod_medico,
            'medico': t.medico, 'cups': t.cups, 'descripcion': t.descripcion, 'grupo': t.grupo,
            'tipo': t.tipo, 'valor': _num(t.valor), 'porcentaje': _num(t.porcentaje),
            'max_puntos': t.max_puntos, 'escalas': t.escalas, 'por_unidad': t.por_unidad,
            'solo_contrato': t.solo_contrato, 'texto_original': t.texto_original, 'activo': t.activo,
            'texto': _texto_tarifa(t)}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tarifas(request):
    return JsonResponse({'tarifas': [_tarifa_json(t) for t in TarifaHonorario.objects.all()]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def importar_tarifas(request):
    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'error': 'Adjunte el Excel de tarifas'}, status=400)
    try:
        nuevas, avisos = h.leer_excel_tarifas(archivo, medicos_zeus(), _medicos_activos())
    except Exception as exc:
        return JsonResponse({'error': f'No se pudo leer el Excel: {exc}'}, status=400)
    if not nuevas:
        return JsonResponse({'error': 'El Excel no trae tarifas reconocibles'}, status=400)
    # Las asignaciones manuales de médico (por encabezado) se conservan al reimportar
    manuales = dict(TarifaHonorario.objects.exclude(cod_medico__isnull=True)
                    .values_list('encabezado', 'cod_medico').distinct())
    nombres = {m['codigo']: m['nombre'] for m in medicos_zeus()}
    campos = {f.name for f in TarifaHonorario._meta.get_fields()}
    with transaction.atomic():
        TarifaHonorario.objects.exclude(area='Manual').delete()   # las creadas en la pantalla se conservan
        objetos = []
        for t in nuevas:
            if t.get('cod_medico') is None and manuales.get(t['encabezado']):
                t['cod_medico'] = manuales[t['encabezado']]
                t['medico'] = nombres.get(t['cod_medico'], t['medico'])
            objetos.append(TarifaHonorario(**{k: v for k, v in t.items() if k in campos}))
        TarifaHonorario.objects.bulk_create(objetos)
    sin_medico = sorted({t['encabezado'] for t in nuevas if t.get('cod_medico') is None})
    return JsonResponse({'ok': True, 'tarifas': len(objetos), 'avisos': avisos, 'sin_medico': sin_medico})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def editar_tarifa(request, tarifa_id: int):
    t = TarifaHonorario.objects.filter(pk=tarifa_id).first()
    if not t:
        return JsonResponse({'error': 'Tarifa no encontrada'}, status=404)
    return _editar(t, request.data)


def _editar(t: TarifaHonorario, d) -> JsonResponse:
    if 'tipo' in d and d['tipo'] in dict(TarifaHonorario.TIPOS):
        t.tipo = d['tipo']
    if 'grupo' in d and d['grupo'] in dict(TarifaHonorario.GRUPOS):
        t.grupo = d['grupo']
    for campo in ('valor', 'porcentaje'):
        if campo in d:
            setattr(t, campo, _dec(d[campo]))
    if 'max_puntos' in d:
        t.max_puntos = int(d['max_puntos']) if str(d['max_puntos'] or '').strip() else None
    for campo in ('escalas', 'solo_contrato'):
        if campo in d:
            setattr(t, campo, str(d[campo] or '').strip())
    for campo in ('por_unidad', 'activo'):
        if campo in d:
            setattr(t, campo, bool(d[campo]))
    t.save()
    return JsonResponse({'ok': True, 'tarifa': _tarifa_json(t)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def crear_tarifa(request):
    """Tarifa nueva desde la pantalla (médico o CUPS que no están en el Excel)."""
    d = request.data
    try:
        cod = int(d.get('cod_medico'))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Seleccione el médico'}, status=400)
    cups = h.norm_cups(d.get('cups'))
    if not cups or cups == '0':
        return JsonResponse({'error': 'Escriba el CUPS'}, status=400)
    nombre = next((m['nombre'] for m in medicos_zeus() if m['codigo'] == cod), '')
    t = TarifaHonorario.objects.create(area='Manual', encabezado=nombre, cod_medico=cod, medico=nombre, cups=cups,
                                       descripcion=(d.get('descripcion') or '').strip()[:255])
    return _editar(t, d)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def asignar_medico(request):
    """Asocia un encabezado del Excel ('MAIRA') con un médico de Zeus en todas sus tarifas."""
    encabezado = request.data.get('encabezado') or ''
    cod = request.data.get('cod_medico')
    cod = int(cod) if str(cod or '').strip() else None
    nombre = next((m['nombre'] for m in medicos_zeus() if m['codigo'] == cod), encabezado)
    n = TarifaHonorario.objects.filter(encabezado=encabezado).update(cod_medico=cod, medico=nombre)
    return JsonResponse({'ok': True, 'actualizadas': n})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def medicos(request):
    return JsonResponse({'medicos': sorted(medicos_zeus(), key=lambda m: m['nombre'])})


# ── Lumier ───────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cargar_lumier(request):
    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'error': 'Adjunte el Excel de Lumier'}, status=400)
    try:
        filas = h.leer_lumier(archivo)
    except Exception as exc:
        return JsonResponse({'error': f'No se pudo leer el Excel: {exc}'}, status=400)
    if not filas:
        return JsonResponse({'error': 'El archivo no trae estudios (¿es el informe de Lumier?)'}, status=400)
    lista_medicos, activos = medicos_zeus(), _medicos_activos()
    asociados, sin_asociar = {}, set()
    for f in filas:
        clave = f['profesional_doc'] or f['profesional']
        if clave not in asociados:
            asociados[clave] = h.asociar_medico(f['profesional'], lista_medicos, activos)
            if asociados[clave] is None and f['profesional']:
                sin_asociar.add(f['profesional'])
        f['cod_medico'] = asociados[clave]
        f['periodo'] = f['fecha'].strftime('%Y-%m')
    periodos = sorted({f['periodo'] for f in filas})
    with transaction.atomic():
        LumierEstudio.objects.filter(periodo__in=periodos).delete()
        LumierEstudio.objects.bulk_create([LumierEstudio(**f) for f in filas])
    conteo = defaultdict(int)
    for f in filas:
        conteo[f['periodo']] += 1
    return JsonResponse({'ok': True, 'estudios': len(filas), 'periodos': dict(conteo),
                         'profesionales_sin_asociar': sorted(sin_asociar)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hallazgos_lumier(request):
    periodo = _periodo(request)
    datos = construir(periodo)
    salida = []
    for x in datos['hallazgos']:
        y = dict(x)
        y['fecha'] = x['fecha'].isoformat() if x.get('fecha') else None
        salida.append(y)
    orden = {'MEDICO_DIFERENTE': 0, 'CUPS_DIFERENTE': 1, 'SOLO_LUMIER': 2, 'SOLO_ZEUS': 3}
    salida.sort(key=lambda x: (bool(x['decision']), orden.get(x['tipo'], 9), x['fecha'] or ''))
    return JsonResponse({'periodo': periodo, 'cargado': datos['lumier_cargado'],
                         'estudios': datos['lumier_estudios'], 'hallazgos': salida})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolver_lumier(request):
    periodo = _periodo(request)
    clave = str(request.data.get('clave') or '')
    decision = request.data.get('decision') or ''
    if not re.fullmatch(r'(MD|SL|SZ|CD):\d+', clave):
        return JsonResponse({'error': 'Hallazgo no válido'}, status=400)
    if decision == '':
        ResolucionLumier.objects.filter(clave=clave).delete()
        return JsonResponse({'ok': True})
    if decision not in dict(ResolucionLumier.DECISIONES):
        return JsonResponse({'error': 'Decisión no válida'}, status=400)
    ResolucionLumier.objects.update_or_create(
        clave=clave, defaults={'periodo': periodo, 'decision': decision,
                               'nota': (request.data.get('nota') or '').strip(), 'usuario': _usuario(request)})
    return JsonResponse({'ok': True})

"""
Auditoría de honorarios médicos: tarifas, cálculo del honorario por línea de Zeus y cruce con Lumier.

Reglas acordadas (2026-11-18):
  - Se audita mes vencido (en noviembre se audita octubre), por fecha del servicio (sis_deta.fecha_servicio).
  - El médico de cada línea es sis_deta.cod_medico (Medicos.codigo).
  - "% del valor de la factura" = porcentaje del valor facturado a la entidad por esa línea (sis_deta.total).
  - Grupo "Sanitas" = contratos SANITAS EVENTO y SANITAS MRC (no Colsanitas ni Medisanitas).
  - Médicos marcados "(HORA)" en el Excel: el valor ya es por CUPS; el término hora no aplica.
  - No entran: aplicación de medicamentos (992990) ni sedación 998701 / 890226.
  - Cuando el radiólogo de Lumier no coincide con el médico de Zeus se genera un hallazgo que se valida a mano.

Reglas 2026-11-21:
  - Monitorización (la tarifa es por hora), potenciales e infiltraciones se pagan por la cantidad facturada.
  - Ecografía doppler: cantidad 1 = tarifa unilateral (64.437); cantidad 2 o más = tarifa BILATERAL del médico.
  - Médicos "(HORA)" en el Excel (Sofy, Sebastián): las consultas se pagan por las horas de su agenda del mes
    (horas × valor de la consulta del Excel); sus demás CUPS (EEG, monitorización...) se pagan por CUPS.
  - Particular de fisiatría: paquetes de Zeus 000006 / 000005 / 000004 = 1 / 2 / 4 extremidades.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from difflib import SequenceMatcher

CUPS_EXCLUIDOS = ('992990', '998701', '890226')
# CUPS de imágenes (RX 87, TAC 879, ecografía 881, doppler 882, resonancia 883, PET 9214): los que lee el
# radiólogo en Lumier
PREFIJOS_IMAGEN = ('87', '88', '9214')
DIAS_TOLERANCIA_LUMIER = 5
# Se pagan por la cantidad facturada aunque la tarifa no diga "cada" (monitorización = por hora)
POR_CANTIDAD = r'MONITORIZ|POTENCIAL|INFILTRAC'
GRUPOS_SUBENCABEZADO = {
    'PARTICULAR': 'PARTICULAR', 'ENTIDADES': 'ENTIDADES', 'OTRAS ENTIDADES': 'ENTIDADES', 'SANITAS': 'SANITAS',
}
# Especialidades de Zeus que no son médicos que cobran honorarios (no se usan al asociar nombres)
_NO_MEDICOS = ('ENFERMERIA', 'GESTION DE GLOSAS', 'SALUD FAMILIAR')


# ── texto ────────────────────────────────────────────────────────────────────

def plano(texto) -> str:
    t = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().upper()
    return ' '.join(t.split())


def tokens(texto) -> list[str]:
    return [t for t in re.split(r'[^A-Z0-9]+', plano(texto)) if len(t) >= 2]


def periodo_anterior(hoy: date | None = None) -> str:
    hoy = hoy or date.today()
    primero = hoy.replace(day=1)
    return (primero - timedelta(days=1)).strftime('%Y-%m')


def rango_periodo(periodo: str) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-11-01) [inicio, fin)."""
    anio, mes = (int(x) for x in periodo.split('-'))
    inicio = date(anio, mes, 1)
    fin = date(anio + (mes == 12), mes % 12 + 1, 1)
    return inicio, fin


# ── CUPS ─────────────────────────────────────────────────────────────────────

def norm_cups(cups) -> str:
    """'053105-10' -> '53105-10'; 891402 -> '891402'. Sin ceros a la izquierda en el código base."""
    c = str(cups or '').strip().upper()
    if re.fullmatch(r'\d+\.0', c):
        c = c[:-2]
    base, _, sufijo = c.partition('-')
    base = base.lstrip('0') or '0'
    return f"{base}-{sufijo}" if sufijo else base


def cups_base(cups) -> str:
    return norm_cups(cups).partition('-')[0]


def excluido(cod_servicio) -> bool:
    return cups_base(cod_servicio) in {norm_cups(c) for c in CUPS_EXCLUIDOS}


def es_imagen(cod_servicio) -> bool:
    return str(cups_base(cod_servicio)).startswith(PREFIJOS_IMAGEN)


# ── contrato → grupo de tarifa ───────────────────────────────────────────────

def grupo_contrato(contrato: str) -> str:
    c = plano(contrato)
    if 'PARTICULAR' in c:
        return 'PARTICULAR'
    if re.search(r'(^|[^A-Z])SANITAS (EVENTO|MRC)', c):
        return 'SANITAS'
    return 'ENTIDADES'


# Orden en que se busca la tarifa según el grupo de la línea (la última opción genera alerta)
_PREFERENCIA = {
    'SANITAS':    ['SANITAS', 'ENTIDADES', 'TODOS'],
    'ENTIDADES':  ['ENTIDADES', 'TODOS'],
    'PARTICULAR': ['PARTICULAR', 'TODOS', 'ENTIDADES'],
}


# ── médicos ──────────────────────────────────────────────────────────────────

def _palabra_igual(a: str, b: str, aproximado: bool = False) -> bool:
    if a == b:
        return True
    return aproximado and len(a) >= 5 and len(b) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.8


def _contenido(cortos: list[str], largos: list[str], aproximado: bool = False) -> bool:
    disponibles = list(largos)
    for t in cortos:
        igual = next((d for d in disponibles if _palabra_igual(t, d, aproximado)), None)
        if igual is None:
            return False
        disponibles.remove(igual)
    return True


def asociar_medico(nombre: str, medicos: list[dict], activos: set | None = None) -> int | None:
    """
    Código de Zeus del médico a partir de un nombre parcial ('PAOLA', 'JHON EDISON MOSQUERA',
    'NAVARRO GUTIERREZ PAOLA ANDREA'). Todas las palabras del nombre más corto deben estar en el otro.
    Si hay varios candidatos se prefiere el que tiene producción (activos); si sigue empatado, None.
    """
    nombre = re.sub(r'\(.*?\)', ' ', str(nombre or ''))
    buscadas = tokens(nombre)
    if not buscadas:
        return None
    candidatos = []
    # Primero palabras exactas; si nadie coincide, se toleran errores de digitación (EDISON/EDINSON),
    # solo con nombres de dos o más palabras (MAIRA y MARIA se parecen pero son personas distintas)
    for aproximado in (False, True):
        if aproximado and (candidatos or len(buscadas) < 2):
            break
        for m in medicos:
            if any(n in plano(m.get('especialidad')) for n in _NO_MEDICOS):
                continue
            propias = tokens(m.get('nombre'))
            cortos, largos = (buscadas, propias) if len(buscadas) <= len(propias) else (propias, buscadas)
            if cortos and _contenido(cortos, largos, aproximado):
                candidatos.append(int(m['codigo']))
    if len(candidatos) > 1 and activos:
        con_produccion = [c for c in candidatos if c in activos]
        if con_produccion:
            candidatos = con_produccion
    return candidatos[0] if len(candidatos) == 1 else None


# ── importación del Excel de tarifas ─────────────────────────────────────────

def _numero(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float, Decimal)):
        return Decimal(str(valor))
    t = str(valor).replace('$', '').strip()
    if re.fullmatch(r'\d{1,3}(\.\d{3})+', t):          # 40.000
        return Decimal(t.replace('.', ''))
    if re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d+)?', t):   # 40,000
        return Decimal(t.replace(',', ''))
    if re.fullmatch(r'\d+(\.\d+)?', t):
        return Decimal(t)
    return None


def interpretar_valor(valor) -> dict | None:
    """Celda de tarifa -> {'tipo', 'valor', 'porcentaje', 'max_puntos'} o None si está vacía/no se entiende."""
    if valor is None or str(valor).strip() in ('', '-'):
        return None
    n = _numero(valor)
    if n is not None:
        return {'tipo': 'FIJO', 'valor': n} if n > 0 else None
    t = plano(valor)
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*%', t)
    if m:
        return {'tipo': 'PORCENTAJE', 'porcentaje': Decimal(m.group(1).replace(',', '.'))}
    if 'PUNTO' in t:
        maximo = re.search(r'MAX\.?\s*(\d+)\s*PUNTO', t)
        resto = t[maximo.end():] if maximo else t
        cifra = re.search(r'\d{1,3}(?:[.,]\d{3})+|\d{4,}', resto)
        if cifra:
            return {'tipo': 'POR_PUNTO', 'valor': _numero(cifra.group(0)),
                    'max_puntos': int(maximo.group(1)) if maximo else None}
    return None


def _por_unidad(descripcion: str, tipo: str) -> bool:
    """La cantidad de la línea multiplica el valor: cada nervio, cada extremidad, por punto."""
    return tipo == 'POR_PUNTO' or bool(re.search(r'\bCADA\b|POR NERVIO|POR PUNTO|X PUNTO', plano(descripcion)))


def leer_excel_tarifas(archivo, medicos: list[dict], activos: set | None = None) -> tuple[list[dict], list[str]]:
    """
    Lee 'Cups Neuro - Honorarios médicos.xlsx'. Devuelve (tarifas, avisos).
    Hoja: fila 1 = CUPS | COD SOAT | DESCRIPCION | médico... ; fila 2 opcional con Particular / Entidades /
    Otras entidades / Sanitas (el médico ocupa dos columnas). Filas "N PROCEDIMIENTO(S)" sin CUPS = escalas.
    """
    import openpyxl
    wb = openpyxl.load_workbook(archivo, data_only=True)
    tarifas, avisos = [], []
    asociados: dict[str, int | None] = {}

    for ws in wb.worksheets:
        filas = [list(r) for r in ws.iter_rows(values_only=True)]
        if not filas:
            continue
        encabezado = filas[0]
        sub = filas[1] if len(filas) > 1 else []
        hay_sub = bool(sub) and not sub[0] and any(plano(c) in GRUPOS_SUBENCABEZADO for c in sub if c)
        # Columnas de médicos: desde la 4ª (después de CUPS, COD SOAT, DESCRIPCION)
        columnas, actual = [], None
        for i in range(3, len(encabezado)):
            nombre = encabezado[i]
            if nombre and str(nombre).strip():
                actual = str(nombre).strip()
            elif not hay_sub:
                actual = None
            grupo = GRUPOS_SUBENCABEZADO.get(plano(sub[i]) if hay_sub and i < len(sub) and sub[i] else '', 'TODOS')
            if actual and plano(actual) != 'PARTICULAR':
                columnas.append((i, actual, grupo))
        inicio = 2 if hay_sub else 1
        ultima_por_columna: dict[int, dict] = {}
        for fila in filas[inicio:]:
            cups, descripcion = fila[0] if fila else None, (fila[2] if len(fila) > 2 else None)
            escala = re.match(r'^\s*(\d+)\s+PROCEDIMIENTO', plano(descripcion)) if not cups else None
            if escala:
                for i, _, _ in columnas:
                    n = _numero(fila[i]) if i < len(fila) else None
                    previa = ultima_por_columna.get(i)
                    if n and previa is not None:
                        previa.setdefault('_escalas', {})[int(escala.group(1))] = n
                continue
            if cups is None or not str(cups).strip():
                continue
            for i, nombre, grupo in columnas:
                celda = fila[i] if i < len(fila) else None
                base = {
                    'area': ws.title.strip(), 'encabezado': nombre, 'cups': norm_cups(cups),
                    'descripcion': str(descripcion or '').strip()[:255], 'grupo': grupo,
                    'texto_original': str(celda if celda is not None else '')[:120],
                    'solo_contrato': 'IPS CONSULTORIO' if 'IPS CONSULTORIO' in plano(nombre) else '',
                }
                interpretado = interpretar_valor(celda)
                if interpretado is None:
                    if celda is not None and str(celda).strip() not in ('', '-'):
                        avisos.append(f"{ws.title} · {nombre} · CUPS {cups}: no se entiende el valor '{celda}'")
                    # Puede ser la fila base de un escalonado (anestesia): las escalas vienen debajo
                    ultima_por_columna[i] = dict(base, tipo='ESCALONADO')
                    continue
                t = dict(base, **interpretado)
                t['por_unidad'] = _por_unidad(t['descripcion'], t['tipo'])
                tarifas.append(t)
                ultima_por_columna[i] = t
        # Escalonados: filas base sin valor que recibieron escalas
        for t in ultima_por_columna.values():
            if t.get('_escalas') and t['tipo'] == 'ESCALONADO' and t not in tarifas:
                tarifas.append(t)

    for t in tarifas:
        t.setdefault('por_unidad', False)
        escalas = t.pop('_escalas', None)
        if escalas:
            t['tipo'] = 'ESCALONADO'
            t['escalas'] = ','.join(str(int(escalas[k])) for k in sorted(escalas))
        nombre = t['encabezado']
        if nombre not in asociados:
            asociados[nombre] = asociar_medico(nombre, medicos, activos)
            if asociados[nombre] is None:
                avisos.append(f"Médico '{nombre}' ({t['area']}): no se pudo asociar con Zeus; asígnelo en Tarifas")
        t['cod_medico'] = asociados[nombre]
        t['medico'] = next((' '.join(str(m['nombre']).split()) for m in medicos
                            if t['cod_medico'] is not None and int(m['codigo']) == t['cod_medico']), nombre)
    # Duplicados (mismo médico, CUPS y grupo con valores distintos): se avisan para revisarlos
    vistos = {}
    for t in tarifas:
        clave = (t['encabezado'], t['cups'], t['grupo'])
        if clave in vistos and vistos[clave] != t.get('valor'):
            avisos.append(f"{t['area']} · {t['encabezado']} · CUPS {t['cups']}: aparece dos veces "
                          f"({vistos[clave]} y {t.get('valor')})")
        vistos.setdefault(clave, t.get('valor'))
    return tarifas, avisos


# ── cálculo del honorario ────────────────────────────────────────────────────

def _redondear(v: Decimal) -> Decimal:
    return v.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def es_bilateral(t) -> bool:
    return 'BILATERAL' in plano(getattr(t, 'descripcion', ''))


def es_doppler(t) -> bool:
    return 'DOPPLER' in plano(getattr(t, 'descripcion', ''))


def es_tarifa_hora(t) -> bool:
    """Tarifa de un médico pagado por horas de agenda (encabezado 'SOFY (HORA)' en el Excel)."""
    return '(HORA)' in plano(getattr(t, 'encabezado', ''))


def elegir_tarifa(tarifas_medico: list, cod_servicio: str, contrato: str,
                  cantidad=1) -> tuple[object | None, list[str]]:
    """Tarifa aplicable a la línea entre las del médico. Devuelve (tarifa, alertas)."""
    alertas = []
    exacto, base = norm_cups(cod_servicio), cups_base(cod_servicio)
    candidatas = [t for t in tarifas_medico if norm_cups(t.cups) == exacto]
    if not candidatas:
        candidatas = [t for t in tarifas_medico if norm_cups(t.cups) == base]
    if not candidatas:
        # Tarifa con sufijo y línea sin él (891402-1 en el Excel, 891402 en Zeus)
        candidatas = [t for t in tarifas_medico if cups_base(t.cups) == base]
    if not candidatas:
        return None, ['Sin tarifa para este CUPS']
    if any(es_doppler(t) for t in candidatas):
        # Doppler: cantidad 1 = unilateral, 2 o más = bilateral (el Excel trae las dos filas por CUPS, o solo una:
        # entonces se toma la del mismo tipo de otro doppler del médico)
        bilateral = float(cantidad or 1) >= 2
        propias = [t for t in candidatas if es_bilateral(t) == bilateral]
        if not propias:
            # Otro doppler de miembros (8823xx) del mismo tipo: todos valen igual (64.437 / bilateral)
            propias = [t for t in tarifas_medico if es_doppler(t) and es_bilateral(t) == bilateral
                       and cups_base(t.cups)[:4] == base[:4] and 'MIEMBRO' in plano(t.descripcion)]
        if propias:
            candidatas = propias
    contrato_plano = plano(contrato)
    permitidas = [t for t in candidatas if not t.solo_contrato or plano(t.solo_contrato) in contrato_plano]
    if not permitidas:
        return None, [f"Tarifa solo para {candidatas[0].solo_contrato}"]
    grupo = grupo_contrato(contrato)
    orden = _PREFERENCIA[grupo]
    for posicion, g in enumerate(orden):
        del_grupo = [t for t in permitidas if t.grupo == g]
        if del_grupo:
            if grupo == 'PARTICULAR' and g == 'ENTIDADES':
                alertas.append('Sin tarifa particular: se usó la de entidades')
            valores = {(t.tipo, t.valor, t.porcentaje) for t in del_grupo}
            if len(valores) > 1:
                alertas.append('Tarifa duplicada con valores distintos: ' + ' / '.join(
                    str(int(t.valor)) if t.valor is not None else f"{t.porcentaje}%" for t in del_grupo))
            return del_grupo[0], alertas
    return None, [f"Sin tarifa para el grupo {grupo.lower()}"]


def calcular_honorario(tarifa, cantidad, valor_facturado, procedimientos_admision: int = 1,
                       descripcion: str = '') -> tuple[Decimal, list[str]]:
    """Honorario de una línea con su tarifa. Devuelve (valor, alertas)."""
    alertas = []
    cantidad = Decimal(str(cantidad or 1))
    if tarifa.tipo == 'PORCENTAJE':
        total = Decimal(str(valor_facturado or 0))
        if total <= 0:
            alertas.append('Línea sin valor facturado: el porcentaje da 0')
        return _redondear(total * Decimal(str(tarifa.porcentaje)) / 100), alertas
    if tarifa.tipo == 'ESCALONADO':
        escalas = [Decimal(x) for x in (tarifa.escalas or '').split(',') if x.strip()]
        if not escalas:
            return Decimal(0), ['Tarifa escalonada sin valores']
        n = min(max(procedimientos_admision, 1), len(escalas))
        alertas.append(f"{procedimientos_admision} procedimiento(s) en la admisión")
        return escalas[n - 1], alertas
    valor = Decimal(str(tarifa.valor or 0))
    if tarifa.tipo == 'POR_PUNTO':
        puntos = cantidad
        if tarifa.max_puntos and puntos > tarifa.max_puntos:
            alertas.append(f"{int(puntos)} puntos: se pagan máximo {tarifa.max_puntos}")
            puntos = Decimal(tarifa.max_puntos)
        return _redondear(valor * puntos), alertas
    if tarifa.por_unidad:
        return _redondear(valor * cantidad), alertas
    texto = plano(f"{descripcion} {getattr(tarifa, 'descripcion', '')}")
    # Monitorización (por hora), potenciales e infiltraciones: por cantidad. En particular la tarifa es por estudio.
    if re.search(POR_CANTIDAD, texto) and getattr(tarifa, 'grupo', '') != 'PARTICULAR':
        return _redondear(valor * cantidad), alertas
    if es_doppler(tarifa):
        return _redondear(valor), alertas   # la cantidad ya eligió la tarifa unilateral o bilateral
    if cantidad > 1:
        alertas.append(f"Cantidad {int(cantidad)}: la tarifa es por estudio, verificar")
    return _redondear(valor), alertas


# ── Lumier ───────────────────────────────────────────────────────────────────

def leer_lumier(archivo) -> list[dict]:
    """
    Informe de Lumier (.xlsx). Viene en una sola columna con los campos separados por '|'
    (o en columnas normales). Devuelve filas con fecha, documento, paciente, cups, examen, cantidad,
    contrato, estado, profesional, profesional_doc, id_cita.
    """
    import openpyxl
    wb = openpyxl.load_workbook(archivo, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    encabezado, salida = None, []
    for fila in ws.iter_rows(values_only=True):
        celdas = [c for c in fila]
        no_vacias = [c for c in celdas if c not in (None, '')]
        if not no_vacias:
            continue
        if len(no_vacias) == 1 and '|' in str(no_vacias[0]):
            valores = [v.strip() for v in str(no_vacias[0]).split('|')]
        else:
            valores = ['' if c is None else c for c in celdas]
        if encabezado is None:
            encabezado = [plano(v) for v in valores]
            continue
        d = dict(zip(encabezado, valores))
        fecha = d.get('FECHA CITA')
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        elif fecha:
            try:
                fecha = datetime.strptime(str(fecha)[:10], '%Y-%m-%d').date()
            except ValueError:
                fecha = None
        if not fecha or not d.get('CODIGO EXAMEN'):
            continue
        profesional = str(d.get('PROFESIONAL') or '').strip()
        doc_prof, _, nombre_prof = profesional.partition(' - ')
        if not nombre_prof:
            doc_prof, nombre_prof = '', profesional
        try:
            cantidad = int(float(d.get('# ESTUDIOS') or 1))
        except ValueError:
            cantidad = 1
        salida.append({
            'fecha': fecha,
            'documento': re.sub(r'\D', '', str(d.get('ID PACIENTE') or '')),
            'paciente': ' '.join(str(d.get('PACIENTE') or '').split())[:200],
            'cups': norm_cups(d.get('CODIGO EXAMEN')),
            'examen': str(d.get('EXAMEN') or '')[:255],
            'cantidad': cantidad,
            'contrato': str(d.get('CONTRATO') or '')[:255],
            'estado': str(d.get('ESTADO ESTUDIO') or '')[:60],
            'profesional': ' '.join(nombre_prof.split())[:200],
            'profesional_doc': re.sub(r'\D', '', doc_prof)[:30],
            'id_cita': str(d.get('ID CITA EXAMEN') or '')[:40],
        })
    return salida


def cruzar_lumier(lineas_imagen: list[dict], estudios: list) -> tuple[dict, dict, list]:
    """
    Empareja líneas de imágenes de Zeus con estudios de Lumier por documento del paciente + CUPS base + fecha
    (±DIAS_TOLERANCIA_LUMIER). Una línea con cantidad N puede corresponder a N filas de Lumier.
    Segunda pasada: mismo paciente y fecha pero otro CUPS (posible error de CUPS en uno de los dos sistemas).
    Devuelve ({sis_deta_id: [estudios]}, {sis_deta_id: [estudios con otro CUPS]}, estudios_sin_pareja).
    """
    def cerca(e, linea):
        return linea['fecha'] and abs((e.fecha - linea['fecha']).days) <= DIAS_TOLERANCIA_LUMIER

    por_clave: dict[tuple, list] = {}
    for e in estudios:
        por_clave.setdefault((e.documento, cups_base(e.cups)), []).append(e)
    usados, pareja = set(), {}
    ordenadas = sorted(lineas_imagen, key=lambda l: l['fecha'] or date.min)
    for linea in ordenadas:
        opciones = sorted((e for e in por_clave.get((linea['documento'], cups_base(linea['cod_servicio'])), [])
                           if e.pk not in usados and cerca(e, linea)),
                          key=lambda e: abs((e.fecha - linea['fecha']).days))
        cupo = max(int(linea.get('cantidad') or 1), 1)
        elegidos = opciones[:cupo]
        if elegidos:
            usados.update(e.pk for e in elegidos)
            pareja[linea['sis_deta_id']] = elegidos

    por_documento: dict[str, list] = {}
    for e in estudios:
        if e.pk not in usados:
            por_documento.setdefault(e.documento, []).append(e)
    otro_cups = {}
    for linea in ordenadas:
        if linea['sis_deta_id'] in pareja:
            continue
        opciones = [e for e in por_documento.get(linea['documento'], [])
                    if e.pk not in usados and cerca(e, linea)]
        if opciones:
            elegido = min(opciones, key=lambda e: abs((e.fecha - linea['fecha']).days))
            usados.add(elegido.pk)
            otro_cups[linea['sis_deta_id']] = [elegido]
    sin_pareja = [e for e in estudios if e.pk not in usados]
    return pareja, otro_cups, sin_pareja


# ── agenda (médicos pagados por hora) ────────────────────────────────────────

def horas_agenda(bloques: list[dict]) -> list[dict]:
    """
    Bloques de agenda de Zeus [{'dia', 'inicio' ('07:30'), 'fin' (hora del último cupo), 'intervalo' (min),
    'cupos'}] → días [{'dia', 'horas', 'bloques': [{'inicio', 'fin', 'horas'}]}]. Las horas de un bloque son
    cupos × intervalo (de 07:30 a 12:00 con cupos de 30 min = 10 cupos = 5 h, hasta las 12:30).
    """
    dias: dict = {}
    for b in sorted(bloques, key=lambda x: (x['dia'], x['inicio'])):
        horas = Decimal(int(b['cupos']) * int(b['intervalo'] or 0)) / 60
        h_fin, m_fin = (int(x) for x in b['fin'].split(':'))
        total_min = h_fin * 60 + m_fin + int(b['intervalo'] or 0)
        fin = f"{total_min // 60:02d}:{total_min % 60:02d}"
        d = dias.setdefault(b['dia'], {'dia': b['dia'], 'horas': Decimal(0), 'bloques': []})
        d['horas'] += horas
        d['bloques'].append({'inicio': b['inicio'], 'fin': fin, 'horas': horas})
    return [dias[k] for k in sorted(dias)]


def id_agenda(dia: date, cod_medico: int) -> int:
    """Identificador (negativo, para no chocar con sis_deta.id) de la línea de agenda de un médico en un día."""
    return -((dia - date(2020, 1, 1)).days * 10000 + int(cod_medico))


def honorario_final(calculado, estado: str, valor_ajustado=None, valor_glosa=None) -> Decimal:
    """
    Lo que se paga de la línea: el calculado (o el ajustado); con GLOSA se descuenta el valor glosado, y si la
    glosa no dice valor se glosa todo.
    """
    base = Decimal(str(valor_ajustado)) if valor_ajustado is not None else Decimal(str(calculado or 0))
    if estado != 'GLOSA':
        return base
    if valor_glosa is None:
        return Decimal(0)
    return max(base - Decimal(str(valor_glosa)), Decimal(0))

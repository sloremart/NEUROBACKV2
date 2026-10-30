"""
Verificación del recaudo (cuota moderadora / copago) de una admisión.

La IA lee de la AUTORIZACIÓN (o del validador de derechos de la EPS) las condiciones de pago:
si requiere pago, el tipo de cobro, el valor o porcentaje, la categoría, el tipo de afiliado y si
alguna alerta lo exonera. El sistema lo compara con lo recaudado en SIESA (tabla `pagos`) y, para
Sanitas, con las reglas del contrato.

Reglas Sanitas (EPS005):
  Subsidiado: solo paga el nivel Z (10 % del valor de los servicios).
  Contributivo:
    - Consultas: cuota moderadora según categoría (cotizantes y beneficiarios).
    - Exámenes/estudios: beneficiario → copago (% según categoría);
                         cotizante → cuota moderadora según categoría.
    - Alerta con "EXONERACION PRESTACION": no se cobra.
"""

import logging
import re

from django.db import connections

logger = logging.getLogger(__name__)

EMPRESAS_SANITAS = ('EPS005',)
REGIMEN_CONTRIBUTIVO, REGIMEN_SUBSIDIADO = 1, 2

CUOTA_MODERADORA_SANITAS = {'A': 5000, 'B': 20100, 'C': 52800}
COPAGO_SANITAS = {'A': 11.5, 'B': 17.3, 'C': 23.0}
COPAGO_NIVEL_Z = 10.0
PORCENTAJE_COPAGO_MAXIMO = 50.0

# Zeus TipoAfiliado: 1 Cotizante, 2 Beneficiario, 3 Sustituto de pensión, 4 Pensionado
AFILIADO_ZEUS = {1: 'COTIZANTE', 2: 'BENEFICIARIO', 3: 'COTIZANTE', 4: 'COTIZANTE'}

TOLERANCIA_PESOS = 100   # SIESA redondea el copago a la centena


def _pesos(valor) -> str:
    return f"${int(round(valor)):,}".replace(",", ".")


def _numero(valor):
    """'20.100', '$ 5,000', '11.5', 11.5 → float; '' o texto → None."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    t = str(valor).strip().replace('$', '').replace(' ', '')
    if not t or not re.search(r'\d', t):
        return None
    if re.fullmatch(r'[1-9]\d{0,2}([.,]\d{3})+', t):     # separador de miles ('0.115' no lo es)
        t = re.sub(r'[.,]', '', t)
    t = t.replace(',', '.')
    try:
        return float(re.sub(r'[^\d.]', '', t))
    except ValueError:
        return None


def _opcion(valor) -> str:
    return str(valor or '').upper().strip().replace(' ', '_')


def datos_recaudo_zeus(estudio: int) -> dict | None:
    """Contrato, régimen, tipo de afiliado, servicios y recaudos válidos de la admisión en Zeus."""
    try:
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute(
                "SELECT c.empresa, c.regimen, c.alias, sp.tipo_afilia "
                "FROM sis_maes sm JOIN contratos c ON c.codigo = sm.contrato "
                "LEFT JOIN sis_paci sp ON sp.autoid = sm.autoid WHERE sm.con_estudio = %s", [estudio])
            fila = cursor.fetchone()
            if not fila:
                return None
            cursor.execute("SELECT LTRIM(RTRIM(descripcion)), ISNULL(total, 0) FROM sis_deta WHERE estudio = %s",
                           [estudio])
            servicios = [(d or '', float(t or 0)) for d, t in cursor.fetchall()]
            cursor.execute(
                "SELECT UPPER(LTRIM(RTRIM(descripcion))), SUM(valor) FROM pagos "
                "WHERE con_estudio = %s AND activo = 1 GROUP BY UPPER(LTRIM(RTRIM(descripcion)))", [estudio])
            cobros = {d: float(v or 0) for d, v in cursor.fetchall()}
    except Exception as exc:
        logger.warning("No se pudo leer el recaudo de la admisión %s: %s", estudio, exc)
        return None
    return {
        "empresa": (fila[0] or '').strip(),
        "regimen": fila[1],
        "contrato": (fila[2] or '').strip(),
        "afiliado_zeus": AFILIADO_ZEUS.get(fila[3]),
        "servicios": servicios,
        "valor_servicios": sum(t for _, t in servicios),
        "consultas": [('CONSULTA' in d.upper()) for d, _ in servicios],
        "cuota_moderadora": cobros.get('CUOTA MODERADORA', 0.0),
        "copago": cobros.get('COPAGO', 0.0),
    }


def _tipo_por_servicio(z: dict):
    """CUOTA_MODERADORA si todos los servicios son consultas, COPAGO si ninguno lo es; None si hay de ambos."""
    consultas = z["consultas"]
    if consultas and all(consultas):
        return 'CUOTA_MODERADORA'
    if consultas and not any(consultas):
        return 'COPAGO'
    return None


def _esperado_sanitas(z: dict, categoria: str, afiliado: str):
    """(tipo, valor) que debía recaudarse según las reglas de Sanitas, o None si no se puede calcular."""
    if z["regimen"] == REGIMEN_SUBSIDIADO:
        if categoria == 'Z':
            return 'COPAGO', z["valor_servicios"] * COPAGO_NIVEL_Z / 100
        return ('NINGUNO', 0.0) if categoria in ('A', 'B', 'C', '1', '2', '3') else None
    if z["regimen"] != REGIMEN_CONTRIBUTIVO or categoria not in CUOTA_MODERADORA_SANITAS:
        return None
    consultas = z["consultas"]
    if consultas and all(consultas):
        return 'CUOTA_MODERADORA', CUOTA_MODERADORA_SANITAS[categoria]
    if consultas and not any(consultas):
        if afiliado == 'BENEFICIARIO':
            return 'COPAGO', z["valor_servicios"] * COPAGO_SANITAS[categoria] / 100
        if afiliado == 'COTIZANTE':
            return 'CUOTA_MODERADORA', CUOTA_MODERADORA_SANITAS[categoria]
    return None   # consulta y estudio en la misma admisión, o afiliado desconocido


def verificar_recaudo(recaudo_doc: dict, estudio) -> tuple[str, str] | None:
    """
    Compara las condiciones de pago leídas del documento con lo recaudado en SIESA.
    Devuelve (tipo_hallazgo, descripción) o None si está bien o no hay datos para decidir.
    """
    if not isinstance(recaudo_doc, dict) or not estudio:
        return None
    requiere = _opcion(recaudo_doc.get('requiere_pago'))
    tipo_doc = _opcion(recaudo_doc.get('tipo_cobro'))
    valor_doc = _numero(recaudo_doc.get('valor'))
    porcentaje_doc = _numero(recaudo_doc.get('porcentaje'))
    categoria = _opcion(recaudo_doc.get('categoria'))
    afiliado_doc = _opcion(recaudo_doc.get('tipo_afiliado'))
    exonerado = _opcion(recaudo_doc.get('exoneracion')) == 'SI'
    # Fracción en lugar de porcentaje o de pesos ("Recaudo de pago: 0.115" = 11,5 %)
    if valor_doc and valor_doc < 1:
        porcentaje_doc, valor_doc = porcentaje_doc or valor_doc, None
    if porcentaje_doc and porcentaje_doc < 1:
        porcentaje_doc *= 100
    # Un copago nunca es la mitad o más del servicio: "Porcentaje: 100%" con "Valor: 0" (Salud Total) es la
    # cobertura, no el copago
    cobertura = bool(porcentaje_doc and porcentaje_doc >= PORCENTAJE_COPAGO_MAXIMO)
    if cobertura:
        porcentaje_doc = None
    if porcentaje_doc and tipo_doc not in ('CUOTA_MODERADORA', 'COPAGO'):
        tipo_doc = 'COPAGO'
    if (requiere in ('', 'NO_INDICA') and categoria in ('', 'NO_INDICA') and not exonerado
            and valor_doc is None and porcentaje_doc is None):
        return None   # el documento no dice nada del pago

    z = datos_recaudo_zeus(int(estudio))
    if not z or z["regimen"] not in (REGIMEN_CONTRIBUTIVO, REGIMEN_SUBSIDIADO):
        return None
    cm, cp = z["cuota_moderadora"], z["copago"]
    cobrado = cm + cp
    cobrado_txt = " y ".join(t for t in (f"cuota moderadora {_pesos(cm)}" if cm else '',
                                          f"copago {_pesos(cp)}" if cp else '') if t) or "nada"
    if 'MRC' in z["contrato"].upper():
        # Sanitas MRC: el tipo de afiliado está en la pestaña MP de la plataforma, que no sale en el
        # pantallazo de la autorización; lo que lea la IA no es confiable y vale lo de Zeus
        afiliado_doc = ''
    afiliado = afiliado_doc if afiliado_doc in ('COTIZANTE', 'BENEFICIARIO') else z["afiliado_zeus"]
    notas = []
    if (afiliado_doc in ('COTIZANTE', 'BENEFICIARIO') and z["afiliado_zeus"]
            and afiliado_doc != z["afiliado_zeus"]):
        notas.append(f"Tipo de afiliado en Zeus {z['afiliado_zeus'].lower()}, la autorización dice "
                     f"{afiliado_doc.lower()}")

    # 1. No requiere pago (dice "no requiere pago", recaudo 0, o alerta de exoneración).
    # Un porcentaje o valor mayor a 0 prevalece sobre otro campo en 0
    # (ej. "Valor pesos pago compartido: 0.0" con "Porcentaje Copago: 11.5").
    con_cobro = bool(porcentaje_doc or valor_doc)
    sin_pago = exonerado or (not con_cobro and (requiere == 'NO'
                                                or (valor_doc == 0 and (requiere != 'SI' or cobertura))))
    if sin_pago:
        if cobrado > TOLERANCIA_PESOS:
            motivo = "una alerta de exoneración" if exonerado else "que no requiere pago"
            return 'RECAUDO', "; ".join([f"Se recaudó {cobrado_txt}, pero la autorización indica {motivo}"] + notas)
        return ('INFORMACION', notas[0]) if notas else None

    # 2. Lo que debía cobrarse: lo que dice el documento o, en Sanitas, las reglas del contrato
    esperado_tipo, esperado_valor = None, None
    if tipo_doc in ('CUOTA_MODERADORA', 'COPAGO'):
        esperado_tipo = tipo_doc
        if valor_doc:
            esperado_valor = valor_doc
        elif porcentaje_doc and tipo_doc == 'COPAGO':
            esperado_valor = z["valor_servicios"] * porcentaje_doc / 100
    if z["empresa"] in EMPRESAS_SANITAS:
        regla = _esperado_sanitas(z, categoria, afiliado)
        if regla:
            if esperado_tipo is None:
                esperado_tipo = regla[0]
            if esperado_valor is None and regla[0] == esperado_tipo:
                esperado_valor = regla[1]
    if esperado_tipo is None:
        return ('INFORMACION', notas[0]) if notas else None

    nombre = {'CUOTA_MODERADORA': 'cuota moderadora', 'COPAGO': 'copago'}
    cat_txt = f" (categoría {categoria})" if categoria in ('A', 'B', 'C', 'Z') else ''
    debia = nombre.get(esperado_tipo, '') + (f" de {_pesos(esperado_valor)}" if esperado_valor else '') + cat_txt

    if esperado_tipo == 'NINGUNO':
        if cobrado > TOLERANCIA_PESOS:
            return 'RECAUDO', "; ".join([f"Se recaudó {cobrado_txt}; según la categoría{cat_txt} no "
                                         f"corresponde cobro"] + notas)
        return ('INFORMACION', notas[0]) if notas else None
    if cobrado <= TOLERANCIA_PESOS:
        return 'RECAUDO', "; ".join([f"No se recaudó; debía cobrarse {debia}"] + notas)
    cobrado_tipo = 'CUOTA_MODERADORA' if cm >= cp else 'COPAGO'
    # Fuera de Sanitas el tipo lo da el servicio: consultas → cuota moderadora; procedimientos (RX, RM, TAC,
    # electro…) → copago, aunque la autorización lo llame cuota moderadora. Se valida solo el valor.
    if (z["empresa"] not in EMPRESAS_SANITAS and cobrado_tipo != esperado_tipo
            and cobrado_tipo == _tipo_por_servicio(z)):
        esperado_tipo = cobrado_tipo
    if cobrado_tipo != esperado_tipo:
        return 'RECAUDO', "; ".join([f"Tipo de cobro incorrecto: se recaudó {cobrado_txt}; debía ser {debia}"]
                                    + notas)
    if esperado_valor and abs(cobrado - esperado_valor) > TOLERANCIA_PESOS:
        return 'RECAUDO', "; ".join([f"Valor recaudado distinto: {cobrado_txt}; debía ser {debia}"] + notas)
    return ('INFORMACION', notas[0]) if notas else None

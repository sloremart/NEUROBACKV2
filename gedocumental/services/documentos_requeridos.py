"""
Documentos que el admisionista debe cargar en cada admisión, según el contrato (regla única para la pestaña
"Documentos faltantes", la bandeja de Cuentas Médicas y Gestión de facturación).

  Capital Salud, Compensar, Ejército, FOMAG, Multisalud, Oncolife, Policía, Salud Total, Sanitas
  (y por defecto cualquier otro contrato) .......... ORDEN + AUTORIZACION + COMPROBANTE
  Colsanitas ....................................... AUTORIZACION + COMPROBANTE; ORDEN solo en consultas
  IPS Consultorio y Equipo Interdisciplinario
  (la orden viene en la autorización) .............. ORDEN o AUTORIZACION + COMPROBANTE
  MRC .............................................. ORDEN + COMPROBANTE (no lleva autorización)
  IPS Solimed ...................................... solo COMPROBANTE (orden y autorización no son obligatorias)
  Bolívar Póliza ................................... ORDEN + COMPROBANTE (no requiere autorización)
  Particular ....................................... no se validan documentos

El RESULTADO no está aquí: lo carga la coordinación médica.
"""

import unicodedata

from django.db import connections

ORDEN, AUTORIZACION, COMPROBANTE = 'ORDEN', 'AUTORIZACION', 'COMPROBANTE'
# Contratos en los que la orden viene en el mismo documento que la autorización: basta con cualquiera de los dos
CONTRATOS_ORDEN_O_AUTORIZACION = ('IPS CONSULTORIO', 'EQUIPO INTERDISCIPLINARIO')
# Contratos en los que la orden y la autorización NO son obligatorias: solo el comprobante (regla 2026-11-19)
CONTRATOS_SOLO_COMPROBANTE = ('SOLIMED',)
# Contratos que no requieren autorización (ni el documento ni el número en SIESA), como MRC (regla 2026-11-19)
CONTRATOS_SIN_AUTORIZACION = ('BOLIVAR POLIZA', 'COLMEDICA LINEA ZAFIRO', 'COLSANITAS PLAN INTEGRAL')
# Colsanitas: contratos en que la consulta de PRIMERA VEZ no requiere orden (el control sí)
COLSANITAS_SIN_ORDEN_PRIMERA_VEZ = ('COLSANITAS PLAN INTEGRAL',)
UFUNCIONAL_CONSULTA = 1   # CONSULTA EXTERNA en Zeus


def _plano(texto) -> str:
    return unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().upper()


def es_particular(contrato: str) -> bool:
    """
    Contrato PARTICULAR (tarifa plena, descuentos, bono, liga): el paciente paga. Sus admisiones solo cargan
    documentos: no se revisan con IA, no pasan por Cuentas Médicas ni por la validación de resultados.
    """
    return 'PARTICULAR' in _plano(contrato)


def admisiones_particulares(admision_ids) -> set:
    """Admisiones (con_estudio) de un contrato particular, según ZeusSalud."""
    return {adm for adm, d in datos_admisiones(admision_ids).items() if es_particular(d['contrato'])}


def admisiones_excluidas(admision_ids) -> set:
    """Admisiones que solo cargan documentos o ya no aplican: particulares y anuladas en SIESA."""
    return {adm for adm, d in datos_admisiones(admision_ids).items() if d['anulada'] or es_particular(d['contrato'])}


def requisitos(contrato: str, es_consulta: bool, primera_vez: bool = False) -> list[tuple[str, ...]]:
    """
    primera_vez: consulta de primera vez (CUPS 8902xx); Colsanitas Plan Integral no pide orden en ellas.
    Lista de requisitos; cada uno es una tupla de tipos alternativos (basta con tener uno de ellos).
    Ej. IPS Consultorio: [('ORDEN', 'AUTORIZACION'), ('COMPROBANTE',)].
    """
    c = _plano(contrato)
    if es_particular(c):
        return []
    if any(n in c for n in CONTRATOS_SOLO_COMPROBANTE):
        return [(COMPROBANTE,)]
    sin_autorizacion = any(n in c for n in CONTRATOS_SIN_AUTORIZACION)
    if 'COLSANITAS' in c:
        # Plan Integral: la consulta de primera vez no requiere orden; el control sí (regla 2026-11-19)
        pide_orden = es_consulta and not (primera_vez and any(n in c for n in COLSANITAS_SIN_ORDEN_PRIMERA_VEZ))
        return (([(ORDEN,)] if pide_orden else []) + ([] if sin_autorizacion else [(AUTORIZACION,)])
                + [(COMPROBANTE,)])
    if any(n in c for n in CONTRATOS_ORDEN_O_AUTORIZACION):
        return [(ORDEN, AUTORIZACION), (COMPROBANTE,)]
    if 'MRC' in c or sin_autorizacion:
        return [(ORDEN,), (COMPROBANTE,)]
    return [(ORDEN,), (AUTORIZACION,), (COMPROBANTE,)]


def faltantes(contrato: str, es_consulta: bool, tipos_cargados, primera_vez: bool = False) -> list[str]:
    """Requisitos sin cumplir, para mostrar: ['ORDEN', 'ORDEN o AUTORIZACION', ...]."""
    tipos = {(t or '').upper() for t in tipos_cargados}
    return [' o '.join(r) for r in requisitos(contrato, es_consulta, primera_vez) if not tipos & set(r)]


def cumple(contrato: str, es_consulta: bool, tipos, primera_vez: bool = False) -> bool:
    """True si los tipos dados (cargados, o aprobados por la IA) cubren todos los requisitos."""
    return not faltantes(contrato, es_consulta, tipos, primera_vez)


def datos_admisiones(admision_ids) -> dict:
    """
    {admisión: {'contrato', 'es_consulta', 'primera_vez', 'anulada'}} desde ZeusSalud. primera_vez: tiene consulta
    de primera vez (CUPS 8902xx) y ninguna de control (8903xx), sin contar cargos revertidos. Una admisión ANULADA en SIESA (el paciente
    canceló y no se hizo el examen) no exige documentos ni pasa por Cuentas Médicas.
    """
    ids = [int(i) for i in admision_ids if i]
    resultado = {}
    with connections['zeussalud'].cursor() as cursor:
        for i in range(0, len(ids), 1000):
            bloque = ids[i:i + 1000]
            cursor.execute(
                f"""SELECT sm.con_estudio,
                           LTRIM(RTRIM(ISNULL(NULLIF(LTRIM(RTRIM(c.alias)), ''), ISNULL(c.nombre, '')))),
                           sm.ufuncional, ISNULL(sm.estado, ''),
                           CASE WHEN EXISTS (SELECT 1 FROM sis_deta d WHERE d.estudio = sm.con_estudio
                                             AND ISNULL(d.ItemRevertido, 0) = 0
                                             AND LEFT(LTRIM(d.cod_servicio), 4) = '8902')
                                 AND NOT EXISTS (SELECT 1 FROM sis_deta d WHERE d.estudio = sm.con_estudio
                                             AND ISNULL(d.ItemRevertido, 0) = 0
                                             AND LEFT(LTRIM(d.cod_servicio), 4) = '8903')
                                THEN 1 ELSE 0 END
                    FROM sis_maes sm LEFT JOIN contratos c ON sm.contrato = c.codigo
                    WHERE sm.con_estudio IN ({', '.join(str(x) for x in bloque)})""")
            for estudio, contrato, ufuncional, estado, primera_vez in cursor.fetchall():
                resultado[int(estudio)] = {'contrato': contrato or '',
                                           'es_consulta': ufuncional == UFUNCIONAL_CONSULTA,
                                           'primera_vez': bool(primera_vez),
                                           'anulada': (estado or '').strip().upper() == 'N'}
    return resultado

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


def requisitos(contrato: str, es_consulta: bool) -> list[tuple[str, ...]]:
    """
    Lista de requisitos; cada uno es una tupla de tipos alternativos (basta con tener uno de ellos).
    Ej. IPS Consultorio: [('ORDEN', 'AUTORIZACION'), ('COMPROBANTE',)].
    """
    c = _plano(contrato)
    if es_particular(c):
        return []
    if any(n in c for n in CONTRATOS_SOLO_COMPROBANTE):
        return [(COMPROBANTE,)]
    if 'COLSANITAS' in c:
        return ([(ORDEN,)] if es_consulta else []) + [(AUTORIZACION,), (COMPROBANTE,)]
    if any(n in c for n in CONTRATOS_ORDEN_O_AUTORIZACION):
        return [(ORDEN, AUTORIZACION), (COMPROBANTE,)]
    if 'MRC' in c:
        return [(ORDEN,), (COMPROBANTE,)]
    return [(ORDEN,), (AUTORIZACION,), (COMPROBANTE,)]


def faltantes(contrato: str, es_consulta: bool, tipos_cargados) -> list[str]:
    """Requisitos sin cumplir, para mostrar: ['ORDEN', 'ORDEN o AUTORIZACION', ...]."""
    tipos = {(t or '').upper() for t in tipos_cargados}
    return [' o '.join(r) for r in requisitos(contrato, es_consulta) if not tipos & set(r)]


def cumple(contrato: str, es_consulta: bool, tipos) -> bool:
    """True si los tipos dados (cargados, o aprobados por la IA) cubren todos los requisitos."""
    return not faltantes(contrato, es_consulta, tipos)


def datos_admisiones(admision_ids) -> dict:
    """
    {admisión: {'contrato', 'es_consulta', 'anulada'}} desde ZeusSalud. Una admisión ANULADA en SIESA (el paciente
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
                           sm.ufuncional, ISNULL(sm.estado, '')
                    FROM sis_maes sm LEFT JOIN contratos c ON sm.contrato = c.codigo
                    WHERE sm.con_estudio IN ({', '.join(str(x) for x in bloque)})""")
            for estudio, contrato, ufuncional, estado in cursor.fetchall():
                resultado[int(estudio)] = {'contrato': contrato or '',
                                           'es_consulta': ufuncional == UFUNCIONAL_CONSULTA,
                                           'anulada': (estado or '').strip().upper() == 'N'}
    return resultado

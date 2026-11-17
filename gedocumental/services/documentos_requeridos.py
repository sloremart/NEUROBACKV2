"""
Documentos que el admisionista debe cargar en cada admisión, según el contrato (regla única para la pestaña
"Documentos faltantes", la bandeja de Cuentas Médicas y Gestión de facturación).

  Capital Salud, Compensar, Ejército, FOMAG, Multisalud, Oncolife, Policía, Salud Total, Sanitas
  (y por defecto cualquier otro contrato) .......... ORDEN + AUTORIZACION + COMPROBANTE
  Colsanitas ....................................... AUTORIZACION + COMPROBANTE; ORDEN solo en consultas
  IPS Consultorio y Equipo Interdisciplinario
  (la orden viene en la autorización) .............. ORDEN o AUTORIZACION + COMPROBANTE
  MRC .............................................. ORDEN + COMPROBANTE (no lleva autorización)
  Particular ....................................... no se validan documentos

El RESULTADO no está aquí: lo carga la coordinación médica.
"""

import unicodedata

from django.db import connections

ORDEN, AUTORIZACION, COMPROBANTE = 'ORDEN', 'AUTORIZACION', 'COMPROBANTE'
# Contratos en los que la orden viene en el mismo documento que la autorización: basta con cualquiera de los dos
CONTRATOS_ORDEN_O_AUTORIZACION = ('IPS CONSULTORIO', 'EQUIPO INTERDISCIPLINARIO')
UFUNCIONAL_CONSULTA = 1   # CONSULTA EXTERNA en Zeus


def _plano(texto) -> str:
    return unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode().upper()


def requisitos(contrato: str, es_consulta: bool) -> list[tuple[str, ...]]:
    """
    Lista de requisitos; cada uno es una tupla de tipos alternativos (basta con tener uno de ellos).
    Ej. IPS Consultorio: [('ORDEN', 'AUTORIZACION'), ('COMPROBANTE',)].
    """
    c = _plano(contrato)
    if 'PARTICULAR' in c:
        return []
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
    """{admisión: {'contrato': alias o nombre del contrato, 'es_consulta': bool}} desde ZeusSalud."""
    ids = [int(i) for i in admision_ids if i]
    resultado = {}
    with connections['zeussalud'].cursor() as cursor:
        for i in range(0, len(ids), 1000):
            bloque = ids[i:i + 1000]
            cursor.execute(
                f"""SELECT sm.con_estudio,
                           LTRIM(RTRIM(ISNULL(NULLIF(LTRIM(RTRIM(c.alias)), ''), ISNULL(c.nombre, '')))),
                           sm.ufuncional
                    FROM sis_maes sm LEFT JOIN contratos c ON sm.contrato = c.codigo
                    WHERE sm.con_estudio IN ({', '.join(str(x) for x in bloque)})""")
            for estudio, contrato, ufuncional in cursor.fetchall():
                resultado[int(estudio)] = {'contrato': contrato or '',
                                           'es_consulta': ufuncional == UFUNCIONAL_CONSULTA}
    return resultado

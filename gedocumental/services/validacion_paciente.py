"""
Validación de datos obligatorios del paciente en ZeusSalud (sis_paci).

No usa IA: compara cada campo contra los catálogos que usa Zeus para RIPS
(spFEVRIPS) y el Resumen Digital de Atención (spRDA_AtencionPaciente):
  - IdentidadGenero  (sismaelm GENERO):  1 Masculino, 2 Femenino, 3 Transgénero, 4 Neutro, 5 No lo declara
  - OrientacionSexual(sismaelm ORINSEX): 1..5  (0 / NULL = sin diligenciar) — NO se valida: no es obligatoria
  - PertenenciaEtnica(sismaelm ETNIA):   1..7  (7 = Ninguna de las anteriores)
  - discapacidad     (sismaelm DISCAP):  1..8  (8 = Sin discapacidad)
  Zona territorial y ocupación ya no se validan.

"No lo declara", "Ninguna" y "Sin discapacidad" son respuestas válidas: no se marcan.
"""

import logging

from django.db import connections

logger = logging.getLogger(__name__)

TELEFONOS_FICTICIOS = {'', '0', '3000000000', '300000000', '30000000', '0000000000'}
DIRECCIONES_FICTICIAS = {'', 'SIN DIRECCION', 'NO TIENE', 'N/A', 'NA', 'NINGUNA'}


def _txt(valor) -> str:
    return (str(valor) if valor is not None else '').strip()


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


CAMPOS_SQL = """
    sm.con_estudio,
    sp.tipo_id, sp.num_id, sp.primer_nom, sp.primer_ape, sp.fecha_naci, sp.sexo,
    sp.IdentidadGenero, sp.OrientacionSexual, sp.PertenenciaEtnica, sp.CodigoComunidadEtnica,
    sp.discapacidad,
    sp.cod_dep, sp.cod_muni, sp.codPaisResidencia, sp.IdPais,
    sp.direccion, sp.telefono, sp.celular
"""


def validar_datos_paciente(consecutivo) -> dict | None:
    """
    Devuelve {"completo": bool, "faltantes": [{"campo", "motivo", "obligatorio"}]}
    o None si no se pudo consultar la admisión.
    """
    return validar_datos_pacientes_bulk([consecutivo]).get(int(consecutivo))


def validar_datos_pacientes_bulk(consecutivos) -> dict:
    """{consecutivo: resultado} para varias admisiones (consultas por bloques de 500)."""
    ids = [int(c) for c in consecutivos]
    resultados = {}
    try:
        with connections['zeussalud'].cursor() as cursor:
            for i in range(0, len(ids), 500):
                bloque = ids[i:i + 500]
                marcadores = ', '.join(['%s'] * len(bloque))
                cursor.execute(
                    f"SELECT {CAMPOS_SQL} FROM sis_maes sm JOIN sis_paci sp ON sp.autoid = sm.autoid "
                    f"WHERE sm.con_estudio IN ({marcadores})",
                    bloque,
                )
                for fila in cursor.fetchall():
                    resultados[int(fila[0])] = _evaluar(fila[1:])
    except Exception as exc:
        logger.warning("No se pudo validar datos de pacientes: %s", exc)
    return resultados


def _evaluar(fila) -> dict:
    (tipo_id, num_id, nombre, apellido, fecha_naci, sexo,
     identidad, orientacion, etnia, comunidad,
     discapacidad,
     cod_dep, cod_muni, pais_res, nacionalidad,
     direccion, telefono, celular) = fila

    faltantes = []

    def falta(campo, motivo, obligatorio=True):
        faltantes.append({"campo": campo, "motivo": motivo, "obligatorio": obligatorio})

    # Identificación básica
    if not _txt(tipo_id):
        falta("Tipo de documento", "Sin diligenciar")
    if not _txt(num_id):
        falta("Número de documento", "Sin diligenciar")
    if not _txt(nombre):
        falta("Primer nombre", "Sin diligenciar")
    if not _txt(apellido):
        falta("Primer apellido", "Sin diligenciar")
    if fecha_naci is None:
        falta("Fecha de nacimiento", "Sin diligenciar")
    if _txt(sexo).upper() not in ('F', 'M', 'I'):
        falta("Sexo", "Sin diligenciar")

    # Variables sociodemográficas (RIPS / RDA)
    if _entero(identidad) not in (1, 2, 3, 4, 5):
        falta("Identidad de género", "Sin diligenciar")
    if _entero(etnia) not in (1, 2, 3, 4, 5, 6, 7):
        falta("Pertenencia étnica", "Sin diligenciar")
    elif _entero(etnia) == 1 and not _txt(comunidad):
        falta("Comunidad étnica", "Paciente indígena sin comunidad étnica")
    if _entero(discapacidad) not in range(1, 9):
        falta("Discapacidad", "Sin diligenciar")

    # Residencia
    if not _txt(cod_dep):
        falta("Departamento de residencia", "Sin diligenciar")
    if not _txt(cod_muni):
        falta("Municipio de residencia", "Sin diligenciar")
    if not _txt(pais_res):
        falta("País de residencia", "Sin diligenciar")
    if nacionalidad is None:
        falta("Nacionalidad", "Sin diligenciar")

    # Contacto (recomendado)
    if _txt(direccion).upper() in DIRECCIONES_FICTICIAS:
        falta("Dirección", "Vacía o genérica ('SIN DIRECCION')", obligatorio=False)
    if _txt(telefono) in TELEFONOS_FICTICIOS and not _txt(celular):
        falta("Teléfono", "Vacío o ficticio (3000000000)", obligatorio=False)

    return {"completo": not faltantes, "faltantes": faltantes}

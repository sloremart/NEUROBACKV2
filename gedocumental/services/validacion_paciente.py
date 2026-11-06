"""
Validación de datos obligatorios del paciente en ZeusSalud (sis_paci).

No usa IA: compara cada campo contra los catálogos que usa Zeus para RIPS
(spFEVRIPS) y el Resumen Digital de Atención (spRDA_AtencionPaciente):
  - IdentidadGenero  (sismaelm GENERO):  1 Masculino, 2 Femenino, 3 Transgénero, 4 Neutro, 5 No lo declara
  - OrientacionSexual(sismaelm ORINSEX): 1..5  (0 / NULL = sin diligenciar) — NO se valida: no es obligatoria
  - PertenenciaEtnica(sismaelm ETNIA):   1..7  (7 = Ninguna de las anteriores)
  - discapacidad     (sismaelm DISCAP):  1..8  (8 = Sin discapacidad)
  - ZonaTerritorial:                     '01' Urbana, '02' Rural (RDA)
  - zona:                                'U' / 'R' (la que usan los RIPS de spFEVRIPS); basta con una de las dos
  - id_cargo (CIUO):  ocupación; 9998 = hogar/estudiante/jubilado... es válido, 9999 = "no se tiene información"

Datos de la orden (VariablesDinamicasEntidades del formulario de admisión; Zeus guarda UN valor por
paciente en VariablesDinamicasEntidadesRegistros y lo sobrescribe en cada admisión):
  2 MEDICO QUE ORDENA, 3 IPS QUE ORDENA, 4 FECHA DE ORDENAMIENTO ('AAAA/MM/DD'),
  6 Nivel Educativo (VariablesDinamicasEntidadesOpciones: 1..13, 13 = Ninguno).

"No lo declara", "Ninguna" y "Sin discapacidad" son respuestas válidas: no se marcan.
"""

import logging
from datetime import datetime

from django.db import connections

logger = logging.getLogger(__name__)

VARIABLE_MEDICO_ORDENA = 2
VARIABLE_IPS_ORDENA = 3
VARIABLE_FECHA_ORDENAMIENTO = 4
VARIABLE_NIVEL_EDUCATIVO = 6

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
    sp.direccion, sp.telefono, sp.celular,
    sp.ZonaTerritorial, sp.zona, sp.id_cargo,
    sm.fecha_ing,
""" + ",\n".join(
    f"(SELECT TOP 1 v.Valor FROM VariablesDinamicasEntidadesRegistros v "
    f"WHERE v.IdPaciente = sm.autoid AND v.IdVariableDinamicaEntidad = {variable} ORDER BY v.Id DESC)"
    for variable in (VARIABLE_MEDICO_ORDENA, VARIABLE_IPS_ORDENA, VARIABLE_FECHA_ORDENAMIENTO,
                     VARIABLE_NIVEL_EDUCATIVO)) + """,
    sm.contrato,
    CASE WHEN ISNULL(LTRIM(RTRIM(sm.nro_autoriza)), '') NOT IN ('', '0')
              OR EXISTS (SELECT 1 FROM sis_deta d WHERE d.estudio = sm.con_estudio
                         AND ISNULL(LTRIM(RTRIM(d.autorizacion)), '') NOT IN ('', '0'))
         THEN 1 ELSE 0 END
"""

# Número de autorización: Zeus no marca qué contratos lo exigen (todos dicen "ControlNumero"), así que se
# decide con los datos: lo exigen los contratos en que casi todas las admisiones recientes lo tienen
# (Capital Salud, Sanitas Evento, Salud Total...). FOMAG, Sanitas MRC, particulares, convenios IPS o SOAT
# nunca lo llevan y no se marcan. Un contrato nuevo entra solo cuando sus admisiones empiezan a traerlo.
DIAS_CONTRATOS_AUTORIZACION = 90
PORCENTAJE_CONTRATO_CON_AUTORIZACION = 80
MINIMO_ADMISIONES_CONTRATO = 5
_contratos_autorizacion = {'hasta': 0.0, 'codigos': None}

SQL_CONTRATOS_CON_AUTORIZACION = """
    SELECT sm.contrato
    FROM sis_maes sm
    WHERE sm.fecha_ing >= DATEADD(day, -%s, GETDATE())
    GROUP BY sm.contrato
    HAVING COUNT(*) >= %s
       AND 100.0 * SUM(CASE WHEN ISNULL(LTRIM(RTRIM(sm.nro_autoriza)), '') NOT IN ('', '0') THEN 1 ELSE 0 END)
           / COUNT(*) >= %s
"""


SIN_AUTORIZACION = "No cuenta con número de autorización en SIESA"


def admisiones_sin_autorizacion(consecutivos) -> set:
    """Admisiones de contratos que exigen número de autorización y no lo tienen en SIESA."""
    ids = [int(c) for c in consecutivos]
    sin = set()
    with connections['zeussalud'].cursor() as cursor:
        requieren = contratos_con_autorizacion(cursor)
        for i in range(0, len(ids), 500):
            bloque = ids[i:i + 500]
            cursor.execute(
                f"""SELECT sm.con_estudio, sm.contrato FROM sis_maes sm
                    WHERE sm.con_estudio IN ({', '.join(['%s'] * len(bloque))})
                      AND ISNULL(LTRIM(RTRIM(sm.nro_autoriza)), '') IN ('', '0')
                      AND NOT EXISTS (SELECT 1 FROM sis_deta d WHERE d.estudio = sm.con_estudio
                                      AND ISNULL(LTRIM(RTRIM(d.autorizacion)), '') NOT IN ('', '0'))""",
                bloque)
            sin.update(int(e) for e, contrato in cursor.fetchall() if contrato in requieren)
    return sin


def contratos_con_autorizacion(cursor) -> set:
    """Códigos de contrato que exigen número de autorización (se recalcula cada hora)."""
    import time
    if _contratos_autorizacion['codigos'] is None or time.time() > _contratos_autorizacion['hasta']:
        cursor.execute(SQL_CONTRATOS_CON_AUTORIZACION, [DIAS_CONTRATOS_AUTORIZACION, MINIMO_ADMISIONES_CONTRATO,
                                                        PORCENTAJE_CONTRATO_CON_AUTORIZACION])
        _contratos_autorizacion.update(codigos={fila[0] for fila in cursor.fetchall()}, hasta=time.time() + 3600)
    return _contratos_autorizacion['codigos']


def _fecha(valor):
    """'2026/10/21' (formato del formulario de Zeus) o '2026-10-21' → date; None si no es una fecha."""
    for formato in ('%Y/%m/%d', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(_txt(valor)[:10], formato).date()
        except ValueError:
            pass
    return None


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
            requieren = contratos_con_autorizacion(cursor)
            for i in range(0, len(ids), 500):
                bloque = ids[i:i + 500]
                marcadores = ', '.join(['%s'] * len(bloque))
                cursor.execute(
                    f"SELECT {CAMPOS_SQL} FROM sis_maes sm JOIN sis_paci sp ON sp.autoid = sm.autoid "
                    f"WHERE sm.con_estudio IN ({marcadores})",
                    bloque,
                )
                for fila in cursor.fetchall():
                    resultados[int(fila[0])] = _evaluar(fila[1:], requieren)
    except Exception as exc:
        logger.warning("No se pudo validar datos de pacientes: %s", exc)
    return resultados


def _evaluar(fila, contratos_autorizacion=frozenset()) -> dict:
    (tipo_id, num_id, nombre, apellido, fecha_naci, sexo,
     identidad, orientacion, etnia, comunidad,
     discapacidad,
     cod_dep, cod_muni, pais_res, nacionalidad,
     direccion, telefono, celular,
     zona, zona_rips_raw, id_cargo,
     fecha_ing, medico_ordena, ips_ordena, fecha_ordenamiento, nivel_educativo,
     contrato, tiene_autorizacion) = fila

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
    # Zona: basta con cualquiera de los dos campos diligenciado (no se compara si coinciden)
    if _txt(zona) not in ('01', '02') and _txt(zona_rips_raw).upper() not in ('U', 'R'):
        falta("Zona territorial", "Sin diligenciar (Urbana / Rural)")
    if _entero(discapacidad) not in range(1, 9):
        falta("Discapacidad", "Sin diligenciar")
    if not _txt(id_cargo):
        falta("Ocupación", "Sin código de ocupación (CIUO)")
    elif _txt(id_cargo) == '9999':
        falta("Ocupación", "Registrada como 'no se tiene información'")
    if _entero(nivel_educativo) not in range(1, 14):
        falta("Nivel educativo", "Sin diligenciar")

    # Número de autorización (en la admisión o en sus servicios), en los contratos que lo exigen
    if contrato in contratos_autorizacion and not tiene_autorizacion:
        falta(SIN_AUTORIZACION, "Diligenciar el número de autorización de la EPS en la admisión")

    # Datos de la orden (formulario de la admisión)
    if not _txt(medico_ordena):
        falta("Médico que ordena", "Sin diligenciar")
    if not _txt(ips_ordena):
        falta("IPS que ordena", "Sin diligenciar")
    fecha_orden = _fecha(fecha_ordenamiento)
    if fecha_orden is None:
        falta("Fecha de ordenamiento", "Sin diligenciar" if not _txt(fecha_ordenamiento) else "Fecha no válida")
    elif fecha_ing and fecha_orden > fecha_ing.date():
        falta("Fecha de ordenamiento", f"Posterior al ingreso ({fecha_orden:%Y/%m/%d})")

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

# GedocumentalV2 — Contexto del proyecto

Sistema de gestión documental para la clínica de neurología. Comparte base de datos con el proyecto neuro-bot (chatbot de agendamiento). Ambos interactúan con ZeusSalud_Neuro via SQL directo.

---

## Base de datos: ZeusSalud_Neuro

- **Servidor local:** `LorenaM` (SQL Server, Windows Auth)
- **Base producción:** `ZeusSalud_Neuro`
- **Base de pruebas:** `ZeusSalud_Prueba` (192.168.1.207, Sa/111)
- **Conexión sqlcmd:** `sqlcmd -S LorenaM -d ZeusSalud_Neuro -E -No`

---

## TRES TIPOS DE CITA EN ZEUS

| Campo | CONSULTA | PROCEDIMIENTO | IMAGEN |
|-------|----------|--------------|--------|
| `asunto` | 1,7,8,9,10,11 | 13,14,15,16 | 2,3,4,5,6,12 |
| `id_sede` | **2** | **2** | **3** |
| `primera_vez_control` | 1=1ªvez / 2=ctrl | **siempre 2** | **siempre 2** |
| Tabla CUPS | `citas_procedimientos_asuntos` | `citas_procedimientos` | `citas_procedimientos` |
| Precio al agendar | **SÍ** (en cpa.Valor) | NO | NO |

---

## MAPA DE ASUNTOS (sis_asunto)

| id | nombre | id_sede | id_consultorio | servicio_codigo |
|----|--------|---------|---------------|-----------------|
| 1 | CONSULTA PRIMERA VEZ FISIATRIA | 2 | 18-21 | 12 |
| 2 | RX | **3** | 2 | 4 |
| 3 | TOMOGRAFÍA | **3** | 3 | 5 |
| 4 | RESONANCIA | **3** | 4 | 6 |
| 5 | MAMOGRAFIA | **3** | 5 | — |
| 6 | ECOGRAFIAS | 2 | 1,6,7,8 | 7 |
| 7 | CONSULTA DE CONTROL FISIATRIA | 2 | 18-21 | 12 |
| 8 | CONSULTA 1ª VEZ NEUROLOGIA | 2 | 9,11,12,13 | 11 |
| 9 | CONSULTA CONTROL NEUROLOGIA | 2 | 9,11,12,13 | 11 |
| 10 | CONSULTA 1ª VEZ NEUROPEDIATRIA | 2 | 29 | 13 |
| 11 | CONSULTA CONTROL NEUROPEDIATRIA | 2 | 29 | 13 |
| 12 | PET/CT | **3** | 28 | 10 |
| 13 | POLISOMNOGRAFIAS | 2 | 26 | 16 |
| 14 | ELECTROENCEFALOGRAMAS-VIDEOTELEMETRIAS | 2 | 27 | 15 |
| 15 | PROCEDIMIENTOS FISIATRIA | 2 | 22-25 | 20 |
| 16 | PROCEDIMIENTOS NEUROLOGIA | 2 | 10,14 | 18 |
| 17 | SOPORTE SEDACION | 2 | 30 | 21 |

---

## CUPS DE NEUROLOGÍA (AsuntoPctos)

| Asunto | CUPS | Descripción |
|--------|------|-------------|
| 8 | **890274** | CONSULTA 1ª VEZ ESPECIALISTA EN NEUROLOGÍA |
| 9 | **890374** | CONSULTA CONTROL ESPECIALISTA EN NEUROLOGÍA |
| 10 | **890275** | CONSULTA 1ª VEZ NEUROLOGÍA PEDIÁTRICA |
| 11 | **890375** | CONSULTA CONTROL NEUROLOGÍA PEDIÁTRICA |

> Fuente autoritativa: tabla `AsuntoPctos`. NO usar la tabla `CUPS` genérica para estos.

---

## TABLAS CLAVE DE AGENDAMIENTO

### programacion_medico
Bloque de agenda. `id` = IDENTITY. `id_programacion` se actualiza con `SCOPE_IDENTITY()` después del INSERT.
- `id_sede = 2` (SEDE 01) o `3` (SEDE 02 — imágenes)
- `activo = 1`

### programacion_medico_relacion
Vincula agenda a consultorio.
- `id_programacion = programacion_medico.id`
- `tipo_empresa = 'ENTIDADES'`

### programacion_medico_detalle
Slots individuales.
- `IdProgramacionMedico = programacion_medico.id` (el IDENTITY — crítico, NO el id de la relación)
- `Medico` = `cod_medi` del médico/técnico (siempre igual a `citas.cod_medi`)
- `IdCita = NULL` cuando disponible; se actualiza al agendar
- `Bloqueado = 0`, `SinProgramacion = 0`

### citas
Registro principal de la cita. Campos obligatorios:
```
autoid, cod_medi, fecha, hora, meridiano, estado ('P'=Pendiente),
asunto, empresa, contrato, fecha_solicitud, id_programacion, id_sede,
cod_user_asigna_cita, primera_vez_control, formaSolicitud (2=presencial),
tipoUsuario ('01'=contributivo, '02'=subsidiado), es_terapia (0),
Adicional (0), CodGrupo (0), EsCitaMultiple (0), lugarAtencion (0),
fecha_usuario_desea_cita
```

### citas_procedimientos_asuntos
Solo para **consultas médicas**. Almacena CUPS + tarifa al momento de agendar.
```
IdCita, IdSisDeta (0), Asunto, Servicio, TipoManual ('256'),
CodProcedimiento, NomProcedimiento, Valor, FechaRegistro
```

### citas_procedimientos
Solo para **procedimientos e imágenes**. Una fila por cada CUPS. No almacena precio.
```
id_procedimiento (CUPS), tipo ('256'), id_cita, Servicio, Cantidad (1)
```

---

## QUERY: SLOTS DISPONIBLES

```sql
SELECT
    pmd.IdProgramacionMedico,
    pm.id_programacion,
    pm.id_sede,
    pmr.id_consultorio,
    con.nombre                              AS consultorio,
    CAST(pmd.Fecha AS DATE)                 AS fecha,
    CONVERT(VARCHAR(5), pmd.Fecha, 108)     AS hora,
    CASE WHEN DATEPART(HOUR, pmd.Fecha) < 12
         THEN 'am' ELSE 'pm' END            AS meridiano
FROM programacion_medico_detalle pmd
JOIN programacion_medico pm
    ON pm.id = pmd.IdProgramacionMedico AND pm.activo = 1
JOIN programacion_medico_relacion pmr
    ON pmr.id_programacion = pm.id
JOIN consultorios con
    ON con.id = pmr.id_consultorio
WHERE pmd.Medico          = @cod_medi
  AND pmr.id_consultorio  = @id_consultorio
  AND pmd.IdCita          IS NULL
  AND pmd.Bloqueado       = 0
  AND pmd.SinProgramacion = 0
  AND CAST(pmd.Fecha AS DATE) >= CAST(GETDATE() AS DATE)
ORDER BY pmd.Fecha
```

---

## TARIFAS POR CONTRATO

### Consultas (via AsuntoPctos)
```sql
SELECT ap.CodProcedimiento, ap.NomProcedimiento, spp.Precio AS tarifa
FROM AsuntoPctos ap
JOIN contratos ct ON ct.codigo = @cod_contrato
JOIN sis_proc_precios spp
    ON spp.Cod_manual = ct.manual
    AND spp.Codigo_proc = ap.CodProcedimiento
    AND spp.Tipo_proc = '256'
WHERE ap.Asunto = @asunto
```

### Procedimientos/Imágenes (directo desde sis_proc_precios)
```sql
-- CUPS con sufijo (ej: 053105-10): prioriza exacto, fallback a código base
DECLARE @cups_base VARCHAR(20) = LEFT(@cups, CHARINDEX('-', @cups + '-') - 1);
SELECT TOP 1 spp.Precio
FROM contratos ct
JOIN sis_proc_precios spp
    ON spp.Cod_manual = ct.manual
    AND spp.Codigo_proc IN (@cups, @cups_base)
    AND spp.Tipo_proc = '256'
WHERE ct.codigo = @cod_contrato
ORDER BY CASE WHEN spp.Codigo_proc = @cups THEN 0 ELSE 1 END;
```

### Validar si un CUPS está cubierto por el contrato del paciente
```sql
SELECT s.cod_proc, spp.Precio
FROM contratos ct
JOIN servicios s ON s.contrato = ct.codigo AND s.cod_proc = @cups
JOIN sis_proc_precios spp
    ON spp.Cod_manual = ct.manual AND spp.Codigo_proc = s.cod_proc AND spp.Tipo_proc = '256'
WHERE ct.codigo = @cod_contrato
-- Sin filas = no cubierto
```

---

## CONTRATOS PRINCIPALES

| codigo | alias | manual | regimen |
|--------|-------|--------|---------|
| 4 | SANITAS EVENTO CONTRIBUTIVO | 11 | 1 |
| 5 | SANITAS MRC SUBSIDIADO | 8 | 2 |
| 7 | SANITAS EVENTO SUBSIDIADO | 11 | 2 |
| 8 | PARTICULAR TARIFA PLENA | 32 | 4 |
| 12 | SALUD TOTAL SUBSIDIADO | 15 | 2 |
| 13 | SALUD TOTAL CONTRIBUTIVO | 15 | 1 |
| 14 | CAPITAL SALUD CONTRIBUTIVO | 34 | 1 |
| 21 | FOMAG | 10 | 7 |
| 22 | COLSANITAS PLAN MODULAR | 21 | 5 |
| 24 | MEDISANITAS | 29 | 5 |
| 25 | SURA POLIZA GLOBAL Y CLASICO | 27 | 5 |
| 39 | ARL POSITIVA | 30 | 5 |

> 47 contratos en total. `tipoUsuario` en citas: regimen=1 → '01', regimen=2 → '02', otros → '01'

---

## CONSULTORIOS (tabla consultorios)

| id | nombre | tipo |
|----|--------|------|
| 1 | CONSULTORIO ECOGRAFIA 01 | imagen |
| 2 | CONSULTORIO RX SEDE 02 | imagen |
| 3 | CONSULTORIO TAC SEDE 02 | imagen |
| 4 | CONSULTORIO RESONANCIA SEDE 02 | imagen |
| 5 | CONSULTORIO MAMOGRAFIA SEDE 02 | imagen |
| 6-8 | CONSULTORIO ECOGRAFIA 02/03/04 | imagen |
| 9 | CONSULTORIO NEUROLOGIA 01 CONSULTA | consulta |
| 10 | CONSULTORIO PROCEDIMIENTO NEUROLOGIA 01 | procedimiento |
| 11 | CONSULTORIO NEUROLOGIA 02 CONSULTA | consulta |
| 12 | CONSULTORIO NEUROLOGIA 03 CONSULTA | consulta |
| 13 | CONSULTORIO NEUROLOGIA 04 CONSULTA | consulta |
| 14 | CONSULTORIO PROCEDIMIENTO NEUROLOGIA 02 | procedimiento |
| 18-21 | CONSULTORIO FISIATRIA 01-04 CONSULTA | consulta |
| 22-25 | CONSULTORIO PROCEDIMIENTO FISIATRIA 01-04 | procedimiento |
| 26 | POLISOMNOGRAFIAS | procedimiento |
| 27 | ELECTROENCEFALOGRAMAS -VIDEOTELEMETRIAS | procedimiento |
| 28 | PET/CT | imagen |
| 29 | CONSULTORIO NEUROLOGIA PEDIÁTRICA 01 | consulta |
| 30 | CONSULTORIO DE ANESTESIOLOGIA | especial |

---

## MÉDICOS Y TÉCNICOS

| cod_medi | Nombre | Consultorio principal | id_consultorio | id_sede |
|----------|--------|-----------------------|---------------|---------|
| 3 | WILLIAM GARCIA ROSSI | RESONANCIA SEDE 02 | 4 | 3 |
| 4 | SILVIO LOPERA FERNANDEZ | TAC SEDE 02 | 3 | 3 |
| 11 | FABIO PEREZ CABALLERO | RX SEDE 02 | 2 | 3 |
| 17 | SEBASTIAN POSADA BUSTOS | NEUROLOGÍA PEDIÁTRICA 01 | 29 | 2 |
| 19 | WILLINGTON CHONA SUAREZ | FISIATRÍA (rota) | 18/23/24 | 2 |
| 20 | ROBERTO ORTEGA VILLALBA | NEUROLOGÍA 01 CONSULTA | 9 | 2 |
| 22 | MARIO VELASCO MARQUEZ | NEUROLOGÍA 03 CONSULTA | 12 | 2 |
| 23 | LORENA PLAZAS RUIZ | NEUROLOGÍA 02 CONSULTA | 11 | 2 |

> Los médicos pueden rotar entre consultorios. No hay asignación fija — siempre consultar agenda activa.

---

## REGLAS CRÍTICAS

1. **SEDE 01** en Zeus UI = `id_sede = 2` en BD (NO es 1)
2. **SEDE 02** (imágenes) = `id_sede = 3`
3. `citas.cod_medi` = `programacion_medico_detalle.Medico` — deben coincidir siempre
4. `IdProgramacionMedico` en detalle = `programacion_medico.id` (IDENTITY) — NO el id de la relación
5. `log_citas` se llena automático por Zeus — NO insertar manualmente
6. CUPS con sufijo (ej: `891901-72`): buscar precio por código base en `sis_proc_precios`
7. `citas_procedimientos_asuntos` → solo consultas médicas (guarda precio)
8. `citas_procedimientos` → solo procedimientos/imágenes (no guarda precio, se liquida al atender)

---

## DATOS DE PRUEBA

- **Paciente prueba** (ZeusSalud_Prueba): `autoid=5`, empresa `EPS005`, contrato=4 (SANITAS)
- **Médico referencia**: `cod_medi=20` → ROBERTO MARIO ORTEGA VILLALBA
- Script de prueba: `NEUROBACK/prueba_cita_zeus.sql`

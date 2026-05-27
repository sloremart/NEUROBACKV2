-- =============================================================================
-- PRUEBA DE AGENDAMIENTO EN ZeusSalud_Prueba
-- Base de datos: ZeusSalud_Prueba
-- Médico:        cod=20  ROBERTO MARIO ORTEGA VILLALBA
-- Fecha prueba:  2026-05-15 (viernes)
-- =============================================================================

USE ZeusSalud_Prueba;
GO

-- =============================================================================
-- PASO 1: CREAR UNA AGENDA (programacion_medico) PARA FECHA FUTURA
-- Solo ejecutar UNA VEZ. Si ya la creaste, ir directo al PASO 2.
-- =============================================================================

DECLARE @new_id INT;

-- 1A. Insertar el bloque de agenda SIN especificar id (es IDENTITY)
--     id_programacion se pone en 0 de placeholder, luego se actualiza
INSERT INTO programacion_medico (
    id_programacion,
    fechainicio, fechafin,
    horainicio, meridianoi,
    horafinal,  meridianof,
    intervalo,
    id_sede,
    lun, mar, mie, jue, vie, sab, dom,
    activo,
    txtLun, txtMar, txtMie, txtJue, txtVie, txtSab, txtDom,
    id_medico,
    numMaxPacte
)
VALUES (
    0,                                 -- placeholder, se actualiza abajo
    '2026-05-15', '2026-05-15',        -- solo el viernes 15 de mayo
    '07:00', 'am',                     -- inicia 7:00 am
    '12:00', 'pm',                     -- termina 12:00 pm
    10,                                -- intervalo 10 minutos
    2,                                 -- sede 2
    0, 0, 0, 0, 1, 0, 0,              -- solo viernes = vie=1
    1,                                 -- activo
    '', '', '', '', '', '', '',
    20,                                -- id_medico = 20
    0
);

-- Capturar el id generado por IDENTITY
SET @new_id = SCOPE_IDENTITY();

-- Actualizar id_programacion para que sea igual al id generado
UPDATE programacion_medico
SET id_programacion = @new_id
WHERE id = @new_id;

-- 1B. Insertar la relación médico-sede-consultorio
--     Id también es IDENTITY, no se especifica
INSERT INTO programacion_medico_relacion (
    id_programacion,
    codMedico, id_sede, id_consultorio,
    tipo_empresa, id_asunto
)
VALUES (
    @new_id,
    20, 2, 9,                          -- médico 20, sede 2, consultorio 9
    'ENTIDADES', NULL
);

PRINT 'Agenda creada. id_programacion = ' + CAST(@new_id AS VARCHAR);
PRINT 'Médico: ROBERTO ORTEGA | Fecha: 2026-05-15 | 07:00am - 12:00pm | cada 10 min';
GO

-- =============================================================================
-- PASO 2: VER SLOTS DISPONIBLES PARA ESA AGENDA
-- *** Reemplaza el número en @id_programacion con el id que imprimió el PASO 1 ***
-- =============================================================================

DECLARE @id_programacion INT = (SELECT MAX(id) FROM programacion_medico WHERE fechainicio='2026-05-15' AND id_medico=20);
DECLARE @fecha           DATE = '2026-05-15';
DECLARE @hora_inicio     TIME = '07:00';
DECLARE @hora_fin        TIME = '12:00';
DECLARE @intervalo       INT  = 10;   -- minutos

-- Generar todos los slots con un CTE recursivo
;WITH Slots AS (
    -- Primer slot
    SELECT @hora_inicio AS slot_hora
    UNION ALL
    -- Siguientes slots sumando el intervalo
    SELECT DATEADD(MINUTE, @intervalo, slot_hora)
    FROM Slots
    WHERE DATEADD(MINUTE, @intervalo, slot_hora) < @hora_fin
)
SELECT
    -- Formato HH:MM
    LEFT(CONVERT(VARCHAR, slot_hora, 108), 5)  AS hora,
    CASE WHEN slot_hora < '12:00' THEN 'am' ELSE 'pm' END AS meridiano,
    -- Verificar si ya hay una cita en ese slot
    CASE
        WHEN EXISTS (
            SELECT 1 FROM citas
            WHERE id_programacion = @id_programacion
              AND fecha            = @fecha
              AND hora             = LEFT(CONVERT(VARCHAR, slot_hora, 108), 5)
              AND estado NOT IN ('C', 'CC', 'I')   -- excluir canceladas/incumplidas
        ) THEN 'OCUPADO'
        ELSE 'DISPONIBLE'
    END AS disponibilidad,
    -- Si está ocupado, mostrar el autoid del paciente
    (
        SELECT TOP 1 CAST(autoid AS VARCHAR)
        FROM citas
        WHERE id_programacion = @id_programacion
          AND fecha            = @fecha
          AND hora             = LEFT(CONVERT(VARCHAR, slot_hora, 108), 5)
          AND estado NOT IN ('C', 'CC', 'I')
    ) AS autoid_paciente
FROM Slots
OPTION (MAXRECURSION 200);

GO

-- =============================================================================
-- PASO 3: INSERTAR UNA CITA DE PRUEBA
-- Paciente: autoid=5 (SANDRA CAMPOS, CC 35263910)
-- Slot:     07:10 am del 2026-05-15
-- Contrato: 4 (SANITAS EVENTO CONTRIBUTIVO, empresa EPS005)
-- Asunto:   9 (CONSULTA DE CONTROL NEUROLOGIA)
-- =============================================================================

-- Verificar que el slot esté libre antes de insertar
DECLARE @id_prog_cita INT = (SELECT MAX(id) FROM programacion_medico WHERE fechainicio='2026-05-15' AND id_medico=20);

IF NOT EXISTS (
    SELECT 1 FROM citas
    WHERE id_programacion = @id_prog_cita
      AND fecha  = '2026-05-15'
      AND hora   = '07:10'
      AND estado NOT IN ('C', 'CC', 'I')
)
BEGIN
    INSERT INTO citas (
        autoid,                    -- paciente
        cod_medi,                  -- médico
        fecha,                     -- fecha cita
        hora,                      -- hora slot
        meridiano,                 -- am/pm
        estado,                    -- P = Pendiente
        asunto,                    -- tipo de consulta
        empresa,                   -- entidad
        contrato,                  -- código contrato
        fecha_solicitud,           -- cuando se agenda
        id_programacion,           -- bloque de agenda
        id_sede,                   -- sede
        cod_user_asigna_cita,      -- usuario que agenda (num_id)
        primera_vez_control,       -- 1=primera vez, 2=control
        formaSolicitud,            -- 2=Presencial
        tipoUsuario,               -- 01=contributivo
        es_terapia,
        Adicional,
        CodGrupo,
        EsCitaMultiple,
        lugarAtencion,
        fecha_usuario_desea_cita
    )
    VALUES (
        5,                         -- autoid SANDRA CAMPOS
        20,                        -- cod_medi ROBERTO ORTEGA
        '2026-05-15',              -- fecha
        '07:10',                   -- hora
        'am',
        'P',                       -- Pendiente
        9,                         -- CONSULTA CONTROL NEUROLOGIA
        'EPS005',                  -- empresa
        '4',                       -- contrato SANITAS EVENTO CONTRIBUTIVO
        GETDATE(),
        @id_prog_cita,             -- id_programacion creado en PASO 1
        2,                         -- sede
        '35263910',                -- num_id del usuario que agenda
        1,                         -- primera vez
        2,                         -- presencial
        '01',                      -- contributivo
        0,                         -- es_terapia
        0,                         -- Adicional
        0,                         -- CodGrupo
        0,                         -- EsCitaMultiple
        0,                         -- lugarAtencion
        CAST(GETDATE() AS DATE)    -- fecha_usuario_desea_cita
    );

    PRINT 'Cita insertada exitosamente. ID: ' + CAST(SCOPE_IDENTITY() AS VARCHAR);
END
ELSE
BEGIN
    PRINT 'El slot 07:10 ya está ocupado. Elige otro slot disponible del PASO 2.';
END

GO

-- =============================================================================
-- PASO 4: VERIFICAR QUE LA CITA QUEDÓ REGISTRADA
-- =============================================================================

SELECT
    c.id,
    c.fecha,
    c.hora + ' ' + c.meridiano  AS hora_cita,
    c.estado,
    sm.nombre                   AS medico,
    sp.primer_nom + ' ' + sp.primer_ape AS paciente,
    sp.num_id,
    sa.nombre                   AS tipo_consulta,
    ct.alias                    AS contrato,
    c.id_programacion
FROM citas c
LEFT JOIN sis_medi sm   ON sm.codigo  = c.cod_medi
LEFT JOIN sis_paci sp   ON sp.autoid  = c.autoid
LEFT JOIN sis_asunto sa ON sa.id      = c.asunto
LEFT JOIN contratos ct  ON ct.codigo  = CAST(c.contrato AS INT)
WHERE c.fecha = '2026-05-15' AND c.cod_medi = 20
ORDER BY c.hora;
GO

# Endpoints de Evaluación de Desempeño

## Resumen de Funcionalidades

Esta aplicación maneja dos tipos principales de evaluaciones:

1. **Actividades Laborales**: Evaluadas por líderes de área
2. **Evaluaciones 360**: Evaluadas por líderes y compañeros de trabajo

## Endpoints para Líderes

### Dashboard de Líder
- `GET /dashboard-lider/resumen/{lider_id}/` - Resumen general de evaluaciones pendientes
- `GET /dashboard-lider/usuarios_a_cargo/{lider_id}/` - Lista de usuarios a cargo

### Evaluación de Actividades Laborales
- `GET /evaluaciones-actividades/para_lider/{lider_id}/` - Actividades que puede evaluar
- `POST /evaluaciones-actividades/evaluar_actividad/` - Evaluar una actividad laboral

**Ejemplo de evaluación de actividad:**
```json
{
    "actividad_id": 1,
    "evaluador_id": 5,
    "calificacion": 8.5,
    "comentarios": "Excelente cumplimiento de la actividad"
}
```

### Evaluación 360 (para líderes)
- `GET /evaluaciones-360/para_lider/{lider_id}/` - Evaluaciones 360 que puede evaluar
- `POST /evaluaciones-360/evaluar_360/` - Completar evaluación 360

**Ejemplo de evaluación 360:**
```json
{
    "asignacion_id": 10,
    "evaluador_id": 5,
    "respuestas": [
        {
            "pregunta_id": 1,
            "respuesta_numerica": 4,
            "comentarios": "Buen desempeño en esta área"
        },
        {
            "pregunta_id": 2,
            "escala_seleccionada_id": 8,
            "comentarios": "Excelente trabajo en equipo"
        }
    ]
}
```

## Endpoints para Compañeros

### Evaluación 360 (para compañeros)
- `GET /evaluaciones-360/para_companero/{usuario_id}/` - Evaluaciones 360 que puede evaluar
- `POST /evaluaciones-360/evaluar_360/` - Completar evaluación 360 (mismo endpoint)

## Endpoints para Ver Preguntas y Progreso

### Ver Preguntas de Evaluación 360
- `GET /evaluaciones-360/preguntas/{asignacion_id}/` - Obtiene todas las preguntas de una evaluación 360 específica

**Respuesta incluye:**
- Preguntas organizadas por categorías
- Escalas de respuesta para preguntas Likert
- Opciones para preguntas múltiples
- Estado de respuesta (ya respondida o no)
- Respuestas anteriores si existen

### Dashboard Personal de Usuario
- `GET /dashboard-usuario/mi_dashboard/{usuario_id}/` - Dashboard personal con evaluaciones pendientes y recibidas
- `GET /dashboard-usuario/progreso_evaluacion/{asignacion_id}/` - Progreso detallado de una evaluación específica

### Evaluaciones Pendientes por Usuario
- `GET /evaluaciones-360/pendientes_usuario/{usuario_id}/` - Lista de evaluaciones pendientes del usuario

## Endpoints Generales

### Gestión de Evaluaciones
- `GET /evaluaciones-actividades/` - Lista de evaluaciones de actividades
- `GET /evaluaciones-360/` - Lista de evaluaciones 360
- `GET /evaluaciones/` - Lista de todas las evaluaciones

### Filtros Disponibles
- `?area_id=X` - Filtrar por área específica
- `?evaluador_id=X` - Filtrar por evaluador
- `?actividad_id=X` - Filtrar por actividad
- `?componente_id=X` - Filtrar por componente

## Estructura de Respuestas

### Actividades para Evaluar (Líder)
```json
{
    "id": 1,
    "nombre": "Revisar documentación",
    "descripcion": "Revisar y validar documentación del proyecto",
    "porcentaje": 25.0,
    "usuario_asignado": {
        "id": 3,
        "nombre": "Juan Pérez"
    },
    "usuarios_grupo": [],
    "ya_evaluada": false,
    "calificacion_anterior": null,
    "comentarios_anterior": null
}
```

### Evaluaciones 360 para Líder
```json
{
    "id": 5,
    "usuario_evaluado": {
        "id": 3,
        "nombre": "Juan Pérez"
    },
    "componente": {
        "id": 2,
        "nombre": "Evaluación 360 - Área Desarrollo"
    },
    "fecha": "2024-01-15",
    "ya_evaluada": false,
    "asignacion_id": 10
}
```

### Preguntas de Evaluación 360 (Organizadas por Categorías)
```json
{
    "evaluacion_id": 5,
    "usuario_evaluado": {
        "id": 3,
        "nombre": "Juan Pérez"
    },
    "componente": {
        "id": 2,
        "nombre": "Evaluación 360 - Área Desarrollo"
    },
    "fecha": "2024-01-15",
    "categorias": [
        {
            "id": 1,
            "nombre": "Trabajo en Equipo",
            "descripcion": "Habilidades de colaboración y trabajo grupal",
            "orden": 1,
            "preguntas": [
                {
                    "id": 1,
                    "texto": "¿Cómo evalúa la capacidad de trabajo en equipo?",
                    "tipo": "LIKERT",
                    "orden": 1,
                    "obligatoria": true,
                    "peso": 1.0,
                    "escalas": [
                        {
                            "id": 1,
                            "valor": 1,
                            "descripcion": "Totalmente en desacuerdo",
                            "orden": 0
                        },
                        {
                            "id": 2,
                            "valor": 2,
                            "descripcion": "En desacuerdo",
                            "orden": 1
                        }
                    ],
                    "ya_respondida": false,
                    "respuesta_anterior": null
                }
            ]
        }
    ],
    "total_preguntas": 15,
    "preguntas_respondidas": 0
}
```

### Dashboard Personal de Usuario
```json
{
    "usuario": {
        "id": 3,
        "nombre": "Juan Pérez",
        "username": "juan.perez",
        "email": "juan@empresa.com",
        "area": "Desarrollo",
        "rol": "Desarrollador",
        "cargo": "Desarrollador Senior",
        "es_lider": false
    },
    "como_evaluador": [
        {
            "asignacion_id": 10,
            "evaluacion_id": 5,
            "tipo": "360",
            "usuario_evaluado": {
                "id": 4,
                "nombre": "María García"
            },
            "componente": {
                "id": 2,
                "nombre": "Evaluación 360 - Área Desarrollo"
            },
            "fecha": "2024-01-15",
            "progreso": {
                "total_preguntas": 15,
                "preguntas_respondidas": 8,
                "porcentaje": 53.33
            },
            "estado": "en_proceso"
        }
    ],
    "como_evaluado": [
        {
            "evaluacion_id": 6,
            "tipo": "360",
            "componente": {
                "id": 2,
                "nombre": "Evaluación 360 - Área Desarrollo"
            },
            "fecha": "2024-01-15",
            "progreso": {
                "total_evaluadores": 5,
                "evaluadores_completados": 3,
                "porcentaje": 60.0
            },
            "estado": "en_proceso"
        }
    ],
    "resumen": {
        "total_evaluaciones_pendientes": 1,
        "total_evaluaciones_recibidas": 1,
        "total_general": 2
    }
}
```

### Resumen de Dashboard
```json
{
    "area": "Desarrollo",
    "actividades_laborales_pendientes": 5,
    "evaluaciones_360_pendientes": 3,
    "usuarios_a_cargo": 8,
    "total_pendientes": 8
}
```

## Flujo de Trabajo

### Para Líderes:
1. Consultar dashboard para ver resumen
2. Ver usuarios a cargo
3. Evaluar actividades laborales
4. Completar evaluaciones 360

### Para Compañeros:
1. Ver evaluaciones 360 asignadas
2. Completar evaluaciones 360

### Para Todos los Usuarios:
1. Ver dashboard personal con evaluaciones pendientes
2. Ver preguntas específicas de cada evaluación
3. Ver progreso de evaluaciones en curso
4. Completar evaluaciones asignadas

## Validaciones

- Solo líderes pueden evaluar actividades laborales
- Solo líderes pueden evaluar actividades de su área
- Las evaluaciones 360 solo pueden ser completadas por usuarios asignados
- Las calificaciones de actividades van de 0.0 a 10.0
- No se pueden duplicar evaluaciones de la misma actividad por el mismo evaluador
- Las preguntas se muestran organizadas por categorías y orden
- Se incluye información de respuestas anteriores para continuar evaluaciones

## Notas Importantes

- Todas las evaluaciones incluyen timestamps automáticos
- Las evaluaciones de actividades laborales son únicas por actividad-evaluador
- Las evaluaciones 360 se marcan como completadas al enviar todas las respuestas
- El sistema valida automáticamente los permisos y restricciones
- Las preguntas se organizan por categorías para mejor experiencia de usuario
- Se incluye progreso visual para evaluaciones en curso
- El dashboard personal muestra tanto evaluaciones pendientes como recibidas

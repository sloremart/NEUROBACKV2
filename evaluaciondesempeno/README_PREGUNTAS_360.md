# Sistema de Preguntas de Evaluación 360 - Documentación

## Descripción General

El sistema mejorado de preguntas de evaluación 360 permite crear, gestionar y responder evaluaciones de desempeño con diferentes tipos de preguntas, categorías, escalas y plantillas.

## Modelos Principales

### 1. CategoriaPregunta
Organiza las preguntas en categorías para mejor estructuración.

### 2. PreguntaComponente360
Preguntas principales con diferentes tipos:
- **LIKERT**: Escala de valoración (1-5 o 1-6)
- **ABIERTA**: Respuesta de texto libre
- **MULTIPLE**: Opciones de selección
- **BOOLEANA**: Sí/No
- **NUMERICA**: Valor numérico

### 3. EscalaRespuesta
Define las escalas para preguntas tipo Likert.

### 4. OpcionRespuesta
Define las opciones para preguntas tipo múltiple.

### 5. RespuestaEvaluacion
Almacena las respuestas de los evaluadores.

### 6. PlantillaEvaluacion
Permite crear plantillas reutilizables de preguntas.

## Endpoints Disponibles

### Categorías de Preguntas
```
GET    /categorias-preguntas/                    # Listar categorías
POST   /categorias-preguntas/                    # Crear categoría
GET    /categorias-preguntas/{id}/               # Obtener categoría
PUT    /categorias-preguntas/{id}/               # Actualizar categoría
DELETE /categorias-preguntas/{id}/               # Eliminar categoría
GET    /categorias-preguntas/?componente_id=1    # Filtrar por componente
```

### Preguntas 360
```
GET    /preguntas360/                            # Listar preguntas
POST   /preguntas360/                            # Crear pregunta
GET    /preguntas360/{id}/                       # Obtener pregunta
PUT    /preguntas360/{id}/                       # Actualizar pregunta
DELETE /preguntas360/{id}/                       # Eliminar pregunta
GET    /preguntas360/por_componente/{id}/        # Preguntas por componente
POST   /preguntas360/crear_con_escalas/          # Crear con escalas/opciones
POST   /preguntas360/{id}/crear_escalas_likert/  # Crear escalas Likert
GET    /preguntas360/organizadas_por_categoria/{id}/ # Organizadas por categoría
```

### Escalas de Respuesta
```
GET    /escalas-respuesta/                       # Listar escalas
POST   /escalas-respuesta/                       # Crear escala
GET    /escalas-respuesta/?pregunta_id=1         # Filtrar por pregunta
```

### Opciones de Respuesta
```
GET    /opciones-respuesta/                      # Listar opciones
POST   /opciones-respuesta/                      # Crear opción
GET    /opciones-respuesta/?pregunta_id=1        # Filtrar por pregunta
```

### Respuestas de Evaluación
```
GET    /respuestas-evaluacion/                   # Listar respuestas
POST   /respuestas-evaluacion/                   # Crear respuesta
POST   /respuestas-evaluacion/responder_evaluacion/ # Responder evaluación completa
GET    /respuestas-evaluacion/validar_completitud/{id}/ # Validar completitud
GET    /respuestas-evaluacion/promedio/{id}/     # Calcular promedio
```

### Plantillas de Evaluación
```
GET    /plantillas-evaluacion/                   # Listar plantillas
POST   /plantillas-evaluacion/                   # Crear plantilla
POST   /plantillas-evaluacion/crear_desde_preguntas/ # Crear desde preguntas
POST   /plantillas-evaluacion/{id}/agregar_pregunta/ # Agregar pregunta
```

## Ejemplos de Uso

### 1. Crear una Categoría
```json
POST /categorias-preguntas/
{
    "nombre": "Liderazgo",
    "descripcion": "Preguntas relacionadas con habilidades de liderazgo",
    "componente": 1,
    "orden": 1,
    "activo": true
}
```

### 2. Crear una Pregunta Likert con Escalas
```json
POST /preguntas360/crear_con_escalas/
{
    "componente": 1,
    "categoria": 1,
    "texto": "¿El empleado demuestra habilidades de liderazgo efectivas?",
    "tipo": "LIKERT",
    "orden": 1,
    "obligatoria": true,
    "peso": 1.5,
    "escalas": [
        {"valor": 1, "descripcion": "Totalmente en desacuerdo", "orden": 0},
        {"valor": 2, "descripcion": "En desacuerdo", "orden": 1},
        {"valor": 3, "descripcion": "Neutral", "orden": 2},
        {"valor": 4, "descripcion": "De acuerdo", "orden": 3},
        {"valor": 5, "descripcion": "Totalmente de acuerdo", "orden": 4}
    ]
}
```

### 3. Crear una Pregunta Múltiple
```json
POST /preguntas360/crear_con_escalas/
{
    "componente": 1,
    "categoria": 1,
    "texto": "¿Qué área necesita más desarrollo?",
    "tipo": "MULTIPLE",
    "orden": 2,
    "obligatoria": false,
    "peso": 1.0,
    "opciones": [
        {"texto": "Comunicación", "valor": "comunicacion", "orden": 0},
        {"texto": "Trabajo en equipo", "valor": "equipo", "orden": 1},
        {"texto": "Gestión de tiempo", "valor": "tiempo", "orden": 2},
        {"texto": "Resolución de problemas", "valor": "problemas", "orden": 3}
    ]
}
```

### 4. Crear una Pregunta Abierta
```json
POST /preguntas360/
{
    "componente": 1,
    "categoria": 1,
    "texto": "¿Qué fortalezas principales observa en el empleado?",
    "tipo": "ABIERTA",
    "orden": 3,
    "obligatoria": true,
    "peso": 1.0
}
```

### 5. Responder una Evaluación
```json
POST /respuestas-evaluacion/responder_evaluacion/
{
    "asignacion_id": 1,
    "respuestas": [
        {
            "pregunta": 1,
            "escala_seleccionada": 4
        },
        {
            "pregunta": 2,
            "opcion_seleccionada": 2
        },
        {
            "pregunta": 3,
            "respuesta_texto": "Excelente trabajo en equipo y comunicación clara"
        },
        {
            "pregunta": 4,
            "respuesta_booleana": true
        },
        {
            "pregunta": 5,
            "respuesta_numerica": 8
        }
    ]
}
```

### 6. Crear una Plantilla
```json
POST /plantillas-evaluacion/crear_desde_preguntas/
{
    "componente_id": 1,
    "nombre": "Evaluación de Liderazgo 2024",
    "descripcion": "Plantilla estándar para evaluar habilidades de liderazgo",
    "pregunta_ids": [1, 2, 3, 4, 5]
}
```

### 7. Obtener Reporte de Evaluación
```
GET /evaluaciones/{id}/reporte/
```

### 8. Obtener Estadísticas
```
GET /evaluaciones/{id}/estadisticas/
```

## Flujo de Trabajo Recomendado

1. **Crear Componentes y Categorías**
   - Definir las áreas de evaluación
   - Organizar en categorías lógicas

2. **Crear Preguntas**
   - Usar tipos apropiados según la necesidad
   - Asignar pesos según importancia
   - Crear escalas/opciones para preguntas estructuradas

3. **Crear Plantillas** (Opcional)
   - Agrupar preguntas en plantillas reutilizables
   - Facilitar la asignación masiva

4. **Asignar Evaluaciones**
   - Usar el endpoint de asignación masiva
   - Configurar evaluadores según tipo (360, 180, 90)

5. **Recopilar Respuestas**
   - Los evaluadores responden las preguntas
   - Sistema valida completitud

6. **Generar Reportes**
   - Obtener estadísticas y análisis
   - Generar recomendaciones

## Validaciones Importantes

- Las preguntas tipo Likert deben tener escalas
- Las preguntas tipo múltiple deben tener opciones
- Las preguntas obligatorias deben ser respondidas
- Los pesos deben ser valores positivos
- Las escalas deben tener valores únicos por pregunta

## Características Avanzadas

- **Cálculo de Promedios Ponderados**: Considera el peso de cada pregunta
- **Validación de Completitud**: Verifica respuestas obligatorias
- **Reportes Detallados**: Incluye estadísticas por categoría y evaluador
- **Plantillas Reutilizables**: Facilita la creación de evaluaciones estándar
- **Organización por Categorías**: Mejora la estructura y análisis
- **Diferentes Tipos de Preguntas**: Flexibilidad en el diseño de evaluaciones 
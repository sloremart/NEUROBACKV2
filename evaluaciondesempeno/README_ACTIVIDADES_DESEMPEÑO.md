# 🚀 Sistema de Actividades de Desempeño - Nueva Estructura

## 📋 Resumen

Este documento describe la **nueva estructura implementada** para separar completamente el sistema de **evaluación 360°** del sistema de **actividades de desempeño** (funciones diarias/contrato).

## 🏗️ Nueva Arquitectura

### **Tablas Principales:**

#### 1. **`LiderActividad`** - Líderes para Actividades
- **Propósito**: Configurar líderes específicos para actividades de desempeño
- **Separación**: Completamente independiente de `PerfilUsuario` (360°)
- **Flexibilidad**: Permite cambios de liderazgo sin afectar evaluaciones 360°

```python
class LiderActividad(models.Model):
    area = models.ForeignKey(Area, ...)
    lider = models.ForeignKey(User, ...)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)  # NULL = vigente
    tipo_actividad = models.CharField(choices=[
        ('FUNCIONES_CONTRATO', 'Funciones de Contrato'),
        ('ACTIVIDADES_DIARIAS', 'Actividades Diarias'),
        ('PROYECTOS_ESPECIALES', 'Proyectos Especiales'),
    ])
```

#### 2. **`ContratoUsuario`** - Información de Contratos
- **Propósito**: Gestionar información laboral de usuarios
- **Campos**: Tipo de contrato, fechas, cargo, salario, área
- **Validaciones**: Fechas coherentes según tipo de contrato

```python
class ContratoUsuario(models.Model):
    usuario = models.ForeignKey(User, ...)
    tipo_contrato = models.CharField(choices=[
        ('TERMINO_FIJO', 'Término Fijo'),
        ('INDEFINIDO', 'Indefinido'),
        ('PRESTACION_SERVICIOS', 'Prestación de Servicios'),
        ('APRENDIZAJE', 'Contrato de Aprendizaje'),
    ])
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    cargo = models.CharField(max_length=150)
    area = models.ForeignKey(Area, ...)
```

### **Tablas Modificadas:**

#### 3. **`AsignacionActividad`** - Asignaciones con Contrato
- **Nuevo campo**: `contrato` (ForeignKey a `ContratoUsuario`)
- **Validación**: El evaluador debe ser líder vigente del área del contrato
- **Relación**: Vincula actividad, usuario, evaluador y contrato

#### 4. **`Actividad`** - Métodos de Asignación Automática
- **`asignar_lider_automatico()`**: Obtiene líder vigente del área
- **`asignar_usuarios_automaticamente()`**: Crea asignaciones automáticamente

## 🔌 Endpoints Disponibles

### **Líderes de Actividades:**
```
GET    /lideres-actividades/                    # Listar todos los líderes
POST   /lideres-actividades/                    # Crear nuevo líder
GET    /lideres-actividades/{id}/               # Obtener líder específico
PUT    /lideres-actividades/{id}/               # Actualizar líder
DELETE /lideres-actividades/{id}/               # Eliminar líder
GET    /lideres-actividades/lideres_vigentes/   # Líderes vigentes por área
POST   /lideres-actividades/asignar_lider_automatico/  # Asignación automática
```

### **Contratos de Usuarios:**
```
GET    /contratos-usuarios/                     # Listar todos los contratos
POST   /contratos-usuarios/                     # Crear nuevo contrato
GET    /contratos-usuarios/{id}/                # Obtener contrato específico
PUT    /contratos-usuarios/{id}/                # Actualizar contrato
DELETE /contratos-usuarios/{id}/                # Eliminar contrato
GET    /contratos-usuarios/contratos_vigentes/  # Contratos vigentes
POST   /contratos-usuarios/crear_contrato_masivo/  # Creación masiva
```

## 🎯 Casos de Uso

### **1. Configurar Líder de Área para Actividades:**
```json
POST /lideres-actividades/
{
    "area": 3,
    "lider": 5,
    "fecha_inicio": "2025-01-01",
    "tipo_actividad": "FUNCIONES_CONTRATO"
}
```

### **2. Crear Contrato de Usuario:**
```json
POST /contratos-usuarios/
{
    "usuario": 232,
    "area": 3,
    "tipo_contrato": "TERMINO_FIJO",
    "fecha_inicio": "2025-01-01",
    "fecha_fin": "2025-12-31",
    "cargo": "AUXILIAR DE CITAS Y ADMISIONES - CALL CENTER"
}
```

### **3. Asignación Automática de Líder:**
```json
POST /lideres-actividades/asignar_lider_automatico/
{
    "area_id": 3,
    "tipo_actividad": "FUNCIONES_CONTRATO"
}
```

### **4. Creación Masiva de Contratos:**
```json
POST /contratos-usuarios/crear_contrato_masivo/
{
    "area_id": 3,
    "usuarios": [
        {
            "usuario_id": 232,
            "tipo_contrato": "TERMINO_FIJO",
            "fecha_inicio": "2025-01-01",
            "fecha_fin": "2025-12-31",
            "cargo": "AUXILIAR DE CITAS"
        },
        {
            "usuario_id": 233,
            "tipo_contrato": "INDEFINIDO",
            "fecha_inicio": "2025-01-01",
            "cargo": "AUXILIAR DE ADMISIONES"
        }
    ]
}
```

## 🔄 Flujo de Trabajo

### **1. Configuración Inicial:**
1. **Crear líderes** para cada área en `LiderActividad`
2. **Crear contratos** para usuarios en `ContratoUsuario`

### **2. Creación de Actividades:**
1. **Crear actividad** con área asignada
2. **Asignar usuarios** (individual o por área)
3. **Sistema automático** asigna líder vigente como evaluador

### **3. Evaluación de Actividades:**
1. **Líder evalúa** actividades de sus subordinados
2. **Sistema valida** que el evaluador sea líder vigente
3. **Se crea** `EvaluacionActividad` con calificación

## ✅ Ventajas de la Nueva Estructura

### **🔒 Separación Clara:**
- **360°**: Usa `PerfilUsuario` (liderazgo fijo)
- **Actividades**: Usa `LiderActividad` (liderazgo dinámico)

### **🔄 Flexibilidad:**
- **Cambios de liderazgo** sin afectar evaluaciones 360°
- **Contratos temporales** con fechas de inicio/fin
- **Múltiples tipos** de actividades por área

### **🤖 Automatización:**
- **Asignación automática** de líderes
- **Validaciones automáticas** de contratos vigentes
- **Creación masiva** de contratos

### **📊 Trazabilidad:**
- **Historial completo** de liderazgos
- **Contratos vigentes** con fechas
- **Auditoría** de cambios

## 🚨 Consideraciones Importantes

### **1. Migración de Datos:**
- **No se eliminan** tablas existentes
- **Nuevas funcionalidades** se agregan paralelamente
- **Compatibilidad** con sistema actual

### **2. Validaciones:**
- **Líderes vigentes** deben existir antes de crear actividades
- **Contratos vigentes** son obligatorios para asignaciones
- **Fechas coherentes** según tipo de contrato

### **3. Permisos:**
- **Solo usuarios autenticados** pueden acceder
- **Validaciones** a nivel de modelo y serializer
- **Auditoría** de cambios en todas las operaciones

## 🔮 Próximos Pasos

### **1. Frontend:**
- **Formularios** para gestión de líderes
- **Formularios** para gestión de contratos
- **Integración** con sistema de actividades existente

### **2. Reportes:**
- **Dashboard** de líderes por área
- **Reportes** de contratos vigentes
- **Métricas** de asignación automática

### **3. Notificaciones:**
- **Alertas** de contratos próximos a vencer
- **Notificaciones** de cambios de liderazgo
- **Recordatorios** de evaluaciones pendientes

---

## 📞 Soporte

Para dudas o problemas con la implementación, revisar:
1. **Logs del sistema** para errores específicos
2. **Validaciones** de modelos y serializers
3. **Permisos** de usuario y autenticación
4. **Integridad** de datos en base de datos

¡La nueva estructura está lista para usar! 🎉

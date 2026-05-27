# 🚀 Configuración de Actividades de Desempeño

## 📋 **Resumen del Problema**

Según las imágenes de las tablas que me mostraste, tienes:

- ✅ **Actividades creadas** en `evaluaciondesempeno_actividad` (IDs 11, 12, 13, 15)
- ✅ **Asignaciones existentes** en `evaluaciondesempeno_asignacionactividad`
- ✅ **Usuarios asignados** en `evaluaciondesempeno_actividad_usuarios_grupo`
- ❌ **Tabla `LiderActividad` VACÍA** - Necesita ser configurada

## 🎯 **Objetivo**

Configurar el sistema para que:
1. **Las actividades estén ligadas a líderes** para evaluación
2. **Los subordinados tengan contratos** asociados
3. **El sistema funcione automáticamente** para asignaciones y evaluaciones

## 🔧 **Solución Implementada**

He creado **2 comandos de Django** para configurar todo automáticamente:

### **1. Comando de Configuración Automática**
```bash
python manage.py configurar_actividades
```

**Parámetros disponibles:**
- `--area-id`: ID del área específica (opcional)
- `--lider-id`: ID del usuario líder (opcional, por defecto usa ID 5)
- `--fecha-inicio`: Fecha de inicio (por defecto: 2025-01-01)
- `--fecha-fin`: Fecha de fin (opcional)
- `--tipo-contrato`: Tipo de contrato (por defecto: TERMINO_FIJO)

**Ejemplos de uso:**
```bash
# Configuración básica (usa valores por defecto)
python manage.py configurar_actividades

# Configuración específica para área 3
python manage.py configurar_actividades --area-id 3

# Configuración con líder específico
python manage.py configurar_actividades --lider-id 217 --area-id 3

# Configuración con fechas específicas
python manage.py configurar_actividades --fecha-inicio 2025-08-01 --fecha-fin 2025-12-31
```

### **2. Comando de Verificación**
```bash
python manage.py verificar_estado_actividades
```

**Parámetros disponibles:**
- `--area-id`: Verificar solo un área específica
- `--detallado`: Mostrar información detallada de cada elemento

**Ejemplos de uso:**
```bash
# Verificación general
python manage.py verificar_estado_actividades

# Verificación específica del área 3
python manage.py verificar_estado_actividades --area-id 3

# Verificación detallada
python manage.py verificar_estado_actividades --detallado
```

## 📊 **Qué Hace el Comando de Configuración**

### **Paso 1: Crear Líder de Actividades**
- Crea un registro en `LiderActividad` para el área especificada
- Asigna el usuario líder (por defecto ID 5)
- Configura fechas de vigencia
- Tipo: `FUNCIONES_CONTRATO`

### **Paso 2: Crear Contratos para Usuarios**
- Identifica todos los usuarios asignados a actividades en el área
- Crea contratos en `ContratoUsuario` para cada usuario
- Asigna fechas, tipo de contrato y cargo
- Marca como activo

### **Paso 3: Crear Asignaciones de Actividades**
- Verifica las actividades existentes en el área
- Crea `AsignacionActividad` para cada usuario-actividad
- Asigna automáticamente el líder como evaluador
- Establece fechas límite (30 días por defecto)

## 🎯 **Configuración Recomendada para tu Caso**

Basándome en las imágenes, te recomiendo ejecutar:

```bash
# 1. Primero verificar el estado actual
python manage.py verificar_estado_actividades --area-id 3 --detallado

# 2. Configurar todo automáticamente
python manage.py configurar_actividades --area-id 3 --lider-id 5

# 3. Verificar que todo esté configurado correctamente
python manage.py verificar_estado_actividades --area-id 3
```

## 🔍 **Verificación Manual de las Tablas**

### **Tabla `LiderActividad`**
Después de la configuración debería tener:
```sql
SELECT * FROM evaluaciondesempeno_lideractividad;
```

**Resultado esperado:**
- `area_id`: 3
- `lider_id`: 5
- `tipo_actividad`: 'FUNCIONES_CONTRATO'
- `activo`: true
- `fecha_inicio`: '2025-01-01'

### **Tabla `ContratoUsuario`**
```sql
SELECT * FROM evaluaciondesempeno_contratousuario WHERE area_id = 3;
```

**Resultado esperado:**
- Contratos para usuarios: 230, 231, 232, 233
- `tipo_contrato`: 'TERMINO_FIJO'
- `activo`: true

### **Tabla `AsignacionActividad`**
```sql
SELECT * FROM evaluaciondesempeno_asignacionactividad 
WHERE actividad_id IN (11, 12, 13, 15);
```

**Resultado esperado:**
- Asignaciones con `evaluador_id`: 5 (líder)
- `usuario_asignado_id`: 230, 231, 232, 233
- `contrato_id`: IDs de contratos creados

## 🚨 **Posibles Errores y Soluciones**

### **Error: "No hay áreas disponibles"**
**Solución:** Crear al menos un área en la tabla `Area`

### **Error: "Usuario no encontrado"**
**Solución:** Verificar que los usuarios 230, 231, 232, 233 existan en `auth_user`

### **Error: "Líder no encontrado"**
**Solución:** Verificar que el usuario líder (ID 5) exista y tenga permisos

### **Error: "Actividad sin área asignada"**
**Solución:** Asignar área a las actividades que no la tengan

## 📱 **Endpoints de la API Disponibles**

Una vez configurado, puedes usar estos endpoints:

### **Líderes de Actividades:**
```
GET    /api/lideres-actividades/                    # Listar líderes
POST   /api/lideres-actividades/                    # Crear líder
GET    /api/lideres-actividades/lideres_vigentes/   # Líderes vigentes
```

### **Contratos de Usuarios:**
```
GET    /api/contratos-usuarios/                     # Listar contratos
POST   /api/contratos-usuarios/                     # Crear contrato
GET    /api/contratos-usuarios/contratos_vigentes/  # Contratos vigentes
```

### **Asignaciones de Actividades:**
```
GET    /api/asignaciones-actividades/               # Listar asignaciones
POST   /api/asignaciones-actividades/               # Crear asignación
```

## 🔄 **Flujo de Trabajo Después de la Configuración**

1. **Líder evalúa actividades** de sus subordinados
2. **Sistema valida** que el evaluador sea líder vigente
3. **Se crea** `EvaluacionActividad` con calificación
4. **Actividad se marca** como completada automáticamente

## ✅ **Verificación Final**

Para confirmar que todo funciona:

1. **Ejecutar verificación:**
   ```bash
   python manage.py verificar_estado_actividades --detallado
   ```

2. **Verificar en base de datos:**
   ```sql
   -- Líderes configurados
   SELECT COUNT(*) FROM evaluaciondesempeno_lideractividad;
   
   -- Contratos creados
   SELECT COUNT(*) FROM evaluaciondesempeno_contratousuario;
   
   -- Asignaciones creadas
   SELECT COUNT(*) FROM evaluaciondesempeno_asignacionactividad;
   ```

3. **Probar endpoint:**
   ```bash
   curl http://localhost:8000/api/lideres-actividades/lideres_vigentes/?area_id=3
   ```

## 🎉 **Resultado Esperado**

Después de la configuración tendrás:
- ✅ **Líder configurado** para evaluar actividades
- ✅ **Contratos creados** para todos los usuarios
- ✅ **Asignaciones automáticas** de actividades
- ✅ **Sistema funcionando** para evaluaciones de desempeño

¡El sistema estará listo para que los líderes evalúen las actividades de sus subordinados! 🚀

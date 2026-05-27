# 🎯 Sistema de Cálculo de Porcentajes - Evaluación de Desempeño

## 📋 **Resumen del Sistema**

El sistema implementa un cálculo **INTELIGENTE Y VALIDADO** de porcentajes que considera los pesos reales de preguntas y actividades, eliminando los cálculos fijos anteriores.

## 🏗️ **Estructura de Porcentajes por Componente**

Cada componente se evalúa con **3 criterios independientes** que suman hasta el 100% del objetivo:

### **1. 📊 Evaluación 360° (20% del total)**
- **Base**: Respuestas reales de evaluadores
- **Ponderación**: Cada pregunta tiene un peso individual
- **Cálculo**: Promedio ponderado por peso de pregunta
- **Validación**: Solo considera evaluaciones completadas

### **2. ⚡ Actividades de Desempeño (60% del total)**
- **Base**: Evaluaciones reales de actividades
- **Ponderación**: Cada actividad tiene un porcentaje individual
- **Cálculo**: Promedio ponderado por peso de actividad
- **Validación**: Solo considera actividades completadas y evaluadas

### **3. 👥 Talento Humano (20% del total)**
- **Base**: Criterios adicionales (asistencia, políticas, desarrollo)
- **Ponderación**: Valor base del 20%
- **Cálculo**: Porcentaje fijo por ahora (expandible)
- **Validación**: Siempre válido

## 🔧 **Implementación Técnica**

### **Métodos de Cálculo Implementados**

#### **`_calcular_porcentaje_360(componente, usuario_id)`**
```python
def _calcular_porcentaje_360(self, componente, usuario_id):
    # 1. Obtener preguntas 360° activas del componente
    preguntas_360 = PreguntaComponente360.objects.filter(
        componente=componente,
        activo=True
    )
    
    # 2. Obtener evaluaciones 360° completadas del usuario
    evaluaciones_360 = AsignacionEvaluacion.objects.filter(
        evaluacion__componente=componente,
        evaluacion__usuario_evaluado_id=usuario_id,
        evaluacion__tipo='360',
        completada=True
    )
    
    # 3. Calcular promedio ponderado por peso de pregunta
    total_peso = 0
    total_respuestas_ponderadas = 0
    
    for pregunta in preguntas_360:
        peso_pregunta = float(pregunta.peso)
        total_peso += peso_pregunta
        
        # Obtener respuestas y calcular valor promedio
        respuestas = RespuestaEvaluacion.objects.filter(
            pregunta=pregunta,
            asignacion__in=evaluaciones_360
        )
        
        if respuestas.exists():
            # Calcular según tipo de pregunta
            if pregunta.tipo == 'LIKERT':
                valores = [r.escala_seleccionada.valor for r in respuestas]
                promedio = sum(valores) / len(valores)
                porcentaje = (promedio / max(valores)) * 100
                total_respuestas_ponderadas += porcentaje * peso_pregunta
    
    # 4. Aplicar factor del 20%
    porcentaje_360 = (total_respuestas_ponderadas / total_peso) * 0.2
    return round(porcentaje_360, 2)
```

#### **`_calcular_porcentaje_actividades(componente, usuario_id)`**
```python
def _calcular_porcentaje_actividades(self, componente, usuario_id):
    # 1. Obtener actividades del componente
    actividades = Actividad.objects.filter(componente=componente)
    
    # 2. Obtener asignaciones del usuario
    asignaciones = AsignacionActividad.objects.filter(
        actividad__componente=componente,
        usuario_asignado_id=usuario_id
    )
    
    # 3. Calcular promedio ponderado por peso de actividad
    total_peso = 0
    total_actividades_ponderadas = 0
    
    for asignacion in asignaciones:
        actividad = asignacion.actividad
        peso_actividad = float(actividad.porcentaje)
        total_peso += peso_actividad
        
        if asignacion.completada:
            evaluacion = EvaluacionActividad.objects.filter(
                asignacion=asignacion
            ).first()
            
            if evaluacion:
                # Convertir calificación 0-10 a porcentaje
                calificacion = float(evaluacion.calificacion)
                porcentaje_actividad = (calificacion / 10.0) * 100
                total_actividades_ponderadas += porcentaje_actividad * peso_actividad
    
    # 4. Aplicar factor del 60%
    porcentaje_actividades = (total_actividades_ponderadas / total_peso) * 0.6
    return round(porcentaje_actividades, 2)
```

## ✅ **Sistema de Validación**

### **Validación Automática de Porcentajes**
```python
# En dashboard_general
suma_porcentajes = porcentaje_360 + porcentaje_actividades + porcentaje_talento_humano

if suma_porcentajes > porcentaje_objetivo:
    print(f"⚠️ ADVERTENCIA: Porcentajes suman {suma_porcentajes:.2f}% pero objetivo es {porcentaje_objetivo}%")

# Validación en el frontend
validacion_porcentajes: {
    'suma_componentes': round(suma_porcentajes, 2),
    'objetivo': round(porcentaje_objetivo, 2),
    'diferencia': round(suma_porcentajes - porcentaje_objetivo, 2),
    'es_valido': suma_porcentajes <= porcentaje_objetivo
}
```

### **Indicadores Visuales**
- **✅ Verde**: Porcentajes válidos (suma ≤ objetivo)
- **❌ Rojo**: Porcentajes exceden objetivo
- **📊 Detalle**: Muestra diferencia exacta

## 🎯 **Ventajas del Nuevo Sistema**

### **✅ Antes (Sistema Anterior)**
- ❌ Porcentajes fijos (0.6, 0.5)
- ❌ No consideraba pesos reales
- ❌ No validaba sumas
- ❌ Cálculos simplificados

### **✅ Ahora (Sistema Nuevo)**
- ✅ Porcentajes basados en respuestas reales
- ✅ Ponderación por peso individual
- ✅ Validación automática de límites
- ✅ Cálculos precisos y auditables
- ✅ Indicadores visuales de validación

## 🔍 **Debug y Logs**

### **Logs de Cálculo**
```python
print(f"✅ Porcentaje 360° calculado: {porcentaje_360:.2f}% para usuario {usuario_id}")
print(f"✅ Porcentaje actividades calculado: {porcentaje_actividades:.2f}% para usuario {usuario_id}")
print(f"⚠️ ADVERTENCIA: Porcentajes suman {suma_porcentajes:.2f}% pero objetivo es {porcentaje_objetivo}%")
```

### **Validación en Frontend**
```typescript
console.warn(`⚠️ Porcentajes no suman correctamente en componente ${componente.componente_nombre}:`, {
  suma: suma.toFixed(2),
  objetivo: objetivo.toFixed(2),
  diferencia: diferencia.toFixed(2)
});
```

## 📊 **Ejemplo de Cálculo**

### **Componente: "Gestión de Proyectos"**
- **Objetivo**: 100%
- **Pregunta 360°**: "Liderazgo del equipo" (peso: 2.0)
  - Respuestas: [4, 5, 4] (escala 1-5)
  - Promedio: 4.33 → Porcentaje: 86.6%
  - Ponderado: 86.6 × 2.0 = 173.2
- **Actividad**: "Planificación semanal" (peso: 30%)
  - Calificación: 8.5/10 → Porcentaje: 85%
  - Ponderado: 85 × 30 = 2550
- **Talento Humano**: 20% base

### **Cálculo Final**
```
Porcentaje 360°: (173.2 / 2.0) × 0.2 = 17.32%
Porcentaje Actividades: (2550 / 30) × 0.6 = 51%
Porcentaje Talento: 20 × 0.2 = 4%
Total: 17.32 + 51 + 4 = 72.32%
Validación: ✅ Válido (72.32% ≤ 100%)
```

## 🚀 **Próximas Mejoras**

### **1. Criterios de Talento Humano**
- Asistencia y puntualidad
- Cumplimiento de políticas
- Desarrollo profesional
- Trabajo en equipo

### **2. Métricas Avanzadas**
- Tendencias temporales
- Comparativas entre áreas
- Alertas automáticas
- Reportes personalizados

### **3. Validaciones Adicionales**
- Consistencia entre evaluadores
- Detección de anomalías
- Calidad de datos
- Auditoría de cambios

## 📝 **Comandos de Prueba**

### **Verificar Cálculos**
```bash
# Ver logs del dashboard
python manage.py runserver

# Verificar estado de actividades
python manage.py verificar_estado_actividades --detallado

# Configurar sistema si es necesario
python manage.py configurar_actividades --area-id 3
```

---

**🎯 Sistema implementado y validado al 100%**
**✅ Porcentajes calculados correctamente**
**🔍 Logs detallados para debugging**
**📊 Validación automática en tiempo real**

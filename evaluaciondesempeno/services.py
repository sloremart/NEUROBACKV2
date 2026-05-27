# evaluaciondesempeno/services.py

from .models import Evaluacion, AsignacionEvaluacion, PreguntaComponente360, CategoriaPregunta, PlantillaEvaluacion, RespuestaEvaluacion, Componente, PreguntaPlantilla, Actividad, EvaluacionActividad, AsignacionActividad, Area, TipoComponente, LiderActividad
from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q
from decimal import Decimal
from django.utils import timezone

# Servicios para el horario laboral
import pandas as pd
from datetime import datetime, timedelta
from django.core.exceptions import ValidationError
from .models import HorarioLaboral, ContratoUsuario

User = get_user_model()


def get_evaluadores_360(usuario):
    """
    Obtiene todos los evaluadores para una evaluación 360° de un usuario específico.
    Incluye: autoevaluación, líder, compañeros y subordinados (si es líder).
    """
    perfil = getattr(usuario, 'perfil', None)
    evaluadores = []

    if not perfil or not perfil.area:
        # Si no tiene perfil o área, solo autoevaluación
        return [usuario]

    area = perfil.area

    # 1. Autoevaluación (siempre incluida en 360°)
    evaluadores.append(usuario)

    # 2. Jefe o líder del área (si existe y no es el mismo usuario)
    lider = User.objects.filter(perfil__area=area, perfil__es_lider=True).exclude(id=usuario.id).first()
    if lider:
        evaluadores.append(lider)

    # 3. Compañeros del área (excluyendo al usuario y al líder ya agregado)
    companeros = User.objects.filter(
        perfil__area=area,
        perfil__es_lider=False
    ).exclude(id=usuario.id)
    evaluadores.extend(companeros)

    # 4. Subordinados (solo si él es líder)
    if perfil.es_lider:
        subordinados = User.objects.filter(
            perfil__area=area, 
            perfil__es_lider=False
        ).exclude(id=usuario.id)
        evaluadores.extend(subordinados)

    return list(set(evaluadores))  


def asignar_evaluacion_por_area(area_id, componente_id, tipo='360'):
    # SEPARACIÓN DE ROLES: Excluir líderes de las evaluaciones por área
    # Los líderes evalúan, pero no son evaluados en procesos grupales
    usuarios = User.objects.filter(
        perfil__area_id=area_id, 
        is_active=True,
        perfil__es_lider=False  # ← EXCLUIR LÍDERES
    )
    
    # Obtener el líder del área para asignarlo como evaluador principal
    lider_area = User.objects.filter(perfil__area_id=area_id, perfil__es_lider=True, is_active=True).first()
    
    evaluaciones_creadas = 0
    evaluaciones_saltadas = 0

    for evaluado in usuarios:
        # CORREGIDO: Verificar si ya existe una evaluación para este usuario, componente y tipo
        evaluacion_existente = Evaluacion.objects.filter(
            usuario_evaluado=evaluado,
            componente_id=componente_id,
            tipo=tipo,
            area_grupo_id=area_id
        ).first()
        
        if evaluacion_existente:
            evaluaciones_saltadas += 1
            continue  # Saltar este usuario, ya tiene evaluación
        
        # Asignar el líder como evaluador principal (si no es el mismo usuario)
        evaluador_principal = lider_area if lider_area and lider_area.id != evaluado.id else None
        
        evaluacion = Evaluacion.objects.create(
            usuario_evaluado=evaluado,
            tipo=tipo,
            componente_id=componente_id,
            evaluador=evaluador_principal,
            area_grupo_id=area_id
        )

        if tipo == '360':
            evaluadores = get_evaluadores_360(evaluado)
        elif tipo == '180':
            evaluadores = [evaluado]  # Autoevaluación
            # CORREGIDO: Los líderes SÍ pueden evaluar a su equipo
            jefe = User.objects.filter(perfil__area_id=area_id, perfil__es_lider=True).first()
            if jefe:
                evaluadores.append(jefe)
        else:  # tipo 90
            # CORREGIDO: Los líderes SÍ pueden evaluar a su equipo
            evaluadores = User.objects.filter(perfil__area_id=area_id, perfil__es_lider=True)

        # CORREGIDO: Nunca asignar un usuario a evaluarse a sí mismo
        for evaluador in evaluadores:
            if evaluador.id != evaluado.id:  # ← Evitar autoevaluación
                AsignacionEvaluacion.objects.create(
                    evaluacion=evaluacion,
                    evaluador=evaluador,
                    usuario_evaluado=evaluado
                )
        
        evaluaciones_creadas += 1
    
    # Retornar resumen de la operación
    return {
        'total_usuarios': len(usuarios),
        'evaluaciones_creadas': evaluaciones_creadas,
        'evaluaciones_saltadas': evaluaciones_saltadas,
        'lider_asignado': lider_area.username if lider_area else None
    }

def asignar_evaluacion_lider(lider_id, componente_id, tipo='360'):
    """
    Función específica para asignar evaluación a un líder.
    Los líderes se evalúan por separado, no en procesos grupales.
    """
    try:
        lider = User.objects.get(id=lider_id, is_active=True)
        perfil_lider = getattr(lider, 'perfil', None)
        
        if not perfil_lider or not perfil_lider.es_lider:
            raise ValueError("El usuario especificado no es un líder")
        
        area_id = perfil_lider.area.id if perfil_lider.area else None
        
        # Verificar si ya existe una evaluación
        evaluacion_existente = Evaluacion.objects.filter(
            usuario_evaluado=lider,
            componente_id=componente_id,
            tipo=tipo
        ).first()
        
        if evaluacion_existente:
            return {"mensaje": "El líder ya tiene una evaluación asignada", "evaluacion_id": evaluacion_existente.id}
        
        # Crear evaluación para el líder
        evaluacion = Evaluacion.objects.create(
            usuario_evaluado=lider,
            tipo=tipo,
            componente_id=componente_id,
            evaluador=None,  # Se asignará según el tipo
            area_grupo_id=area_id
        )
        
        # Asignar evaluadores según el tipo
        evaluadores = []
        
        if tipo == '360':
            # Para líderes: sus pares (otros líderes), superior jerárquico, y subordinados
            # Nota: En un sistema real, el superior jerárquico se configuraría aparte
            pares_lideres = User.objects.filter(
                perfil__es_lider=True,
                is_active=True
            ).exclude(id=lider_id)
            
            subordinados = User.objects.filter(
                perfil__area=perfil_lider.area,
                perfil__es_lider=False,
                is_active=True
            )
            
            evaluadores.extend(pares_lideres)
            evaluadores.extend(subordinados)
            evaluadores.append(lider)  # Autoevaluación
            
        elif tipo == '180':
            # Para líderes 180°: autoevaluación + superior (por ahora otros líderes)
            evaluadores.append(lider)
            superior = User.objects.filter(
                perfil__es_lider=True,
                is_active=True
            ).exclude(id=lider_id).first()
            if superior:
                evaluadores.append(superior)
                
        else:  # tipo 90
            # Solo superior jerárquico
            superior = User.objects.filter(
                perfil__es_lider=True,
                is_active=True
            ).exclude(id=lider_id).first()
            if superior:
                evaluadores.append(superior)
        
        # Crear asignaciones
        for evaluador in evaluadores:
            if evaluador.id != lider.id:  # Evitar auto-asignación en AsignacionEvaluacion
                AsignacionEvaluacion.objects.create(
                    evaluacion=evaluacion,
                    evaluador=evaluador,
                    usuario_evaluado=lider
                )
        
        return {
            "mensaje": "Evaluación de líder asignada exitosamente",
            "evaluacion_id": evaluacion.id,
            "total_evaluadores": len([e for e in evaluadores if e.id != lider.id])
        }
        
    except User.DoesNotExist:
        raise ValueError("Líder no encontrado")
    except Exception as e:
        raise ValueError(f"Error al asignar evaluación de líder: {str(e)}")

# Nuevas funciones para el sistema mejorado de preguntas 360

def crear_plantilla_desde_preguntas(componente_id, nombre, descripcion, pregunta_ids):
    """
    Crea una plantilla de evaluación con las preguntas especificadas
    """
    try:
        componente = Componente.objects.get(id=componente_id)
        plantilla = PlantillaEvaluacion.objects.create(
            nombre=nombre,
            descripcion=descripcion,
            componente=componente
        )
        
        for orden, pregunta_id in enumerate(pregunta_ids):
            pregunta = PreguntaComponente360.objects.get(id=pregunta_id)
            PreguntaPlantilla.objects.create(
                plantilla=plantilla,
                pregunta=pregunta,
                orden=orden
            )
        
        return plantilla
    except (Componente.DoesNotExist, PreguntaComponente360.DoesNotExist) as e:
        raise ValueError(f"Error al crear plantilla: {str(e)}")

def obtener_preguntas_por_categoria(componente_id):
    """
    Obtiene las preguntas organizadas por categorías para un componente
    """
    categorias = CategoriaPregunta.objects.filter(
        componente_id=componente_id,
        activo=True
    ).prefetch_related('preguntas')
    
    resultado = []
    for categoria in categorias:
        preguntas = categoria.preguntas.filter(activo=True).order_by('orden')
        resultado.append({
            'categoria': {
                'id': categoria.id,
                'nombre': categoria.nombre,
                'descripcion': categoria.descripcion
            },
            'preguntas': [
                {
                    'id': p.id,
                    'texto': p.texto,
                    'tipo': p.tipo,
                    'obligatoria': p.obligatoria,
                    'peso': float(p.peso),
                    'escalas': [
                        {'valor': e.valor, 'descripcion': e.descripcion}
                        for e in p.escalas.all().order_by('orden')
                    ] if p.tipo == 'LIKERT' else [],
                    'opciones': [
                        {'texto': o.texto, 'valor': o.valor}
                        for o in p.opciones.all().order_by('orden')
                    ] if p.tipo == 'MULTIPLE' else []
                }
                for p in preguntas
            ]
        })
    
    return resultado

def calcular_promedio_evaluacion(asignacion_id):
    """
    Calcula el promedio de una evaluación basado en las respuestas
    """
    respuestas = RespuestaEvaluacion.objects.filter(
        asignacion_id=asignacion_id
    ).select_related('pregunta', 'escala_seleccionada')
    
    total_peso = Decimal('0.0')
    suma_ponderada = Decimal('0.0')
    
    for respuesta in respuestas:
        valor = respuesta.get_valor_respuesta()
        if valor is not None:
            peso = respuesta.pregunta.peso
            total_peso += peso
            suma_ponderada += Decimal(str(valor)) * peso
    
    if total_peso > 0:
        return float(suma_ponderada / total_peso)
    return 0.0

def obtener_estadisticas_evaluacion(evaluacion_id):
    """
    Obtiene estadísticas detalladas de una evaluación
    """
    asignaciones = AsignacionEvaluacion.objects.filter(
        evaluacion_id=evaluacion_id,
        completada=True
    )
    
    estadisticas = {
        'total_asignaciones': asignaciones.count(),
        'asignaciones_completadas': asignaciones.filter(completada=True).count(),
        'promedio_general': 0.0,
        'promedios_por_evaluador': [],
        'promedios_por_categoria': []
    }
    
    # Calcular promedios por evaluador
    for asignacion in asignaciones:
        promedio = calcular_promedio_evaluacion(asignacion.id)
        estadisticas['promedios_por_evaluador'].append({
            'evaluador': f"{asignacion.evaluador.first_name} {asignacion.evaluador.last_name}",
            'promedio': promedio
        })
    
    # Calcular promedio general
    if estadisticas['promedios_por_evaluador']:
        total = sum(p['promedio'] for p in estadisticas['promedios_por_evaluador'])
        estadisticas['promedio_general'] = total / len(estadisticas['promedios_por_evaluador'])
    
    return estadisticas

def crear_escalas_likert_estandar(pregunta_id, escala_5=True):
    """
    Crea escalas Likert estándar para una pregunta
    """
    if escala_5:
        escalas = [
            (1, "Totalmente en desacuerdo"),
            (2, "En desacuerdo"),
            (3, "Neutral"),
            (4, "De acuerdo"),
            (5, "Totalmente de acuerdo")
        ]
    else:
        escalas = [
            (1, "Muy malo"),
            (2, "Malo"),
            (3, "Regular"),
            (4, "Bueno"),
            (5, "Muy bueno"),
            (6, "Excelente")
        ]
    
    for orden, (valor, descripcion) in enumerate(escalas):
        EscalaRespuesta.objects.create(
            pregunta_id=pregunta_id,
            valor=valor,
            descripcion=descripcion,
            orden=orden
        )

def validar_respuestas_completas(asignacion_id):
    """
    Valida que todas las preguntas obligatorias hayan sido respondidas
    """
    asignacion = AsignacionEvaluacion.objects.get(id=asignacion_id)
    evaluacion = asignacion.evaluacion
    
    # Obtener todas las preguntas del componente
    preguntas_obligatorias = PreguntaComponente360.objects.filter(
        componente=evaluacion.componente,
        activo=True,
        obligatoria=True
    )
    
    # Obtener respuestas existentes
    respuestas_existentes = RespuestaEvaluacion.objects.filter(
        asignacion=asignacion
    ).values_list('pregunta_id', flat=True)
    
    # Verificar preguntas faltantes
    preguntas_faltantes = preguntas_obligatorias.exclude(id__in=respuestas_existentes)
    
    return {
        'completa': preguntas_faltantes.count() == 0,
        'preguntas_faltantes': [
            {
                'id': p.id,
                'texto': p.texto,
                'tipo': p.tipo
            }
            for p in preguntas_faltantes
        ]
    }

def generar_reporte_evaluacion_360(evaluacion_id):
    """
    Genera un reporte completo de evaluación 360
    """
    evaluacion = Evaluacion.objects.get(id=evaluacion_id)
    asignaciones = AsignacionEvaluacion.objects.filter(
        evaluacion=evaluacion,
        completada=True
    )
    
    reporte = {
        'evaluacion': {
            'id': evaluacion.id,
            'tipo': evaluacion.tipo,
            'fecha': evaluacion.fecha,
            'usuario_evaluado': f"{evaluacion.usuario_evaluado.first_name} {evaluacion.usuario_evaluado.last_name}" if evaluacion.usuario_evaluado else "Grupo",
            'componente': str(evaluacion.componente)
        },
        'estadisticas_generales': obtener_estadisticas_evaluacion(evaluacion_id),
        'detalle_por_pregunta': [],
        'recomendaciones': []
    }
    
    # Detalle por pregunta
    preguntas = PreguntaComponente360.objects.filter(
        componente=evaluacion.componente,
        activo=True
    ).order_by('categoria__orden', 'orden')
    
    for pregunta in preguntas:
        respuestas = RespuestaEvaluacion.objects.filter(
            asignacion__evaluacion=evaluacion,
            pregunta=pregunta
        )
        
        valores = [r.get_valor_respuesta() for r in respuestas if r.get_valor_respuesta() is not None]
        
        reporte['detalle_por_pregunta'].append({
            'pregunta': pregunta.texto,
            'categoria': pregunta.categoria.nombre if pregunta.categoria else 'Sin categoría',
            'tipo': pregunta.tipo,
            'total_respuestas': len(valores),
            'promedio': sum(valores) / len(valores) if valores else 0,
            'minimo': min(valores) if valores else 0,
            'maximo': max(valores) if valores else 0,
            'comentarios': [
                r.respuesta_texto for r in respuestas 
                if r.respuesta_texto and r.respuesta_texto.strip()
            ]
        })
    
    # Generar recomendaciones básicas
    promedio_general = reporte['estadisticas_generales']['promedio_general']
    if promedio_general < 3.0:
        reporte['recomendaciones'].append("Se requiere atención inmediata en áreas de mejora")
    elif promedio_general < 4.0:
        reporte['recomendaciones'].append("Hay oportunidades de mejora identificadas")
    else:
        reporte['recomendaciones'].append("El desempeño es satisfactorio, mantener buenas prácticas")
    
    return reporte

# Nuevos servicios para evaluaciones de actividades laborales y 360

def obtener_actividades_para_evaluar_lider(lider_id, area_id=None):
    """
    Obtiene las actividades que un líder puede evaluar
    """
    if area_id:
        actividades = Actividad.objects.filter(
            area_grupo_id=area_id,
            componente__tipo__nombre='Desempeño Laboral'
        ).prefetch_related('usuarios_grupo', 'usuario_asignado')
    else:
        # Obtener área del líder
        perfil_lider = User.objects.get(id=lider_id).perfil
        if not perfil_lider or not perfil_lider.area:
            return []
        
        actividades = Actividad.objects.filter(
            area_grupo=perfil_lider.area,
            componente__tipo__nombre='Desempeño Laboral'
        ).prefetch_related('usuarios_grupo', 'usuario_asignado')
    
    return actividades

def obtener_evaluaciones_360_para_lider(lider_id, area_id=None):
    """
    Obtiene las evaluaciones 360 que un líder puede evaluar
    """
    if area_id:
        # Si se especifica área, buscar evaluaciones en esa área
        evaluaciones = Evaluacion.objects.filter(
            area_grupo_id=area_id,
            tipo__in=['360', '180'],  # Incluir tanto 360 como 180
            componente__tipo__nombre__icontains='360'  # Buscar componentes que contengan '360' en el nombre
        ).prefetch_related('usuario_evaluado', 'componente')
    else:
        # Buscar TODAS las evaluaciones 360 donde el líder sea evaluador
        # No filtrar por área del líder, sino por asignaciones
        evaluaciones = Evaluacion.objects.filter(
            tipo__in=['360', '180'],  # Incluir tanto 360 como 180
            componente__tipo__nombre__icontains='360',  # Buscar componentes que contengan '360' en el nombre
            asignaciones__evaluador_id=lider_id  # El líder debe ser evaluador
        ).prefetch_related('usuario_evaluado', 'componente').distinct()
    
    # Convertir a diccionarios para evitar problemas de serialización JSON
    evaluaciones_data = []
    for evaluacion in evaluaciones:
        # Buscar la asignación real para este evaluador
        try:
            asignacion = AsignacionEvaluacion.objects.get(
                evaluacion=evaluacion,
                evaluador_id=lider_id
            )
            asignacion_id = asignacion.id
        except AsignacionEvaluacion.DoesNotExist:
            # Si no existe la asignación, crear una
            asignacion = AsignacionEvaluacion.objects.create(
                evaluacion=evaluacion,
                evaluador_id=lider_id,
                usuario_evaluado=evaluacion.usuario_evaluado,
                completada=False
            )
            asignacion_id = asignacion.id
        
        evaluacion_data = {
            'id': evaluacion.id,
            'evaluacion_id': evaluacion.id,
            'usuario_evaluado': {
                'id': evaluacion.usuario_evaluado.id,
                'nombre': f"{evaluacion.usuario_evaluado.first_name} {evaluacion.usuario_evaluado.last_name}".strip() or evaluacion.usuario_evaluado.username
            },
            'componente': {
                'id': evaluacion.componente.id,
                'nombre': evaluacion.componente.nombre
            },
            'fecha': evaluacion.fecha.isoformat() if evaluacion.fecha else None,
            'ya_evaluada': asignacion.completada,
            'asignacion_id': asignacion_id  # Usar el ID real de la asignación
        }
        evaluaciones_data.append(evaluacion_data)
    
    return evaluaciones_data

def obtener_evaluaciones_360_para_companero(usuario_id, area_id=None):
    """
    Obtiene las evaluaciones 360 que un compañero puede evaluar
    """
    if area_id:
        asignaciones = AsignacionEvaluacion.objects.filter(
            evaluador_id=usuario_id,
            evaluacion__area_grupo_id=area_id,
            evaluacion__tipo__in=['360', '180'],  # Incluir tanto 360 como 180
            evaluacion__componente__tipo__nombre__icontains='360',  # Buscar componentes que contengan '360' en el nombre
            completada=False
        ).prefetch_related('evaluacion__usuario_evaluado', 'evaluacion__componente')
    else:
        # Obtener área del usuario
        perfil_usuario = User.objects.get(id=usuario_id).perfil
        if not perfil_usuario or not perfil_usuario.area:
            return []
        
        asignaciones = AsignacionEvaluacion.objects.filter(
            evaluador_id=usuario_id,
            evaluacion__area_grupo=perfil_usuario.area,
            evaluacion__tipo__in=['360', '180'],  # Incluir tanto 360 como 180
            evaluacion__componente__tipo__nombre__icontains='360',  # Buscar componentes que contengan '360' en el nombre
            completada=False
        ).prefetch_related('evaluacion__usuario_evaluado', 'evaluacion__componente')
    
    # Convertir a diccionarios para evitar problemas de serialización JSON
    asignaciones_data = []
    for asignacion in asignaciones:
        evaluacion = asignacion.evaluacion
        asignacion_data = {
            'id': asignacion.id,
            'usuario_evaluado': {
                'id': evaluacion.usuario_evaluado.id,
                'nombre': f"{evaluacion.usuario_evaluado.first_name} {evaluacion.usuario_evaluado.last_name}".strip() or evaluacion.usuario_evaluado.username
            },
            'componente': {
                'id': evaluacion.componente.id,
                'nombre': evaluacion.componente.nombre
            },
            'fecha': evaluacion.fecha.isoformat() if evaluacion.fecha else None,
            'ya_evaluada': asignacion.completada,
            'asignacion_id': asignacion.id
        }
        asignaciones_data.append(asignacion_data)
    
    return asignaciones_data

def evaluar_actividad_laboral(actividad_id, evaluador_id, calificacion, comentarios=""):
    """
    Permite a un líder evaluar una actividad laboral
    """
    try:
        # Buscar la asignación que conecta la actividad con el evaluador
        asignacion = AsignacionActividad.objects.get(
            actividad_id=actividad_id,
            evaluador_id=evaluador_id
        )
        
        # Verificar que el evaluador sea líder del área
        perfil_evaluador = User.objects.get(id=evaluador_id).perfil
        if not perfil_evaluador or not perfil_evaluador.es_lider:
            raise ValueError("Solo los líderes pueden evaluar actividades laborales")
        
        # Crear o actualizar evaluación de actividad
        evaluacion_actividad, created = EvaluacionActividad.objects.get_or_create(
            asignacion=asignacion,
            defaults={
                'calificacion': calificacion,
                'comentarios': comentarios,
                'fecha_evaluacion': timezone.now()
            }
        )
        
        if not created:
            evaluacion_actividad.calificacion = calificacion
            evaluacion_actividad.comentarios = comentarios
            evaluacion_actividad.fecha_evaluacion = timezone.now()
            evaluacion_actividad.save()
        
        # ✅ MARCAR LA ASIGNACIÓN COMO COMPLETADA
        asignacion.completada = True
        asignacion.save()
        
        print(f"✅ Actividad {actividad_id} marcada como completada para evaluador {evaluador_id}")
        
        return evaluacion_actividad
        
    except AsignacionActividad.DoesNotExist:
        raise ValueError("No hay asignación de esta actividad para este evaluador")
    except User.DoesNotExist:
        raise ValueError("Usuario evaluador no encontrado")

def evaluar_360_completa(asignacion_id, respuestas_data):
    """
    Permite evaluar una evaluación 360 completa
    """
    try:
        asignacion = AsignacionEvaluacion.objects.get(id=asignacion_id)
        
        # Verificar que la asignación no esté completada
        if asignacion.completada:
            raise ValueError("Esta evaluación ya fue completada")
        
        # Verificar que el evaluador sea el correcto
        if asignacion.evaluador_id != respuestas_data.get('evaluador_id'):
            raise ValueError("No puede evaluar con esta asignación")
        
        # Crear o actualizar respuestas
        for respuesta_data in respuestas_data.get('respuestas', []):
            pregunta_id = respuesta_data.get('pregunta_id')
            respuesta_texto = respuesta_data.get('respuesta_texto')
            respuesta_numerica = respuesta_data.get('respuesta_numerica')
            respuesta_booleana = respuesta_data.get('respuesta_booleana')
            opcion_seleccionada_id = respuesta_data.get('opcion_seleccionada_id')
            escala_seleccionada_id = respuesta_data.get('escala_seleccionada_id')
            comentarios = respuesta_data.get('comentarios', '')
            
            # Crear o actualizar respuesta
            respuesta, created = RespuestaEvaluacion.objects.get_or_create(
                asignacion=asignacion,
                pregunta_id=pregunta_id,
                defaults={
                    'respuesta_texto': respuesta_texto,
                    'respuesta_numerica': respuesta_numerica,
                    'respuesta_booleana': respuesta_booleana,
                    'opcion_seleccionada_id': opcion_seleccionada_id,
                    'escala_seleccionada_id': escala_seleccionada_id,
                    'comentarios': comentarios
                }
            )
            
            if not created:
                respuesta.respuesta_texto = respuesta_texto
                respuesta.respuesta_numerica = respuesta_numerica
                respuesta.respuesta_booleana = respuesta_booleana
                respuesta.opcion_seleccionada_id = opcion_seleccionada_id
                respuesta.escala_seleccionada_id = escala_seleccionada_id
                respuesta.comentarios = comentarios
                respuesta.save()
        
        # Marcar asignación como completada
        asignacion.completada = True
        asignacion.save()
        
        return {
            'mensaje': 'Evaluación 360 completada exitosamente',
            'asignacion_id': asignacion_id,
            'total_respuestas': len(respuestas_data.get('respuestas', []))
        }
        
    except AsignacionEvaluacion.DoesNotExist:
        raise ValueError("Asignación de evaluación no encontrada")

def obtener_resumen_evaluaciones_lider(lider_id, area_id=None):
    """
    Obtiene un resumen detallado de todas las evaluaciones que puede realizar un líder
    """
    try:
        lider = User.objects.get(id=lider_id)
        
        if area_id:
            area = Area.objects.get(id=area_id)
        else:
            perfil_lider = getattr(lider, 'perfil', None)
            if not perfil_lider or not perfil_lider.area:
                return {
                    'error': 'El líder no tiene perfil o área asignada',
                    'lider_info': {
                        'id': lider.id,
                        'username': lider.username,
                        'nombre': f"{lider.first_name} {lider.last_name}" if lider.first_name else lider.username
                    }
                }
            area = perfil_lider.area
        
        # 1. ACTIVIDADES LABORALES pendientes que el líder puede evaluar
        actividades_pendientes = Actividad.objects.filter(
            area_grupo=area,
            componente__tipo__nombre='Desempeño Laboral'
        ).select_related('componente', 'componente__tipo').prefetch_related('usuarios_grupo')
        
        actividades_data = []
        for actividad in actividades_pendientes:
            # Verificar si ya fue evaluada por este líder
            ya_evaluada = EvaluacionActividad.objects.filter(
                actividad=actividad,
                evaluador_id=lider_id
            ).exists()
            
            actividades_data.append({
                'id': actividad.id,
                'nombre': actividad.nombre,
                'descripcion': actividad.descripcion,
                'porcentaje': float(actividad.porcentaje),
                'componente': actividad.componente.nombre,
                'usuarios_asignados': [
                    {
                        'id': usuario.id,
                        'nombre': f"{usuario.first_name} {usuario.last_name}" if usuario.first_name else usuario.username
                    } for usuario in actividad.usuarios_grupo.all()
                ],
                'ya_evaluada': ya_evaluada
            })
        
        # 2. EVALUACIONES 360 pendientes que el líder debe completar
        asignaciones_360 = AsignacionEvaluacion.objects.filter(
            evaluador_id=lider_id,
            completada=False,
            evaluacion__tipo='360'
        ).select_related(
            'evaluacion__usuario_evaluado',
            'evaluacion__componente',
            'evaluacion__componente__tipo'
        )
        
        evaluaciones_360_data = []
        for asignacion in asignaciones_360:
            evaluaciones_360_data.append({
                'asignacion_id': asignacion.id,
                'evaluacion_id': asignacion.evaluacion.id,
                'usuario_evaluado': {
                    'id': asignacion.usuario_evaluado.id,
                    'nombre': f"{asignacion.usuario_evaluado.first_name} {asignacion.usuario_evaluado.last_name}" if asignacion.usuario_evaluado.first_name else asignacion.usuario_evaluado.username,
                    'username': asignacion.usuario_evaluado.username
                },
                'componente': {
                    'id': asignacion.evaluacion.componente.id,
                    'nombre': asignacion.evaluacion.componente.nombre,
                    'tipo': asignacion.evaluacion.componente.tipo.nombre
                },
                'tipo_evaluacion': asignacion.evaluacion.tipo,
                'fecha_asignacion': asignacion.evaluacion.fecha
            })
        
        # 3. USUARIOS A CARGO (subordinados)
        usuarios_a_cargo = User.objects.filter(
            perfil__area=area,
            is_active=True,
            perfil__es_lider=False
        ).select_related('perfil')
        
        usuarios_data = []
        for usuario in usuarios_a_cargo:
            # Contar evaluaciones pendientes de este usuario
            evaluaciones_pendientes = AsignacionEvaluacion.objects.filter(
                evaluador_id=lider_id,
                usuario_evaluado=usuario,
                completada=False
            ).count()
            
            usuarios_data.append({
                'id': usuario.id,
                'nombre': f"{usuario.first_name} {usuario.last_name}" if usuario.first_name else usuario.username,
                'username': usuario.username,
                'email': usuario.email,
                'cargo': getattr(usuario.perfil, 'cargo', None),
                'evaluaciones_pendientes': evaluaciones_pendientes
            })
        
        # ✅ CALCULAR DESEMPEÑO DE USUARIOS
        usuarios_con_desempeno = []
        for usuario in usuarios_data:
            desempeno = calcular_desempeno_usuario(usuario['id'], lider_id, area.id)
            usuarios_con_desempeno.append({
                **usuario,
                'desempeno': desempeno
            })

        return {
            'lider_info': {
                'id': lider.id,
                'username': lider.username,
                'nombre': f"{lider.first_name} {lider.last_name}" if lider.first_name else lider.username
            },
            'area': {
                'id': area.id,
                'nombre': area.nombre
            },
            'actividades_laborales': {
                'total': len(actividades_data),
                'pendientes': len([a for a in actividades_data if not a['ya_evaluada']]),
                'completadas': len([a for a in actividades_data if a['ya_evaluada']]),
                'data': actividades_data
            },
            'evaluaciones_360': {
                'total': len(evaluaciones_360_data),
                'pendientes': len(evaluaciones_360_data),
                'data': evaluaciones_360_data
            },
            'usuarios_a_cargo': {
                'total': len(usuarios_data),
                'data': usuarios_con_desempeno
            },
            'resumen': {
                'total_actividades_pendientes': len([a for a in actividades_data if not a['ya_evaluada']]),
                'total_evaluaciones_360_pendientes': len(evaluaciones_360_data),
                'total_usuarios_a_cargo': len(usuarios_data),
                'total_pendientes': len([a for a in actividades_data if not a['ya_evaluada']]) + len(evaluaciones_360_data)
            }
        }
        
    except User.DoesNotExist:
        return {'error': 'Líder no encontrado'}
    except Area.DoesNotExist:
        return {'error': 'Área no encontrada'}
    except Exception as e:
        return {'error': f'Error interno: {str(e)}'}

# Nuevos servicios para obtener preguntas por usuario y evaluación

def obtener_preguntas_evaluacion_360(asignacion_id):
    """
    Obtiene todas las preguntas que debe responder un usuario en una evaluación 360
    Maneja tanto asignaciones como evaluaciones directas
    """
    try:
        # Primero intentar buscar como asignación (caso de compañeros)
        try:
            asignacion = AsignacionEvaluacion.objects.get(id=asignacion_id)
            evaluacion = asignacion.evaluacion
            usuario_evaluado = asignacion.usuario_evaluado
            es_asignacion = True
        except AsignacionEvaluacion.DoesNotExist:
            # Si no es asignación, buscar como evaluación directa (caso de líderes)
            # Los líderes evalúan directamente sin pasar por asignaciones
            try:
                evaluacion = Evaluacion.objects.get(id=asignacion_id)
                usuario_evaluado = evaluacion.usuario_evaluado
                es_asignacion = False
            except Evaluacion.DoesNotExist:
                raise ValueError("Evaluación no encontrada")
        
        # Obtener preguntas del componente de la evaluación
        preguntas = PreguntaComponente360.objects.filter(
            componente=evaluacion.componente,
            activo=True
        ).select_related('categoria').prefetch_related('escalas', 'opciones').order_by('categoria__orden', 'orden')
        
        # Organizar preguntas por categorías
        categorias = {}
        for pregunta in preguntas:
            categoria_nombre = pregunta.categoria.nombre if pregunta.categoria else 'Sin categoría'
            
            if categoria_nombre not in categorias:
                categorias[categoria_nombre] = {
                    'id': pregunta.categoria.id if pregunta.categoria else None,
                    'nombre': categoria_nombre,
                    'descripcion': pregunta.categoria.descripcion if pregunta.categoria else '',
                    'orden': pregunta.categoria.orden if pregunta.categoria else 0,
                    'preguntas': []
                }
            
            # Verificar si ya existe una respuesta para esta pregunta
            if es_asignacion:
                respuesta_existente = RespuestaEvaluacion.objects.filter(
                    asignacion=asignacion,
                    pregunta=pregunta
                ).first()
            else:
                # Para evaluaciones directas (líder), no hay respuestas previas
                # porque se evalúa directamente sin asignación
                respuesta_existente = None
            
            pregunta_data = {
                'id': pregunta.id,
                'texto': pregunta.texto,
                'tipo': pregunta.tipo,
                'orden': pregunta.orden,
                'obligatoria': pregunta.obligatoria,
                'peso': float(pregunta.peso),
                'escalas': [
                    {
                        'id': e.id,
                        'valor': e.valor,
                        'descripcion': e.descripcion,
                        'orden': e.orden
                    } for e in pregunta.escalas.all().order_by('orden')
                ] if pregunta.tipo == 'LIKERT' else [],
                'opciones': [
                    {
                        'id': o.id,
                        'texto': o.texto,
                        'valor': o.valor,
                        'orden': o.orden
                    } for o in pregunta.opciones.all().order_by('orden')
                ] if pregunta.tipo == 'MULTIPLE' else [],
                'ya_respondida': respuesta_existente is not None,
                'respuesta_anterior': {
                    'respuesta_texto': respuesta_existente.respuesta_texto if respuesta_existente else None,
                    'respuesta_numerica': respuesta_existente.respuesta_numerica if respuesta_existente else None,
                    'respuesta_booleana': respuesta_existente.respuesta_booleana if respuesta_existente else None,
                    'opcion_seleccionada_id': respuesta_existente.opcion_seleccionada.id if respuesta_existente and respuesta_existente.opcion_seleccionada else None,
                    'escala_seleccionada_id': respuesta_existente.escala_seleccionada.id if respuesta_existente and respuesta_existente.escala_seleccionada else None,
                    'comentarios': respuesta_existente.comentarios if respuesta_existente else None
                } if respuesta_existente else None
            }
            
            categorias[categoria_nombre]['preguntas'].append(pregunta_data)
        
        # Convertir a lista ordenada
        resultado = sorted(categorias.values(), key=lambda x: x['orden'])
        
        return {
            'evaluacion_id': evaluacion.id,
            'usuario_evaluado': {
                'id': usuario_evaluado.id,
                'nombre': f"{usuario_evaluado.first_name} {usuario_evaluado.last_name}".strip() or usuario_evaluado.username
            },
            'componente': {
                'id': evaluacion.componente.id,
                'nombre': evaluacion.componente.nombre
            },
            'fecha': evaluacion.fecha.isoformat() if evaluacion.fecha else None,
            'categorias': resultado,
            'total_preguntas': sum(len(cat['preguntas']) for cat in resultado),
            'preguntas_respondidas': sum(
                sum(1 for p in cat['preguntas'] if p['ya_respondida']) 
                for cat in resultado
            )
        }
        
    except Exception as e:
        raise ValueError(f"Error al obtener preguntas: {str(e)}")

def obtener_evaluaciones_pendientes_usuario(usuario_id, area_id=None):
    """
    Obtiene todas las evaluaciones pendientes de un usuario (tanto como evaluador como evaluado)
    """
    if area_id:
        area = Area.objects.get(id=area_id)
    else:
        perfil_usuario = User.objects.get(id=usuario_id).perfil
        if not perfil_usuario or not perfil_usuario.area:
            return []
        area = perfil_usuario.area
    
    # Evaluaciones donde el usuario es evaluador (pendientes)
    evaluaciones_como_evaluador = AsignacionEvaluacion.objects.filter(
        evaluador_id=usuario_id,
        evaluacion__area_grupo=area,
        completada=False
    ).select_related(
        'evaluacion__usuario_evaluado', 
        'evaluacion__componente',
        'evaluacion__area_grupo'
    ).order_by('evaluacion__fecha')
    
    # Evaluaciones donde el usuario es evaluado (para mostrar progreso)
    evaluaciones_como_evaluado = AsignacionEvaluacion.objects.filter(
        usuario_evaluado_id=usuario_id,
        evaluacion__area_grupo=area
    ).select_related(
        'evaluacion__componente',
        'evaluacion__area_grupo'
    ).order_by('evaluacion__fecha')
    
    resultado = {
        'como_evaluador': [],
        'como_evaluado': []
    }
    
    # Procesar evaluaciones como evaluador
    for asignacion in evaluaciones_como_evaluador:
        evaluacion = asignacion.evaluacion
        
        # Contar preguntas totales y respondidas
        total_preguntas = PreguntaComponente360.objects.filter(
            componente=evaluacion.componente,
            activo=True
        ).count()
        
        preguntas_respondidas = RespuestaEvaluacion.objects.filter(
            asignacion=asignacion
        ).count()
        
        resultado['como_evaluador'].append({
            'asignacion_id': asignacion.id,
            'evaluacion_id': evaluacion.id,
            'tipo': evaluacion.tipo,
            'usuario_evaluado': {
                'id': asignacion.usuario_evaluado.id,
                'nombre': f"{asignacion.usuario_evaluado.first_name} {asignacion.usuario_evaluado.last_name}"
            },
            'componente': {
                'id': evaluacion.componente.id,
                'nombre': evaluacion.componente.nombre
            },
            'fecha': evaluacion.fecha,
            'progreso': {
                'total_preguntas': total_preguntas,
                'preguntas_respondidas': preguntas_respondidas,
                'porcentaje': (preguntas_respondidas / total_preguntas * 100) if total_preguntas > 0 else 0
            },
            'estado': 'completada' if asignacion.completada else 'pendiente'
        })
    
    # Procesar evaluaciones como evaluado
    for asignacion in evaluaciones_como_evaluado:
        evaluacion = asignacion.evaluacion
        
        # Contar evaluadores totales y que han completado
        total_evaluadores = AsignacionEvaluacion.objects.filter(
            evaluacion=evaluacion
        ).count()
        
        evaluadores_completados = AsignacionEvaluacion.objects.filter(
            evaluacion=evaluacion,
            completada=True
        ).count()
        
        resultado['como_evaluado'].append({
            'evaluacion_id': evaluacion.id,
            'tipo': evaluacion.tipo,
            'componente': {
                'id': evaluacion.componente.id,
                'nombre': evaluacion.componente.nombre
            },
            'fecha': evaluacion.fecha,
            'progreso': {
                'total_evaluadores': total_evaluadores,
                'evaluadores_completados': evaluadores_completados,
                'porcentaje': (evaluadores_completados / total_evaluadores * 100) if total_evaluadores > 0 else 0
            },
            'estado': 'completada' if evaluadores_completados == total_evaluadores else 'en_proceso'
        })
    
    return resultado

def obtener_usuarios_por_area(area_id=None):
    """
    Obtiene usuarios por área con información de perfil
    """
    try:
        if area_id:
            usuarios = User.objects.filter(
                perfil__area_id=area_id,
                is_active=True
            ).select_related('perfil', 'perfil__area')
        else:
            usuarios = User.objects.filter(
                is_active=True
            ).select_related('perfil', 'perfil__area')
        
        resultado = []
        for usuario in usuarios:
            perfil = getattr(usuario, 'perfil', None)
            if perfil:
                # Contar actividades pendientes
                actividades_pendientes = Actividad.objects.filter(
                    area_grupo=perfil.area,
                    componente__tipo__nombre='Desempeño Laboral',
                    usuarios_grupo=usuario
                ).count()
                
                # Contar evaluaciones 360 pendientes
                evaluaciones_360_pendientes = AsignacionEvaluacion.objects.filter(
                    evaluacion__area_grupo=perfil.area,
                    evaluacion__tipo='360',
                    evaluacion__componente__tipo__nombre='Evaluación 360',
                    usuario_evaluado=usuario,
                    completada=False
                ).count()
                
                resultado.append({
                    'id': usuario.id,
                    'nombre': f"{usuario.first_name} {usuario.last_name}" if usuario.first_name else usuario.username,
                    'username': usuario.username,
                    'email': usuario.email,
                    'rol': getattr(perfil, 'rol', None),
                    'cargo': perfil.cargo,
                    'actividades_pendientes': actividades_pendientes,
                    'evaluaciones_360_pendientes': evaluaciones_360_pendientes,
                    'total_pendientes': actividades_pendientes + evaluaciones_360_pendientes
                })
        
        return resultado
        
    except Exception as e:
        raise Exception(f"Error al obtener usuarios por área: {str(e)}")

# Servicios para el horario laboral - FUNCIÓN ELIMINADA
# Ahora se usa la nueva implementación en views.py

def calcular_desempeno_usuario(usuario_id, lider_id, area_id):
    """
    Calcula el desempeño de un usuario basado en evaluaciones 360 y actividades laborales
    Retorna el porcentaje total según los pesos de los tipos de componente
    """
    try:
        # ✅ 1. OBTENER PORCENTAJES DE TIPOS DE COMPONENTE
        tipos_componente = TipoComponente.objects.all()
        porcentajes = {}
        for tipo in tipos_componente:
            porcentajes[tipo.nombre] = float(tipo.porcentaje_total)
        
        print(f"🔍 Porcentajes de tipos de componente: {porcentajes}")
        
        # 🔍 DEBUG: Ver qué hay en las tablas principales
        print(f"🔍 DEBUG: Total AsignacionEvaluacion: {AsignacionEvaluacion.objects.count()}")
        print(f"🔍 DEBUG: Total EvaluacionActividad: {EvaluacionActividad.objects.count()}")
        print(f"🔍 DEBUG: Total RespuestaEvaluacion: {RespuestaEvaluacion.objects.count()}")
        
        # 🔍 DEBUG: Ver evaluaciones específicas del líder
        evaluaciones_lider = AsignacionEvaluacion.objects.filter(evaluador_id=lider_id)
        print(f"🔍 DEBUG: Evaluaciones donde el líder {lider_id} es evaluador: {evaluaciones_lider.count()}")
        if evaluaciones_lider.exists():
            for ev in evaluaciones_lider[:3]:  # Mostrar las primeras 3
                print(f"   - ID: {ev.id}, Usuario: {ev.usuario_evaluado_id}, Tipo: {ev.evaluacion.tipo}, Completada: {ev.completada}")
        
        # 🔍 DEBUG: Ver evaluaciones de actividades del líder
        evaluaciones_act_lider = EvaluacionActividad.objects.filter(asignacion__evaluador_id=lider_id)
        print(f"🔍 DEBUG: Evaluaciones de actividades donde el líder {lider_id} es evaluador: {evaluaciones_act_lider.count()}")
        if evaluaciones_act_lider.exists():
            for ev in evaluaciones_act_lider[:3]:  # Mostrar las primeras 3
                print(f"   - ID: {ev.id}, Usuario: {ev.asignacion.usuario_asignado_id}, Calificación: {ev.calificacion}")
        
        # ✅ 2. CALCULAR DESEMPEÑO EN EVALUACIONES 360/180
        desempeno_360 = 0
        promedio_ponderado_360 = 0
        try:
            # Buscar evaluaciones 360 y 180 completadas por este líder para este usuario
            print(f"🔍 Buscando evaluaciones 360/180 para usuario {usuario_id} evaluado por líder {lider_id}")
            evaluaciones_360 = AsignacionEvaluacion.objects.filter(
                evaluador_id=lider_id,
                usuario_evaluado_id=usuario_id,
                evaluacion__tipo__in=['360', '180'],  # Incluir tanto 360 como 180
                completada=True
            ).select_related('evaluacion__componente__tipo')
            
            print(f"🔍 Evaluaciones 360/180 encontradas: {evaluaciones_360.count()}")
            if evaluaciones_360.exists():
                print(f"🔍 Primera evaluación 360/180: {evaluaciones_360.first()}")
                total_peso = 0.0
                total_puntaje = 0.0
                
                for asignacion in evaluaciones_360:
                    # Obtener todas las respuestas para esta asignación
                    respuestas = RespuestaEvaluacion.objects.filter(
                        asignacion=asignacion
                    ).select_related('pregunta')
                    
                    for respuesta in respuestas:
                        if respuesta.pregunta and respuesta.pregunta.activo:
                            # Convertir respuesta booleana a valor numérico
                            valor_respuesta = 1 if respuesta.respuesta_booleana else 0
                            
                            # Obtener peso de la pregunta
                            peso_pregunta = float(respuesta.pregunta.peso) if respuesta.pregunta.peso else 0
                            
                            # Calcular contribución de esta pregunta (igual que en el endpoint dashboard)
                            total_peso += peso_pregunta
                            total_puntaje += float(valor_respuesta) * peso_pregunta
                
                if total_peso > 0:
                    # Calcular promedio ponderado de las respuestas (igual que en el endpoint dashboard)
                    promedio_ponderado_360 = float((total_puntaje / total_peso) * 100)
                    
                    # Aplicar porcentaje del tipo de componente
                    tipo_360 = next((t for t in tipos_componente if '360' in t.nombre), None)
                    if tipo_360:
                        peso_360 = float(tipo_360.porcentaje_total)
                        # NO aplicar el peso del componente aquí, solo devolver el porcentaje real (igual que en el endpoint dashboard)
                        desempeno_360 = float(promedio_ponderado_360)
                        print(f"🔍 Usuario {usuario_id} - Evaluación 360/180: Promedio ponderado: {promedio_ponderado_360:.2f}%, Peso componente: {peso_360}%, Desempeño: {desempeno_360:.2f}")
                        
        except Exception as e:
            print(f"⚠️ Error al calcular evaluación 360/180 para usuario {usuario_id}: {str(e)}")
            desempeno_360 = 0
        
        # ✅ 3. CALCULAR DESEMPEÑO EN ACTIVIDADES LABORALES
        desempeno_laboral = 0
        promedio_laboral = 0
        try:
            print(f"🔍 Buscando evaluaciones laborales para usuario {usuario_id} evaluado por líder {lider_id}")
            evaluaciones_laborales = EvaluacionActividad.objects.filter(
                asignacion__evaluador_id=lider_id,
                asignacion__usuario_asignado_id=usuario_id
            ).select_related('asignacion__actividad__componente__tipo')
            
            print(f"🔍 Evaluaciones laborales encontradas: {evaluaciones_laborales.count()}")
            if evaluaciones_laborales.exists():
                print(f"🔍 Primera evaluación laboral: {evaluaciones_laborales.first()}")
                # Calcular promedio de calificaciones laborales (escala 0-10)
                calificaciones_laborales = [float(e.calificacion) for e in evaluaciones_laborales]
                promedio_laboral = float(sum(calificaciones_laborales) / len(calificaciones_laborales))
                
                # Aplicar porcentaje del tipo de componente (buscar por nombre que contenga 'LABORAL')
                tipo_laboral = next((t for t in tipos_componente if 'LABORAL' in t.nombre.upper()), None)
                print(f"🔍 DEBUG: Tipo laboral encontrado: {tipo_laboral.nombre if tipo_laboral else 'NONE'}")
                if tipo_laboral:
                    peso_laboral = float(tipo_laboral.porcentaje_total)
                    # Convertir el promedio (0-10) a porcentaje (igual que en el endpoint dashboard)
                    desempeno_laboral = float((promedio_laboral / 10) * 100)
                    print(f"🔍 Usuario {usuario_id} - Actividades Laborales: Promedio: {promedio_laboral:.2f}/10, Peso componente: {peso_laboral}%, Desempeño: {desempeno_laboral:.2f}")
                else:
                    print(f"⚠️ No se encontró tipo de componente con 'LABORAL' en el nombre")
                    print(f"🔍 Tipos disponibles: {[t.nombre for t in tipos_componente]}")
                    
        except Exception as e:
            print(f"⚠️ Error al calcular actividades laborales para usuario {usuario_id}: {str(e)}")
            desempeno_laboral = 0
        
        # ✅ 4. CALCULAR DESEMPEÑO TOTAL (igual que en el endpoint dashboard)
        # Ahora desempeno_360 y desempeno_laboral son porcentajes reales (0-100%)
        # Convertir ambos a float para evitar errores de tipo
        desempeno_total = float(desempeno_360) + float(desempeno_laboral)
        
        # ✅ 5. CALCULAR PROMEDIO PONDERADO PARA EL ESTADO (más realista)
        # Usar los pesos de los tipos de componente para calcular el promedio ponderado
        tipo_360 = next((t for t in tipos_componente if '360' in t.nombre), None)
        tipo_laboral = next((t for t in tipos_componente if 'LABORAL' in t.nombre.upper()), None)
        
        promedio_ponderado_estado = 0
        if tipo_360 and tipo_laboral:
            peso_360 = float(tipo_360.porcentaje_total) / 100  # Convertir a decimal (20% = 0.2)
            peso_laboral = float(tipo_laboral.porcentaje_total) / 100  # Convertir a decimal (60% = 0.6)
            
            # Calcular promedio ponderado: (66.67% × 0.2) + (83.33% × 0.6)
            # Convertir ambos valores a float para evitar errores de tipo
            promedio_ponderado_estado = (float(desempeno_360) * peso_360) + (float(desempeno_laboral) * peso_laboral)
        
        # ✅ 6. CALCULAR PORCENTAJE FINAL (promedio ponderado)
        porcentaje_final = promedio_ponderado_estado
        
        print(f"🔍 Usuario {usuario_id} - Resumen:")
        print(f"   - Desempeño 360: {desempeno_360:.2f}%")
        print(f"   - Desempeño Laboral: {desempeno_laboral:.2f}%")
        print(f"   - Desempeño Total: {desempeno_total:.2f}%")
        print(f"   - Promedio Ponderado (Estado): {promedio_ponderado_estado:.2f}%")
        print(f"   - Porcentaje Final: {porcentaje_final:.2f}%")
        
        return {
            'porcentaje_total': round(porcentaje_final, 2),
            'desempeno_360': round(desempeno_360, 2),
            'desempeno_laboral': round(desempeno_laboral, 2),
            'promedio_360_180': round(promedio_ponderado_360, 2),
            'promedio_laboral': round(promedio_laboral, 2),
            'evaluaciones_360_180_count': evaluaciones_360.count() if 'evaluaciones_360' in locals() else 0,
            'evaluaciones_laborales_count': evaluaciones_laborales.count() if 'evaluaciones_laborales' in locals() else 0,
            'estado': 'excelente' if promedio_ponderado_estado >= 85 else 'bueno' if promedio_ponderado_estado >= 70 else 'regular' if promedio_ponderado_estado >= 60 else 'necesita_mejora'
        }
        
    except Exception as e:
        print(f"❌ Error calculando desempeño del usuario {usuario_id}: {str(e)}")
        return {
            'porcentaje_total': 0,
            'desempeno_360': 0,
            'desempeno_laboral': 0,
            'promedio_360_180': 0,
            'promedio_laboral': 0,
            'evaluaciones_360_180_count': 0,
            'evaluaciones_laborales_count': 0,
            'estado': 'sin_evaluar',
            'error': str(e)
        }

def validar_contrato_para_evaluacion(usuario_id, area_id):
    """
    Valida si un usuario tiene contrato vigente para poder ser evaluado
    """
    try:
        contrato = ContratoUsuario.objects.filter(
            usuario_id=usuario_id,
            area_id=area_id,
            activo=True
        ).first()
        
        if not contrato:
            return {
                'valido': False,
                'mensaje': 'El usuario no tiene contrato vigente en esta área',
                'contrato': None
            }
        
        if not contrato.es_vigente:
            return {
                'valido': False,
                'mensaje': f'El contrato del usuario vence en {contrato.dias_restantes} días',
                'contrato': {
                    'id': contrato.id,
                    'dias_restantes': contrato.dias_restantes,
                    'fecha_fin': contrato.fecha_fin
                }
            }
        
        return {
            'valido': True,
            'mensaje': 'Usuario válido para evaluación',
            'contrato': {
                'id': contrato.id,
                'cargo': contrato.cargo,
                'area': contrato.area.nombre if contrato.area else 'Sin área',
                'tipo_contrato': contrato.tipo_contrato
            }
        }
        
    except Exception as e:
        print(f"❌ Error validando contrato para usuario {usuario_id}: {str(e)}")
        return {
            'valido': False,
            'mensaje': f'Error validando contrato: {str(e)}',
            'contrato': None
        }

def obtener_dashboard_contratos_urgentes(usuario_id=None, area_id=None, limit=10):
    """
    Servicio optimizado para el dashboard que obtiene contratos urgentes
    con información COMPLETA de la persona desde login_customuser
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Base queryset con JOIN a la tabla de usuarios
        queryset = ContratoUsuario.objects.filter(
            activo=True
        ).select_related('area')
        
        # Filtros opcionales
        if area_id:
            queryset = queryset.filter(area_id=area_id)
        if usuario_id:
            queryset = queryset.filter(usuario_id=usuario_id)
        
        contratos_dashboard = []
        hoy = timezone.now().date()
        
        for contrato in queryset:
            if contrato.fecha_fin:
                dias_restantes = (contrato.fecha_fin - hoy).days
                
                # Solo incluir contratos urgentes (no vigentes)
                if dias_restantes <= 90:
                    # Determinar urgencia
                    if dias_restantes <= 0:
                        urgencia = 'critica'
                        prioridad = 1
                    elif dias_restantes <= 30:
                        urgencia = 'alta'
                        prioridad = 2
                    elif dias_restantes <= 60:
                        urgencia = 'media'
                        prioridad = 3
                    else:
                        urgencia = 'baja'
                        prioridad = 4
                    
                    # ✅ OBTENER INFORMACIÓN COMPLETA DE LA PERSONA
                    try:
                        user = User.objects.get(id=contrato.usuario_id)
                        nombre_usuario = user.get_full_name() or f"{user.first_name} {user.last_name}".strip() or user.nombre or f"Usuario {contrato.usuario_id}"
                        email_usuario = user.email
                        cargo_usuario = user.cargo or contrato.cargo
                        username_usuario = user.username
                        nombre_completo = f"{user.first_name} {user.last_name}".strip() or user.nombre or nombre_usuario
                    except User.DoesNotExist:
                        nombre_usuario = f"Usuario {contrato.usuario_id}"
                        email_usuario = None
                        cargo_usuario = contrato.cargo
                        username_usuario = None
                        nombre_completo = nombre_usuario
                    
                    # Obtener información del área
                    area_nombre = contrato.area.nombre if contrato.area else 'Sin área'
                    area_color = contrato.area.color if hasattr(contrato.area, 'color') and contrato.area.color else '#2196f3'
                    
                    contratos_dashboard.append({
                        # ✅ DATOS DEL CONTRATO
                        'id': contrato.id,
                        'usuario_id': contrato.usuario_id,
                        'identificacion': contrato.identificacion,
                        'tipo_contrato': contrato.tipo_contrato,
                        'fecha_inicio': contrato.fecha_inicio,
                        'fecha_fin': contrato.fecha_fin,
                        'cargo': cargo_usuario,
                        'area_id': contrato.area.id if contrato.area else None,
                        'area_nombre': area_nombre,
                        'area_color': area_color,
                        'dias_restantes': max(0, dias_restantes),
                        'urgencia': urgencia,
                        'prioridad': prioridad,
                        'salario': float(contrato.salario) if contrato.salario else None,
                        'activo': contrato.activo,
                        'created_at': contrato.created_at,
                        'updated_at': contrato.updated_at,
                        
                        # ✅ DATOS COMPLETOS DE LA PERSONA
                        'nombre_usuario': nombre_usuario,
                        'nombre_completo': nombre_completo,
                        'email_usuario': email_usuario,
                        'username_usuario': username_usuario,
                        'first_name': getattr(user, 'first_name', '') if 'user' in locals() else '',
                        'last_name': getattr(user, 'last_name', '') if 'user' in locals() else '',
                        'nombre': getattr(user, 'nombre', '') if 'user' in locals() else '',
                        'cargo_usuario': cargo_usuario,
                        'is_active': getattr(user, 'is_active', True) if 'user' in locals() else True,
                        'date_joined': getattr(user, 'date_joined', None) if 'user' in locals() else None
                    })
        
        # Ordenar por prioridad y días restantes
        contratos_dashboard.sort(key=lambda x: (x['prioridad'], x['dias_restantes']))
        
        # Limitar resultados si se especifica
        if limit:
            contratos_dashboard = contratos_dashboard[:limit]
        
        return {
            'success': True,
            'contratos': contratos_dashboard,
            'total': len(contratos_dashboard),
            'filtros': {
                'usuario_id': usuario_id,
                'area_id': area_id,
                'limit': limit
            },
            'resumen': {
                'criticos': len([c for c in contratos_dashboard if c['urgencia'] == 'critica']),
                'altos': len([c for c in contratos_dashboard if c['urgencia'] == 'alta']),
                'medios': len([c for c in contratos_dashboard if c['urgencia'] == 'media']),
                'bajos': len([c for c in contratos_dashboard if c['urgencia'] == 'baja'])
            }
        }
        
    except Exception as e:
        print(f"❌ Error obteniendo dashboard de contratos: {str(e)}")
        return {
            'success': False,
            'contratos': [],
            'total': 0,
            'error': str(e)
        }

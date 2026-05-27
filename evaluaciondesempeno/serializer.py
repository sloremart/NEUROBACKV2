from rest_framework import serializers
from .models import *
from django.contrib.auth import get_user_model

User = get_user_model()

class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = '__all__'

class TipoComponenteSerializer(serializers.ModelSerializer):
    class Meta:
        model = TipoComponente
        fields = '__all__'

class ComponenteSerializer(serializers.ModelSerializer):
    tipo_nombre = serializers.CharField(source='tipo.nombre', read_only=True)
    area_nombre = serializers.CharField(source='area.nombre', read_only=True)

    class Meta:
        model = Componente
        fields = ['id', 'tipo', 'area', 'tipo_nombre', 'area_nombre', 'descripcion', 'es_360']



User = get_user_model()


class ActividadSerializer(serializers.ModelSerializer):
    usuario_asignado_nombre = serializers.SerializerMethodField()
    usuarios_grupo_nombres = serializers.SerializerMethodField()
    tipo_nombre = serializers.SerializerMethodField()
    area_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Actividad
        fields = [
            'id', 'componente', 'nombre', 'descripcion', 'porcentaje',
            'usuario_asignado', 'usuarios_grupo', 'area_grupo',
            'usuario_asignado_nombre', 'usuarios_grupo_nombres',
            'tipo_nombre', 'area_nombre'
        ]

    def get_usuario_asignado_nombre(self, obj):
        if obj.usuario_asignado:
            return f"{obj.usuario_asignado.first_name} {obj.usuario_asignado.last_name}"
        return None

    def get_usuarios_grupo_nombres(self, obj):
        return [f"{u.first_name} {u.last_name}" for u in obj.usuarios_grupo.all()]

    def get_tipo_nombre(self, obj):
        return obj.componente.tipo.nombre if obj.componente and obj.componente.tipo else None

    def get_area_nombre(self, obj):
        return obj.componente.area.nombre if obj.componente and obj.componente.area else None

    def validate(self, data):
        usuario = data.get('usuario_asignado')
        area = data.get('area_grupo')

        if usuario and area:
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Solo puede asignar la actividad a un usuario individual o a un grupo por área, no ambos."
                ]
            })

        if not usuario and not area:
            raise serializers.ValidationError({
                "non_field_errors": [
                    "Debe asignar la actividad a un usuario individual o a un área con usuarios activos."
                ]
            })

        return data

    def create(self, validated_data):
        # ✅ CORREGIR: NO remover area_grupo, solo obtenerlo para lógica adicional
        area = validated_data.get('area_grupo')
        usuario = validated_data.get('usuario_asignado')

        # ✅ CORREGIR: Crear actividad CON todos los campos, incluyendo area_grupo
        actividad = Actividad.objects.create(**validated_data)

        if not usuario and area:
            # CORREGIDO: Usar ContratoUsuario en lugar de User.perfil
            from .models import ContratoUsuario
            from django.utils import timezone
            
            # Filtrar por campos reales de la base de datos
            hoy = timezone.now().date()
            usuarios_contratos = ContratoUsuario.objects.filter(
                area=area, 
                activo=True,
                fecha_inicio__lte=hoy  # Contrato iniciado
            ).filter(
                # Para contratos de término fijo, verificar que no hayan expirado
                models.Q(tipo_contrato='INDEFINIDO') |  # Contratos indefinidos
                models.Q(fecha_fin__isnull=True) |      # Sin fecha de fin
                models.Q(fecha_fin__gte=hoy)            # Fecha de fin en el futuro
            )
            
            if not usuarios_contratos.exists():
                raise serializers.ValidationError({
                    "non_field_errors": [
                        "El área seleccionada no tiene usuarios activos."
                    ]
                })
            
            # Obtener los usuarios del sistema desde los contratos
            usuarios_ids = usuarios_contratos.values_list('usuario_id', flat=True)
            usuarios = User.objects.filter(id__in=usuarios_ids, is_active=True)
            
            if not usuarios.exists():
                raise serializers.ValidationError({
                    "non_field_errors": [
                        "El área seleccionada no tiene usuarios activos."
                    ]
                })
            
            actividad.usuarios_grupo.set(usuarios)

        return actividad

    def update(self, instance, validated_data):
        # ✅ CORREGIR: NO remover area_grupo, solo obtenerlo para lógica adicional
        area = validated_data.get('area_grupo')
        usuario = validated_data.get('usuario_asignado')

        # ✅ CORREGIR: Actualizar CON todos los campos, incluyendo area_grupo
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if not usuario and area:
            # CORREGIDO: Usar ContratoUsuario en lugar de User.perfil
            from .models import ContratoUsuario
            from django.utils import timezone
            
            # Filtrar por campos reales de la base de datos
            hoy = timezone.now().date()
            usuarios_contratos = ContratoUsuario.objects.filter(
                area=area, 
                activo=True,
                fecha_inicio__lte=hoy  # Contrato iniciado
            ).filter(
                # Para contratos de término fijo, verificar que no hayan expirado
                models.Q(tipo_contrato='INDEFINIDO') |  # Contratos indefinidos
                models.Q(fecha_fin__isnull=True) |      # Sin fecha de fin
                models.Q(fecha_fin__gte=hoy)            # Fecha de fin en el futuro
            )
            
            # Obtener los usuarios del sistema desde los contratos
            usuarios_ids = usuarios_contratos.values_list('usuario_id', flat=True)
            usuarios = User.objects.filter(id__in=usuarios_ids, is_active=True)
            
            instance.usuarios_grupo.set(usuarios)

        return instance


    
class EvaluacionSerializer(serializers.ModelSerializer):
    usuario_evaluado_nombre = serializers.SerializerMethodField()
    evaluador_nombre = serializers.SerializerMethodField()
    componente_nombre = serializers.SerializerMethodField()
    area_grupo_nombre = serializers.SerializerMethodField()

    class Meta:
        model = Evaluacion
        fields = '__all__'
        extra_kwargs = {
            'usuario_evaluado': {'required': False, 'allow_null': True}
        }

    def get_usuario_evaluado_nombre(self, obj):
        if obj.usuario_evaluado:
            return f"{obj.usuario_evaluado.first_name} {obj.usuario_evaluado.last_name}"
        return None

    def get_evaluador_nombre(self, obj):
        if obj.evaluador:
            return f"{obj.evaluador.first_name} {obj.evaluador.last_name}"
        return None

    def get_componente_nombre(self, obj):
        return str(obj.componente) if obj.componente else None

    def get_area_grupo_nombre(self, obj):
        return obj.area_grupo.nombre if hasattr(obj, 'area_grupo') and obj.area_grupo else None

    def validate(self, data):
        # Validación básica
        if self.context.get('request').method == 'POST' and not data.get('usuario_evaluado') and not data.get('area_grupo'):
            raise serializers.ValidationError({
                'usuario_evaluado': 'Debe asignar un usuario o un área.'
            })
        
        # ✅ MODIFICADO: Validación de duplicados para evaluaciones individuales
        # Ahora permite múltiples evaluaciones del mismo tipo y componente para el mismo usuario
        # siempre que tengan diferentes evaluadores
        if data.get('usuario_evaluado') and data.get('componente') and data.get('tipo') and data.get('evaluador'):
            evaluacion_existente = Evaluacion.objects.filter(
                usuario_evaluado=data['usuario_evaluado'],
                componente=data['componente'],
                tipo=data['tipo'],
                evaluador=data['evaluador']  # ✅ AGREGADO: Solo validar duplicados si es el mismo evaluador
            ).first()
            
            # Si estamos actualizando, excluir la evaluación actual
            if evaluacion_existente and (not self.instance or evaluacion_existente.id != self.instance.id):
                raise serializers.ValidationError({
                    'non_field_errors': [
                        f"Ya existe una evaluación {data['tipo']} del componente "
                        f"'{data['componente']}' para este usuario con el mismo evaluador."
                    ]
                })
        
        return data

    def create(self, validated_data):
        # Si es una evaluación individual y no tiene area_grupo, asignar automáticamente
        if validated_data.get('usuario_evaluado') and not validated_data.get('area_grupo'):
            usuario_evaluado = validated_data['usuario_evaluado']
            perfil = getattr(usuario_evaluado, 'perfil', None)
            if perfil and perfil.area:
                validated_data['area_grupo'] = perfil.area
        
        return super().create(validated_data)

class EvaluacionDashboardSerializer(serializers.ModelSerializer):
    """Serializer para el dashboard que incluye información de asignaciones completadas"""
    usuario_evaluado_nombre = serializers.SerializerMethodField()
    evaluador_nombre = serializers.SerializerMethodField()
    componente_nombre = serializers.SerializerMethodField()
    area_grupo_nombre = serializers.SerializerMethodField()
    completada = serializers.SerializerMethodField()
    total_asignaciones = serializers.SerializerMethodField()
    asignaciones_completadas = serializers.SerializerMethodField()
    porcentaje_completado = serializers.SerializerMethodField()

    class Meta:
        model = Evaluacion
        fields = '__all__'
        extra_kwargs = {
            'usuario_evaluado': {'required': False, 'allow_null': True}
        }

    def get_completada(self, obj):
        """Una evaluación está completada si todas sus asignaciones están completadas"""
        total_asignaciones = obj.asignaciones.count()
        if total_asignaciones == 0:
            return False
        asignaciones_completadas = obj.asignaciones.filter(completada=True).count()
        return asignaciones_completadas == total_asignaciones

    def get_total_asignaciones(self, obj):
        return obj.asignaciones.count()

    def get_asignaciones_completadas(self, obj):
        return obj.asignaciones.filter(completada=True).count()

    def get_porcentaje_completado(self, obj):
        total = obj.asignaciones.count()
        if total == 0:
            return 0
        completadas = obj.asignaciones.filter(completada=True).count()
        return round((completadas / total) * 100, 2)

    def get_usuario_evaluado_nombre(self, obj):
        if obj.usuario_evaluado:
            return f"{obj.usuario_evaluado.first_name} {obj.usuario_evaluado.last_name}"
        return None

    def get_evaluador_nombre(self, obj):
        if obj.evaluador:
            return f"{obj.evaluador.first_name} {obj.evaluador.last_name}"
        return None

    def get_componente_nombre(self, obj):
        return str(obj.componente) if obj.componente else None

    def get_area_grupo_nombre(self, obj):
        return obj.area_grupo.nombre if hasattr(obj, 'area_grupo') and obj.area_grupo else None

    def validate(self, data):
        # Validación básica
        if self.context.get('request').method == 'POST' and not data.get('usuario_evaluado') and not data.get('area_grupo'):
            raise serializers.ValidationError({
                'usuario_evaluado': 'Debe asignar un usuario o un área.'
            })
        
        # Validación de duplicados para evaluaciones individuales
        if data.get('usuario_evaluado') and data.get('componente') and data.get('tipo'):
            evaluacion_existente = Evaluacion.objects.filter(
                usuario_evaluado=data['usuario_evaluado'],
                componente=data['componente'],
                tipo=data['tipo']
            ).first()
            
            # Si estamos actualizando, excluir la evaluación actual
            if evaluacion_existente and (not self.instance or evaluacion_existente.id != self.instance.id):
                raise serializers.ValidationError({
                    'non_field_errors': [
                        f"Ya existe una evaluación {data['tipo']} del componente "
                        f"'{data['componente']}' para este usuario."
                    ]
                })
        
        return data

    def create(self, validated_data):
        # Si es una evaluación individual y no tiene area_grupo, asignar automáticamente
        if validated_data.get('usuario_evaluado') and not validated_data.get('area_grupo'):
            usuario_evaluado = validated_data['usuario_evaluado']
            perfil = getattr(usuario_evaluado, 'perfil', None)
            if perfil and perfil.area:
                validated_data['area_grupo'] = perfil.area
        
        return super().create(validated_data)

class PreguntaEvaluacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreguntaEvaluacion
        fields = '__all__'


User = get_user_model()

class PerfilUsuarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = PerfilUsuario
        fields = ['area', 'rol', 'cargo', 'es_evaluador', 'es_lider', 'tipo_evaluacion']

class UserConPerfilSerializer(serializers.ModelSerializer):
    perfil = PerfilUsuarioSerializer()

    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'username', 'email', 'perfil']


class AsignacionEvaluacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AsignacionEvaluacion
        fields = '__all__'

# Nuevos serializers para el sistema mejorado de preguntas 360

class CategoriaPreguntaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaPregunta
        fields = '__all__'

class EscalaRespuestaSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscalaRespuesta
        fields = '__all__'

class OpcionRespuestaSerializer(serializers.ModelSerializer):
    class Meta:
        model = OpcionRespuesta
        fields = '__all__'

class PreguntaComponente360Serializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    escalas = EscalaRespuestaSerializer(many=True, read_only=True)
    opciones = OpcionRespuestaSerializer(many=True, read_only=True)
    
    class Meta:
        model = PreguntaComponente360
        fields = '__all__'

    def validate(self, data):
        tipo = data.get('tipo')
        if tipo == 'LIKERT' and not data.get('escalas'):
            raise serializers.ValidationError({
                'escalas': 'Las preguntas tipo Likert deben tener escalas de respuesta.'
            })
        elif tipo == 'MULTIPLE' and not data.get('opciones'):
            raise serializers.ValidationError({
                'opciones': 'Las preguntas tipo múltiple deben tener opciones de respuesta.'
            })
        return data

class RespuestaEvaluacionSerializer(serializers.ModelSerializer):
    evaluador_nombre = serializers.CharField(source='asignacion.evaluador.get_full_name', read_only=True)
    pregunta_texto = serializers.CharField(source='pregunta.texto', read_only=True)
    tipo_pregunta = serializers.CharField(source='pregunta.tipo', read_only=True)
    
    class Meta:
        model = RespuestaEvaluacion
        fields = '__all__'

    def validate(self, data):
        pregunta = data.get('pregunta')
        tipo = pregunta.tipo if pregunta else None
        
        if tipo == 'LIKERT' and not data.get('escala_seleccionada'):
            raise serializers.ValidationError({
                'escala_seleccionada': 'Debe seleccionar una escala para preguntas tipo Likert.'
            })
        elif tipo == 'MULTIPLE' and not data.get('opcion_seleccionada'):
            raise serializers.ValidationError({
                'opcion_seleccionada': 'Debe seleccionar una opción para preguntas tipo múltiple.'
            })
        elif tipo == 'BOOLEANA' and data.get('respuesta_booleana') is None:
            raise serializers.ValidationError({
                'respuesta_booleana': 'Debe responder Sí o No para preguntas tipo booleana.'
            })
        elif tipo == 'NUMERICA' and data.get('respuesta_numerica') is None:
            raise serializers.ValidationError({
                'respuesta_numerica': 'Debe proporcionar un valor numérico.'
            })
        elif tipo == 'ABIERTA' and not data.get('respuesta_texto'):
            raise serializers.ValidationError({
                'respuesta_texto': 'Debe proporcionar una respuesta de texto.'
            })
        
        return data

class PlantillaEvaluacionSerializer(serializers.ModelSerializer):
    componente_nombre = serializers.CharField(source='componente.nombre', read_only=True)
    total_preguntas = serializers.SerializerMethodField()
    
    class Meta:
        model = PlantillaEvaluacion
        fields = '__all__'
    
    def get_total_preguntas(self, obj):
        return obj.preguntas.count()

class PreguntaPlantillaSerializer(serializers.ModelSerializer):
    pregunta_detalle = PreguntaComponente360Serializer(source='pregunta', read_only=True)
    
    class Meta:
        model = PreguntaPlantilla
        fields = '__all__'

# Serializer para crear preguntas con escalas y opciones
class PreguntaCompletaSerializer(serializers.ModelSerializer):
    escalas = EscalaRespuestaSerializer(many=True, required=False)
    opciones = OpcionRespuestaSerializer(many=True, required=False)
    
    class Meta:
        model = PreguntaComponente360
        fields = '__all__'
    
    def create(self, validated_data):
        escalas_data = validated_data.pop('escalas', [])
        opciones_data = validated_data.pop('opciones', [])
        
        pregunta = PreguntaComponente360.objects.create(**validated_data)
        
        # Crear escalas si existen
        for escala_data in escalas_data:
            EscalaRespuesta.objects.create(pregunta=pregunta, **escala_data)
        
        # Crear opciones si existen
        for opcion_data in opciones_data:
            OpcionRespuesta.objects.create(pregunta=pregunta, **opcion_data)
        
        return pregunta

    def update(self, instance, validated_data):
        escalas_data = validated_data.pop('escalas', [])
        opciones_data = validated_data.pop('opciones', [])
        
        # Actualizar pregunta
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Actualizar escalas
        if escalas_data:
            instance.escalas.all().delete()
            for escala_data in escalas_data:
                EscalaRespuesta.objects.create(pregunta=instance, **escala_data)
        
        # Actualizar opciones
        if opciones_data:
            instance.opciones.all().delete()
            for opcion_data in opciones_data:
                OpcionRespuesta.objects.create(pregunta=instance, **opcion_data)
        
        return instance

# Serializer para asignaciones de actividades laborales
class AsignacionActividadSerializer(serializers.ModelSerializer):
    actividad_nombre = serializers.CharField(source='actividad.nombre', read_only=True)
    usuario_asignado_nombre = serializers.CharField(source='usuario_asignado.get_full_name', read_only=True)
    evaluador_nombre = serializers.CharField(source='evaluador.get_full_name', read_only=True)
    componente_nombre = serializers.CharField(source='actividad.componente.nombre', read_only=True)
    area_nombre = serializers.CharField(source='actividad.area_grupo.nombre', read_only=True)
    
    class Meta:
        model = AsignacionActividad
        fields = '__all__'
        read_only_fields = ['fecha_asignacion', 'fecha_completada']
    
    def validate(self, data):
        # Validar que el evaluador no sea el mismo usuario asignado
        if data.get('evaluador') == data.get('usuario_asignado'):
            raise serializers.ValidationError({
                'evaluador': 'El evaluador no puede ser el mismo usuario asignado'
            })
        return data

# Serializer para evaluaciones de actividades laborales
class EvaluacionActividadSerializer(serializers.ModelSerializer):
    evaluador_nombre = serializers.CharField(source='asignacion.evaluador.get_full_name', read_only=True)
    actividad_nombre = serializers.CharField(source='asignacion.actividad.nombre', read_only=True)
    usuario_evaluado_nombre = serializers.CharField(source='asignacion.usuario_asignado.get_full_name', read_only=True)
    componente_nombre = serializers.CharField(source='asignacion.actividad.componente.nombre', read_only=True)
    area_nombre = serializers.CharField(source='asignacion.actividad.area_grupo.nombre', read_only=True)
    
    class Meta:
        model = EvaluacionActividad
        fields = '__all__'
        read_only_fields = ['fecha_evaluacion']
    
    def validate_calificacion(self, value):
        if value < 0 or value > 10:
            raise serializers.ValidationError("La calificación debe estar entre 0.0 y 10.0")
        return value

# NUEVOS SERIALIZERS PARA ACTIVIDADES DE DESEMPEÑO

class LiderActividadSerializer(serializers.ModelSerializer):
    """Serializer para líderes de actividades de desempeño"""
    lider_nombre = serializers.SerializerMethodField()
    area_nombre = serializers.CharField(source='area.nombre', read_only=True)
    es_vigente = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = LiderActividad
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
    
    def validate(self, data):
        """Validar que no haya solapamiento de fechas para el mismo área y tipo"""
        from .models import LiderActividad
        
        area = data.get('area')
        tipo_actividad = data.get('tipo_actividad')
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        
        # Buscar líderes existentes que puedan solaparse
        lideres_existentes = LiderActividad.objects.filter(
            area=area,
            tipo_actividad=tipo_actividad,
            activo=True
        ).exclude(pk=self.instance.pk if self.instance else None)
        
        for lider_existente in lideres_existentes:
            # Verificar solapamiento de fechas
            if lider_existente.fecha_fin is None:  # Líder vigente sin fecha fin
                if fecha_fin is None or fecha_fin > lider_existente.fecha_inicio:
                    raise serializers.ValidationError(
                        f"Ya existe un líder vigente para {area.nombre} en {tipo_actividad} "
                        f"desde {lider_existente.fecha_inicio}"
                    )
            elif fecha_fin is None:  # Nuevo líder sin fecha fin
                if fecha_inicio < lider_existente.fecha_fin:
                    raise serializers.ValidationError(
                        f"El nuevo líder se solapa con el líder existente hasta {lider_existente.fecha_fin}"
                    )
            else:  # Ambos con fechas fin
                if (fecha_inicio < lider_existente.fecha_fin and 
                    fecha_fin > lider_existente.fecha_inicio):
                    raise serializers.ValidationError(
                        f"Las fechas se solapan con el líder existente"
                    )
        
        return data
    
    def get_lider_nombre(self, obj):
        """Obtener el nombre del líder desde el modelo User"""
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=obj.lider_id)
            return user.get_full_name() or f"Usuario {obj.lider_id}"
        except User.DoesNotExist:
            return f"Usuario {obj.lider_id}"

class ContratoUsuarioSerializer(serializers.ModelSerializer):
    """Serializer para contratos de usuarios"""
    usuario_nombre = serializers.SerializerMethodField()
    area_nombre = serializers.CharField(source='area.nombre', read_only=True)
    es_vigente = serializers.BooleanField(read_only=True)
    dias_restantes = serializers.IntegerField(read_only=True)
    
    # Serializar fechas en formato ISO explícitamente
    fecha_inicio = serializers.DateField(format='%Y-%m-%d')
    fecha_fin = serializers.DateField(format='%Y-%m-%d', allow_null=True)
    
    class Meta:
        model = ContratoUsuario
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
    
    def validate(self, data):
        """Validar fechas del contrato"""
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        tipo_contrato = data.get('tipo_contrato')
        
        # Para contratos de término fijo, la fecha fin es obligatoria
        if tipo_contrato != 'INDEFINIDO' and not fecha_fin:
            raise serializers.ValidationError(
                f"Los contratos de {tipo_contrato} deben tener fecha de fin"
            )
        
        # Para contratos indefinidos, no debe tener fecha fin
        if tipo_contrato == 'INDEFINIDO' and fecha_fin:
            raise serializers.ValidationError(
                "Los contratos indefinidos no deben tener fecha de fin"
            )
        
        # Validar que fecha_inicio no sea en el futuro
        from django.utils import timezone
        hoy = timezone.now().date()
        if fecha_inicio > hoy:
            raise serializers.ValidationError(
                "La fecha de inicio no puede ser en el futuro"
            )
        
        # Validar que fecha_fin sea posterior a fecha_inicio
        if fecha_fin and fecha_fin <= fecha_inicio:
            raise serializers.ValidationError(
                "La fecha de fin debe ser posterior a la fecha de inicio"
            )
        
        return data
    
    def get_usuario_nombre(self, obj):
        """Obtener el nombre del usuario desde el modelo User"""
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=obj.usuario_id)
            return user.get_full_name() or f"Usuario {obj.usuario_id}"
        except User.DoesNotExist:
            return f"Usuario {obj.usuario_id}"

# Serializer para asignaciones de actividades con nueva estructura
class AsignacionActividadCompletaSerializer(serializers.ModelSerializer):
    """Serializer completo para asignaciones de actividades con información del contrato"""
    actividad_nombre = serializers.CharField(source='actividad.nombre', read_only=True)
    usuario_asignado_nombre = serializers.CharField(source='usuario_asignado.get_full_name', read_only=True)
    evaluador_nombre = serializers.CharField(source='evaluador.get_full_name', read_only=True)
    componente_nombre = serializers.CharField(source='actividad.componente.nombre', read_only=True)
    area_nombre = serializers.CharField(source='actividad.area_grupo.nombre', read_only=True)
    contrato_info = ContratoUsuarioSerializer(source='contrato', read_only=True)
    
    class Meta:
        model = 'AsignacionActividad'
        fields = '__all__'
        read_only_fields = ['fecha_asignacion', 'fecha_completada']
    
    def validate(self, data):
        """Validar que el evaluador sea líder vigente del área"""
        from .models import LiderActividad
        
        # Si se proporciona contrato, validar que el evaluador sea líder vigente
        if data.get('contrato'):
            lider_vigente = LiderActividad.objects.filter(
                area=data['contrato'].area,
                tipo_actividad='FUNCIONES_CONTRATO',
                activo=True,
                fecha_inicio__lte=timezone.now().date(),
                fecha_fin__isnull=True
            ).first()
            
            if not lider_vigente or lider_vigente.lider != data.get('evaluador'):
                raise serializers.ValidationError({
                    'evaluador': 'El evaluador debe ser el líder vigente del área del contrato'
                })
        
        return data

class HorarioLaboralSerializer(serializers.ModelSerializer):
    """Serializer para registros de horario laboral"""
    
    class Meta:
        model = HorarioLaboral
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
        # ✅ Hacer campos de hora opcionales para registros consolidados
        extra_kwargs = {
            'hora_entrada_manana': {'required': False, 'allow_null': True},
            'hora_salida_almuerzo': {'required': False, 'allow_null': True},
            'hora_entrada_almuerzo': {'required': False, 'allow_null': True},
            'hora_salida_final': {'required': False, 'allow_null': True},
        }
    
    def validate(self, data):
        """Validar datos del horario laboral"""
        # Validar que la fecha no sea en el futuro
        from django.utils import timezone
        hoy = timezone.now().date()
        if data.get('fecha') > hoy:
            raise serializers.ValidationError(
                "La fecha del registro no puede ser en el futuro"
            )
        
        # ✅ VALIDACIÓN CONDICIONAL: Solo validar horas si se proporcionan
        # Para registros consolidados por persona, estos campos pueden ser null
        if (data.get('hora_salida_almuerzo') and data.get('hora_entrada_almuerzo') and
            data['hora_entrada_almuerzo'] <= data['hora_salida_almuerzo']):
            raise serializers.ValidationError(
                "La hora de entrada del almuerzo debe ser posterior a la hora de salida"
            )
        
        # 🎯 VALIDAR PERÍODO DE EVALUACIÓN
        fecha_inicio = data.get('fecha_inicio_periodo')
        fecha_fin = data.get('fecha_fin_periodo')
        
        if fecha_inicio and fecha_fin:
            if fecha_inicio > fecha_fin:
                raise serializers.ValidationError(
                    "La fecha de inicio del período no puede ser posterior a la fecha de fin"
                )
            
            if fecha_fin > hoy:
                raise serializers.ValidationError(
                    "La fecha de fin del período no puede ser en el futuro"
                )
        
        return data
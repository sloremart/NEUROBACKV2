from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

User = get_user_model()

class Area(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre

class TipoComponente(models.Model):
    nombre = models.CharField(max_length=100, unique=True)  
    porcentaje_total = models.DecimalField(max_digits=5, decimal_places=2)  
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.porcentaje_total}%)"

class Componente(models.Model):
    nombre = models.CharField(max_length=100, null=True, blank=True)
    descripcion = models.TextField(blank=True, null=True)
    tipo = models.ForeignKey(TipoComponente, on_delete=models.CASCADE, related_name='componentes')
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='componentes')
    es_360 = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        self.nombre = f"{self.tipo.nombre} - {self.area.nombre}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nombre

class Actividad(models.Model):
    componente = models.ForeignKey(Componente, on_delete=models.CASCADE, related_name='actividades')
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, null=True)
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2)

    usuario_asignado = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='actividad_individual', null=True, blank=True
    )
    usuarios_grupo = models.ManyToManyField(
        User, blank=True, related_name='actividades_grupales'
    )

 
    area_grupo = models.ForeignKey(
        Area, on_delete=models.SET_NULL, null=True, blank=True, related_name='actividades_area'
    )

    def __str__(self):
        destino = self.usuario_asignado if self.usuario_asignado else "grupo"
        return f"{self.nombre} - {destino}"
    
    def asignar_lider_automatico(self):
        """Asigna automáticamente el líder vigente del área para actividades"""
        if not self.area_grupo:
            return None
            
        from .models import LiderActividad
        
        lider_vigente = LiderActividad.objects.filter(
            area=self.area_grupo,
            tipo_actividad='FUNCIONES_CONTRATO',
            activo=True,
            fecha_inicio__lte=timezone.now().date(),
            fecha_fin__isnull=True
        ).first()
        
        if lider_vigente:
            return lider_vigente.lider
        else:
            raise ValidationError(f"No hay líder vigente para el área {self.area_grupo.nombre}")
    
    def asignar_usuarios_automaticamente(self, usuarios_ids, fecha_limite=None):
        """Asigna usuarios y crea AsignacionActividad automáticamente con líder vigente"""
        if not self.area_grupo:
            raise ValidationError("La actividad debe tener un área asignada para asignación automática")
        
        lider = self.asignar_lider_automatico()
        if not lider:
            raise ValidationError("No se pudo asignar líder automáticamente")
        
        # Crear asignaciones automáticamente
        for usuario_id in usuarios_ids:
            # Buscar contrato vigente del usuario
            contrato_vigente = ContratoUsuario.objects.filter(
                usuario_id=usuario_id,
                area=self.area_grupo,
                activo=True
            ).first()
            
            if contrato_vigente:
                AsignacionActividad.objects.create(
                    actividad=self,
                    usuario_asignado_id=usuario_id,
                    evaluador=lider,
                    contrato=contrato_vigente,
                    fecha_limite=fecha_limite
                )
            else:
                raise ValidationError(f"El usuario {usuario_id} no tiene contrato vigente en el área {self.area_grupo.nombre}")



from evaluaciondesempeno.models import Area 

class Evaluacion(models.Model):
    TIPO_EVALUACION = [
        ('360', '360 Grados'),
        ('180', '180 Grados'),
        ('90', '90 Grados'),
    ]
    usuario_evaluado = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='evaluaciones_recibidas',
        null=True,
        blank=True
    )
    evaluador = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='evaluaciones_realizadas',
        null=True,
        blank=True
    )
    tipo = models.CharField(max_length=10, choices=TIPO_EVALUACION)
    componente = models.ForeignKey(Componente, on_delete=models.CASCADE)
    area_grupo = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True)
    fecha = models.DateField(auto_now_add=True)

    class Meta:
        # ✅ SIMPLIFICADO: Solo remover unique_together para permitir múltiples evaluaciones
        # unique_together = [
        #     ('usuario_evaluado', 'componente', 'tipo')
        # ]
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['usuario_evaluado', 'tipo', 'componente']),
            models.Index(fields=['evaluador', 'fecha']),
        ]

    def __str__(self):
        return f"{self.tipo} - {self.usuario_evaluado or 'Grupo'}"

    def clean(self):
        """Validaciones adicionales de negocio"""
        from django.core.exceptions import ValidationError
        
        # Validar que se asigne usuario_evaluado o area_grupo, pero no ambos
        if not self.usuario_evaluado and not self.area_grupo:
            raise ValidationError("Debe asignar un usuario evaluado o un área grupo")
        
        # ✅ SIMPLIFICADO: Solo validar que no se evalúe a sí mismo
        if self.usuario_evaluado and self.evaluador and self.usuario_evaluado == self.evaluador:
            raise ValidationError("Un usuario no puede evaluarse a sí mismo")

class PreguntaEvaluacion(models.Model):
    evaluacion = models.ForeignKey(Evaluacion, on_delete=models.CASCADE, related_name='preguntas')
    texto = models.TextField()
    valor = models.IntegerField()

    def __str__(self):
        return self.texto


class PerfilUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    area = models.ForeignKey(Area, on_delete=models.SET_NULL, null=True, blank=True)
    rol = models.CharField(max_length=100, blank=True, null=True)
    cargo = models.CharField(max_length=150, blank=True, null=True)
    es_evaluador = models.BooleanField(default=False)
    es_lider = models.BooleanField(default=False)

    TIPO_EVALUACION = [
        ('360', '360 Grados'),
        ('180', '180 Grados'),
        ('90', '90 Grados'),
    ]
    tipo_evaluacion = models.CharField(max_length=10, choices=TIPO_EVALUACION, blank=True, null=True)

    def __str__(self):
        return f"{self.user.username} - {self.rol or 'Sin rol'}"




class AsignacionEvaluacion(models.Model):
    evaluacion = models.ForeignKey(Evaluacion, on_delete=models.CASCADE, related_name='asignaciones')
    evaluador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='asignaciones_realizadas')
    usuario_evaluado = models.ForeignKey(User, on_delete=models.CASCADE, related_name='asignaciones_recibidas')
    completada = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.evaluador} evalúa a {self.usuario_evaluado} ({self.evaluacion.tipo})"

# Nuevos modelos para sistema mejorado de preguntas 360

class CategoriaPregunta(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    componente = models.ForeignKey(Componente, on_delete=models.CASCADE, related_name='categorias_preguntas')
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['orden', 'nombre']
        unique_together = ['nombre', 'componente']

    def __str__(self):
        return f"{self.nombre} - {self.componente}"

class PreguntaComponente360(models.Model):
    TIPO_PREGUNTA = [
        ('LIKERT', 'Escala Likert'),
        ('ABIERTA', 'Pregunta Abierta'),
        ('MULTIPLE', 'Opción Múltiple'),
        ('BOOLEANA', 'Sí/No'),
        ('NUMERICA', 'Valor Numérico'),
    ]
    
    componente = models.ForeignKey(Componente, on_delete=models.CASCADE, related_name='preguntas_360')
    categoria = models.ForeignKey(CategoriaPregunta, on_delete=models.CASCADE, related_name='preguntas', null=True, blank=True)
    texto = models.TextField()
    tipo = models.CharField(max_length=20, choices=TIPO_PREGUNTA, default='LIKERT')
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    obligatoria = models.BooleanField(default=True)
    peso = models.DecimalField(max_digits=5, decimal_places=2, default=1.00, help_text="Peso de la pregunta en la evaluación")
    
    class Meta:
        ordering = ['categoria__orden', 'orden', 'texto']

    def __str__(self):
        return f"{self.texto[:50]}... ({self.get_tipo_display()})"

class EscalaRespuesta(models.Model):
    pregunta = models.ForeignKey(PreguntaComponente360, on_delete=models.CASCADE, related_name='escalas')
    valor = models.IntegerField()
    descripcion = models.CharField(max_length=100)
    orden = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['orden', 'valor']
        unique_together = ['pregunta', 'valor']

    def __str__(self):
        return f"{self.pregunta.texto[:30]}... - {self.valor}: {self.descripcion}"

class OpcionRespuesta(models.Model):
    pregunta = models.ForeignKey(PreguntaComponente360, on_delete=models.CASCADE, related_name='opciones')
    texto = models.CharField(max_length=200)
    valor = models.CharField(max_length=50, blank=True, null=True)
    orden = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['orden', 'texto']

    def __str__(self):
        return f"{self.pregunta.texto[:30]}... - {self.texto}"

class RespuestaEvaluacion(models.Model):
    asignacion = models.ForeignKey(AsignacionEvaluacion, on_delete=models.CASCADE, related_name='respuestas')
    pregunta = models.ForeignKey(PreguntaComponente360, on_delete=models.CASCADE, related_name='respuestas')
    respuesta_texto = models.TextField(blank=True, null=True)
    respuesta_numerica = models.IntegerField(null=True, blank=True)
    respuesta_booleana = models.BooleanField(null=True, blank=True)
    opcion_seleccionada = models.ForeignKey(OpcionRespuesta, on_delete=models.SET_NULL, null=True, blank=True)
    escala_seleccionada = models.ForeignKey(EscalaRespuesta, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_respuesta = models.DateTimeField(auto_now_add=True)
    comentarios = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ['asignacion', 'pregunta']

    def __str__(self):
        return f"Respuesta de {self.asignacion.evaluador} para {self.pregunta.texto[:30]}..."

    def get_valor_respuesta(self):
        """Retorna el valor de la respuesta según el tipo de pregunta"""
        if self.pregunta.tipo == 'LIKERT' and self.escala_seleccionada:
            return self.escala_seleccionada.valor
        elif self.pregunta.tipo == 'NUMERICA':
            return self.respuesta_numerica
        elif self.pregunta.tipo == 'BOOLEANA':
            return 1 if self.respuesta_booleana else 0
        elif self.pregunta.tipo == 'MULTIPLE' and self.opcion_seleccionada:
            return self.opcion_seleccionada.valor
        return None

class PlantillaEvaluacion(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True, null=True)
    componente = models.ForeignKey(Componente, on_delete=models.CASCADE, related_name='plantillas')
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.nombre} - {self.componente}"

class PreguntaPlantilla(models.Model):
    plantilla = models.ForeignKey(PlantillaEvaluacion, on_delete=models.CASCADE, related_name='preguntas')
    pregunta = models.ForeignKey(PreguntaComponente360, on_delete=models.CASCADE, related_name='plantillas')
    orden = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['orden']
        unique_together = ['plantilla', 'pregunta']

    def __str__(self):
        return f"{self.plantilla.nombre} - {self.pregunta.texto}"

# NUEVAS TABLAS PARA ACTIVIDADES DE DESEMPEÑO

class LiderActividad(models.Model):
    """Configuración de líderes para actividades de desempeño (separado de evaluación 360°)"""
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='lideres_actividades')
    lider_id = models.IntegerField()  # ID manual de login_customer
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)  # NULL = vigente
    activo = models.BooleanField(default=True)
    tipo_actividad = models.CharField(
        max_length=50, 
        choices=[
            ('FUNCIONES_CONTRATO', 'Funciones de Contrato'),
            ('ACTIVIDADES_DIARIAS', 'Actividades Diarias'),
            ('PROYECTOS_ESPECIALES', 'Proyectos Especiales'),
        ],
        default='FUNCIONES_CONTRATO'
    )
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    
    class Meta:
        unique_together = ['area', 'tipo_actividad', 'fecha_inicio']
        ordering = ['-fecha_inicio']
    
    def __str__(self):
        return f"Líder {self.lider_id} - {self.area.nombre} ({self.get_tipo_actividad_display()})"
    
    @property
    def es_vigente(self):
        """Verifica si el liderazgo está vigente"""
        hoy = timezone.now().date()
        return (
            self.activo and 
            self.fecha_inicio <= hoy and 
            (self.fecha_fin is None or self.fecha_fin >= hoy)
        )

class ContratoUsuario(models.Model):
    """Información del contrato laboral del usuario para actividades de desempeño"""
    TIPO_CONTRATO = [
        ('TERMINO_FIJO', 'Término Fijo'),
        ('INDEFINIDO', 'Indefinido'),
        ('PRESTACION_SERVICIOS', 'Prestación de Servicios'),
        ('APRENDIZAJE', 'Contrato de Aprendizaje'),
    ]
    
    usuario_id = models.IntegerField()  # ID manual de login_customer
    identificacion = models.CharField(
        max_length=20, 
        null=True,  # ← AGREGAR: Permitir NULL en base de datos
        blank=True,  # ← AGREGAR: Permitir campo vacío en formularios
        unique=True,  # ← MANTENER: Único solo cuando tenga valor
        help_text="Número de identificación del usuario (cédula, pasaporte, etc.)"
    )
    tipo_contrato = models.CharField(max_length=50, choices=TIPO_CONTRATO)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)  # NULL para indefinido
    activo = models.BooleanField(default=True)
    salario = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    cargo = models.CharField(max_length=150)
    area = models.ForeignKey(Area, on_delete=models.CASCADE, related_name='contratos_usuarios')
    
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now, null=True, blank=True)
    
    class Meta:
        ordering = ['-fecha_inicio']
        indexes = [
            models.Index(fields=['identificacion']),
            models.Index(fields=['usuario_id']),
            models.Index(fields=['area']),
        ]
    
    def __str__(self):
        return f"Usuario {self.usuario_id} - {self.identificacion} ({self.get_tipo_contrato_display()})"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        # Validar que la identificación sea única
        if self.identificacion:
            contratos_existentes = ContratoUsuario.objects.filter(
                identificacion=self.identificacion
            ).exclude(pk=self.pk)
            
            if contratos_existentes.exists():
                raise ValidationError(
                    f"Ya existe un contrato con la identificación {self.identificacion}"
                )
        
        # Validar que las fechas sean coherentes
        if self.fecha_fin and self.fecha_inicio >= self.fecha_fin:
            raise ValidationError("La fecha de fin debe ser posterior a la fecha de inicio")
    
    @property
    def es_vigente(self):
        """Verifica si el contrato está vigente"""
        hoy = timezone.now().date()
        if self.tipo_contrato == 'INDEFINIDO':
            return self.activo and self.fecha_inicio <= hoy
        else:
            return (
                self.activo and 
                self.fecha_inicio <= hoy and 
                (self.fecha_fin is None or self.fecha_fin >= hoy)
            )
    
    @property
    def dias_restantes(self):
        """Calcula días restantes del contrato"""
        if self.tipo_contrato == 'INDEFINIDO' or not self.fecha_fin:
            return None
        hoy = timezone.now().date()
        return (self.fecha_fin - hoy).days if self.fecha_fin > hoy else 0

# Modelo para asignaciones de actividades laborales
class AsignacionActividad(models.Model):
    """Modelo para asignar actividades a usuarios con evaluadores específicos"""
    actividad = models.ForeignKey(Actividad, on_delete=models.CASCADE, related_name='asignaciones')
    usuario_asignado = models.ForeignKey(User, on_delete=models.CASCADE, related_name='actividades_asignadas')
    evaluador = models.ForeignKey(User, on_delete=models.CASCADE, related_name='actividades_para_evaluar')
    contrato = models.ForeignKey(ContratoUsuario, on_delete=models.CASCADE, related_name='actividades_asignadas', null=True, blank=True)
    fecha_asignacion = models.DateTimeField(auto_now_add=True)
    fecha_limite = models.DateTimeField(null=True, blank=True)
    completada = models.BooleanField(default=False)
    fecha_completada = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        unique_together = ['actividad', 'usuario_asignado', 'contrato']
        ordering = ['-fecha_asignacion']
    
    def __str__(self):
        return f"{self.evaluador} evalúa a {self.usuario_asignado} en {self.actividad}"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        # Validar que el evaluador no sea el mismo usuario asignado
        if self.evaluador == self.usuario_asignado:
            raise ValidationError("El evaluador no puede ser el mismo usuario asignado")
        
        # Validar que el evaluador sea líder vigente del área
        if self.contrato and self.contrato.area:
            lider_vigente = LiderActividad.objects.filter(
                area=self.contrato.area,
                tipo_actividad='FUNCIONES_CONTRATO',
                activo=True,
                fecha_inicio__lte=timezone.now().date(),
                fecha_fin__isnull=True
            ).first()
            
            if not lider_vigente or lider_vigente.lider != self.evaluador:
                raise ValidationError(
                    f"El evaluador debe ser el líder vigente del área {self.contrato.area.nombre} "
                    f"para actividades de desempeño"
                )

# Modelo para evaluaciones de actividades laborales
class EvaluacionActividad(models.Model):
    asignacion = models.ForeignKey(AsignacionActividad, on_delete=models.CASCADE, related_name='evaluaciones')
    calificacion = models.DecimalField(max_digits=3, decimal_places=1, help_text="Calificación de 0.0 a 10.0")
    comentarios = models.TextField(blank=True, null=True)
    fecha_evaluacion = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['asignacion']
        ordering = ['-fecha_evaluacion']
    
    def __str__(self):
        return f"{self.asignacion.evaluador} evalúa {self.asignacion.actividad} - {self.calificacion}"
    
    def clean(self):
        if self.calificacion < 0 or self.calificacion > 10:
            raise ValidationError("La calificación debe estar entre 0.0 y 10.0")
    
    def save(self, *args, **kwargs):
        # Marcar la asignación como completada cuando se evalúa
        if not self.asignacion.completada:
            self.asignacion.completada = True
            self.asignacion.fecha_completada = timezone.now()
            self.asignacion.save()
        super().save(*args, **kwargs)

# Modelo para el horario laboral de los usuarios
class HorarioLaboral(models.Model):
    """Registro del horario laboral diario de los usuarios"""
    # Campo de identificación para relacionar con el contrato
    identificacion = models.CharField(
        max_length=20, 
        help_text="Número de identificación del trabajador",
        db_index=True  # Índice para búsquedas rápidas
    )
    
    # Campos de información del trabajador
    nombre_completo = models.CharField(max_length=200, help_text="Nombre completo del trabajador")
    grupo_area = models.CharField(max_length=100, help_text="Grupo o área del trabajador")
    fecha = models.DateField(help_text="Fecha del registro")
    
    # Campos de tiempo
    hora_entrada_manana = models.TimeField(
        null=True, 
        blank=True, 
        help_text="Hora de entrada por la mañana (opcional para registros consolidados)"
    )
    hora_salida_almuerzo = models.TimeField(
        null=True, 
        blank=True, 
        help_text="Hora de salida al almuerzo"
    )
    hora_entrada_almuerzo = models.TimeField(
        null=True, 
        blank=True, 
        help_text="Hora de entrada del almuerzo"
    )
    hora_salida_final = models.TimeField(
        null=True, 
        blank=True, 
        help_text="Hora de salida final"
    )
    
    # Campos de atraso
    atraso_manana = models.BigIntegerField(
        null=True, 
        blank=True, 
        help_text="Minutos de atraso en la llegada"
    )
    atraso_almuerzo = models.BigIntegerField(
        null=True, 
        blank=True, 
        help_text="Minutos de atraso del almuerzo"
    )
    atraso_salida = models.BigIntegerField(
        null=True, 
        blank=True, 
        help_text="Minutos de atraso en la salida"
    )
    
    # Campos adicionales
    total_horas_trabajadas = models.BigIntegerField(
        null=True, 
        blank=True, 
        help_text="Total de horas trabajadas en el día (en minutos)"
    )
    cargo = models.CharField(
        max_length=150, 
        null=True, 
        blank=True, 
        help_text="Cargo del trabajador"
    )
    
    # ✅ CAMPOS PARA CONSOLIDADO (OPCIONALES - pueden no existir en producción)
    total_dias = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Total de días evaluados para esta persona"
    )
    dias_con_atraso = models.IntegerField(
        null=True, 
        blank=True, 
        help_text="Total de días donde hubo al menos un atraso"
    )
    porcentaje_cumplimiento = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        null=True, 
        blank=True, 
        help_text="Porcentaje de cumplimiento calculado (0-100)"
    )
    
    # 🎯 CAMPOS DE TRAZABILIDAD DEL PERÍODO (OPCIONALES - pueden no existir en producción)
    fecha_inicio_periodo = models.DateField(
        null=True, 
        blank=True, 
        help_text="Fecha de inicio del período evaluado en el Excel"
    )
    fecha_fin_periodo = models.DateField(
        null=True, 
        blank=True, 
        help_text="Fecha de fin del período evaluado en el Excel"
    )
    periodo_descripcion = models.CharField(
        max_length=200,
        null=True, 
        blank=True, 
        help_text="Descripción del período (ej: 'Agosto 2025 - Semana 1-2')"
    )
    
    # Metadatos
    archivo_origen = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        help_text="Nombre del archivo Excel de origen"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        # 🎯 PERMITIR MÚLTIPLES REGISTROS POR USUARIO
        # Removido unique_together para evitar problemas con campos NULL
        ordering = ['-fecha', 'identificacion']
        indexes = [
            models.Index(fields=['fecha']),
            models.Index(fields=['identificacion']),
            models.Index(fields=['grupo_area']),
            # 🎯 ÍNDICE PARA CONSULTAS POR PERÍODO (cuando los campos no sean NULL)
            models.Index(fields=['fecha_inicio_periodo', 'fecha_fin_periodo']),
        ]
    
    def __str__(self):
        return f"{self.nombre_completo} - {self.identificacion} - {self.fecha}" 
        
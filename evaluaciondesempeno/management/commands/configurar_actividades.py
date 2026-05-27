from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from evaluaciondesempeno.models import (
    LiderActividad, ContratoUsuario, Actividad, Area, 
    AsignacionActividad, EvaluacionActividad
)
from datetime import date

User = get_user_model()

class Command(BaseCommand):
    help = 'Configura automáticamente líderes y contratos para actividades de desempeño'

    def add_arguments(self, parser):
        parser.add_argument(
            '--area-id',
            type=int,
            help='ID del área para configurar (opcional)',
        )
        parser.add_argument(
            '--lider-id',
            type=int,
            help='ID del usuario líder (opcional)',
        )
        parser.add_argument(
            '--fecha-inicio',
            type=str,
            default='2025-01-01',
            help='Fecha de inicio para contratos y liderazgo (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--fecha-fin',
            type=str,
            help='Fecha de fin para contratos (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--tipo-contrato',
            type=str,
            default='TERMINO_FIJO',
            choices=['TERMINO_FIJO', 'INDEFINIDO', 'PRESTACION_SERVICIOS', 'APRENDIZAJE'],
            help='Tipo de contrato para los usuarios',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Iniciando configuración de actividades de desempeño...'))
        
        # Obtener parámetros
        area_id = options['area_id']
        lider_id = options['lider_id']
        fecha_inicio = date.fromisoformat(options['fecha_inicio'])
        fecha_fin = date.fromisoformat(options['fecha_fin']) if options['fecha_fin'] else None
        tipo_contrato = options['tipo_contrato']
        
        # Si no se especifica área, usar la primera disponible
        if not area_id:
            areas = Area.objects.all()
            if areas.exists():
                area_id = areas.first().id
                self.stdout.write(f'📍 Usando área por defecto: {areas.first().nombre} (ID: {area_id})')
            else:
                self.stdout.write(self.style.ERROR('❌ No hay áreas disponibles. Crea al menos una área primero.'))
                return
        
        # Si no se especifica líder, usar el usuario con ID 5 (que parece ser líder según las imágenes)
        if not lider_id:
            lider_id = 5
            self.stdout.write(f'👑 Usando líder por defecto: Usuario ID {lider_id}')
        
        try:
            # 1. Crear o actualizar líder de actividades
            self.stdout.write('📋 Configurando líder de actividades...')
            lider_actividad, created = LiderActividad.objects.get_or_create(
                area_id=area_id,
                tipo_actividad='FUNCIONES_CONTRATO',
                defaults={
                    'lider_id': lider_id,
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'activo': True
                }
            )
            
            if created:
                self.stdout.write(f'✅ Líder de actividades creado: Usuario {lider_id} para área {area_id}')
            else:
                lider_actividad.lider_id = lider_id
                lider_actividad.fecha_inicio = fecha_inicio
                lider_actividad.fecha_fin = fecha_fin
                lider_actividad.activo = True
                lider_actividad.save()
                self.stdout.write(f'🔄 Líder de actividades actualizado: Usuario {lider_id} para área {area_id}')
            
            # 2. Obtener usuarios que ya están asignados a actividades en esta área
            actividades_area = Actividad.objects.filter(area_grupo_id=area_id)
            usuarios_asignados = set()
            
            for actividad in actividades_area:
                # Usuarios individuales
                if actividad.usuario_asignado:
                    usuarios_asignados.add(actividad.usuario_asignado.id)
                
                # Usuarios de grupo
                usuarios_asignados.update(actividad.usuarios_grupo.values_list('id', flat=True))
            
            # También obtener usuarios de la tabla de asignaciones existentes
            asignaciones_existentes = AsignacionActividad.objects.filter(
                actividad__area_grupo_id=area_id
            )
            for asignacion in asignaciones_existentes:
                usuarios_asignados.add(asignacion.usuario_asignado.id)
                usuarios_asignados.add(asignacion.evaluador.id)
            
            self.stdout.write(f'👥 Usuarios encontrados en el área: {list(usuarios_asignados)}')
            
            # 3. Crear contratos para usuarios asignados
            self.stdout.write('📝 Creando contratos para usuarios...')
            contratos_creados = 0
            contratos_actualizados = 0
            
            for usuario_id in usuarios_asignados:
                if usuario_id == lider_id:  # No crear contrato para el líder
                    continue
                    
                contrato, created = ContratoUsuario.objects.get_or_create(
                    user_id=usuario_id,
                    area_id=area_id,
                    defaults={
                        'tipo_contrato': tipo_contrato,
                        'fecha_inicio': fecha_inicio,
                        'fecha_fin': fecha_fin,
                        'cargo': 'AUXILIAR DE CITAS Y ADMISIONES - CALL CENTER',
                        'activo': True
                    }
                )
                
                if created:
                    contratos_creados += 1
                    self.stdout.write(f'  ✅ Contrato creado para usuario {usuario_id}')
                else:
                    # Actualizar contrato existente
                    contrato.tipo_contrato = tipo_contrato
                    contrato.fecha_inicio = fecha_inicio
                    contrato.fecha_fin = fecha_fin
                    contrato.activo = True
                    contrato.save()
                    contratos_actualizados += 1
                    self.stdout.write(f'  🔄 Contrato actualizado para usuario {usuario_id}')
            
            # 4. Verificar asignaciones existentes y crear nuevas si es necesario
            self.stdout.write('🔗 Verificando asignaciones de actividades...')
            asignaciones_creadas = 0
            
            for actividad in actividades_area:
                # Obtener usuarios para esta actividad
                usuarios_actividad = set()
                if actividad.usuario_asignado:
                    usuarios_actividad.add(actividad.usuario_asignado.id)
                usuarios_actividad.update(actividad.usuarios_grupo.values_list('id', flat=True))
                
                for usuario_id in usuarios_actividad:
                    if usuario_id == lider_id:
                        continue
                        
                    # Verificar si ya existe asignación
                    asignacion_existente = AsignacionActividad.objects.filter(
                        actividad=actividad,
                        usuario_asignado_id=usuario_id
                    ).first()
                    
                    if not asignacion_existente:
                        # Crear nueva asignación
                        try:
                            contrato_usuario = ContratoUsuario.objects.get(
                                user_id=usuario_id,
                                area_id=area_id,
                                activo=True
                            )
                            
                            AsignacionActividad.objects.create(
                                actividad=actividad,
                                usuario_asignado_id=usuario_id,
                                evaluador_id=lider_id,
                                contrato=contrato_usuario,
                                fecha_limite=timezone.now() + timezone.timedelta(days=30)  # 30 días por defecto
                            )
                            asignaciones_creadas += 1
                            self.stdout.write(f'  ✅ Asignación creada: Actividad {actividad.id} -> Usuario {usuario_id}')
                        except ContratoUsuario.DoesNotExist:
                            self.stdout.write(f'  ⚠️  No se pudo crear asignación para usuario {usuario_id} (contrato no encontrado)')
            
            # 5. Resumen final
            self.stdout.write(self.style.SUCCESS('\n🎉 Configuración completada exitosamente!'))
            self.stdout.write(f'📊 Resumen:')
            self.stdout.write(f'  • Líder configurado: Usuario {lider_id}')
            self.stdout.write(f'  • Área: {area_id}')
            self.stdout.write(f'  • Contratos creados: {contratos_creados}')
            self.stdout.write(f'  • Contratos actualizados: {contratos_actualizados}')
            self.stdout.write(f'  • Asignaciones creadas: {asignaciones_creadas}')
            self.stdout.write(f'  • Total usuarios en área: {len(usuarios_asignados)}')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error durante la configuración: {str(e)}'))
            raise

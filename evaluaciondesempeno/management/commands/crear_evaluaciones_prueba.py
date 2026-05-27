from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from evaluaciondesempeno.models import Area, Componente, Evaluacion, AsignacionEvaluacion, CategoriaPregunta, PreguntaComponente360
from evaluaciondesempeno.services import asignar_evaluacion_por_area

User = get_user_model()

class Command(BaseCommand):
    help = 'Crea evaluaciones de prueba para el sistema'

    def handle(self, *args, **options):
        self.stdout.write('Creando evaluaciones de prueba...')
        
        try:
            # Obtener área del Call Center (área 3 según la base de datos)
            area_call_center = Area.objects.get(id=3)
            self.stdout.write(f'Área encontrada: {area_call_center.nombre}')
            
            # Obtener o crear componente de evaluación 360
            componente_360, created = Componente.objects.get_or_create(
                nombre='Evaluación 360 Call Center',
                defaults={
                    'tipo_id': 1,  # Asumiendo que existe un tipo de componente
                    'area': area_call_center,
                    'descripcion': 'Evaluación 360 para personal del Call Center',
                    'es_360': True
                }
            )
            
            if created:
                self.stdout.write(f'Componente creado: {componente_360.nombre}')
            else:
                self.stdout.write(f'Componente existente: {componente_360.nombre}')
            
            # Obtener usuarios del área
            usuarios_area = User.objects.filter(
                perfil__area=area_call_center,
                is_active=True
            ).select_related('perfil')
            
            self.stdout.write(f'Usuarios encontrados en el área: {usuarios_area.count()}')
            
            # Crear evaluaciones para cada usuario
            for usuario in usuarios_area:
                perfil = usuario.perfil
                self.stdout.write(f'Procesando usuario: {usuario.username} (Líder: {perfil.es_lider}, Evaluador: {perfil.es_evaluador})')
                
                # Crear evaluación 360
                evaluacion = Evaluacion.objects.create(
                    usuario_evaluado=usuario,
                    tipo='360',
                    componente=componente_360,
                    area_grupo=area_call_center,
                    fecha='2025-08-11'
                )
                
                # Obtener evaluadores para este usuario
                evaluadores = []
                
                # Autoevaluación
                evaluadores.append(usuario)
                
                # Si es líder, agregar compañeros
                if perfil.es_lider:
                    companeros = User.objects.filter(
                        perfil__area=area_call_center,
                        perfil__es_lider=False,
                        is_active=True
                    ).exclude(id=usuario.id)
                    evaluadores.extend(companeros)
                    
                    # Agregar otros líderes del área
                    otros_lideres = User.objects.filter(
                        perfil__area=area_call_center,
                        perfil__es_lider=True,
                        is_active=True
                    ).exclude(id=usuario.id)
                    evaluadores.extend(otros_lideres)
                else:
                    # Si no es líder, agregar líderes del área
                    lideres = User.objects.filter(
                        perfil__area=area_call_center,
                        perfil__es_lider=True,
                        is_active=True
                    )
                    evaluadores.extend(lideres)
                
                # Crear asignaciones para cada evaluador
                for evaluador in evaluadores:
                    if evaluador != usuario:  # No crear autoevaluación duplicada
                        AsignacionEvaluacion.objects.create(
                            evaluacion=evaluacion,
                            evaluador=evaluador,
                            usuario_evaluado=usuario,
                            completada=False
                        )
                        self.stdout.write(f'  - Asignación creada: {evaluador.username} evalúa a {usuario.username}')
                
                self.stdout.write(f'  Evaluación creada para {usuario.username} con {len(evaluadores)} evaluadores')
            
            self.stdout.write(
                self.style.SUCCESS('✅ Evaluaciones de prueba creadas exitosamente!')
            )
            
            # Mostrar resumen
            total_evaluaciones = Evaluacion.objects.filter(area_grupo=area_call_center).count()
            total_asignaciones = AsignacionEvaluacion.objects.filter(
                evaluacion__area_grupo=area_call_center
            ).count()
            
            self.stdout.write(f'📊 Resumen:')
            self.stdout.write(f'  - Total evaluaciones: {total_evaluaciones}')
            self.stdout.write(f'  - Total asignaciones: {total_asignaciones}')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error al crear evaluaciones: {str(e)}')
            )

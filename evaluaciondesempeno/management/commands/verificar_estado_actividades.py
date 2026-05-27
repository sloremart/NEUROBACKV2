from django.core.management.base import BaseCommand
from django.utils import timezone
from evaluaciondesempeno.models import (
    LiderActividad, ContratoUsuario, Actividad, Area, 
    AsignacionActividad, EvaluacionActividad
)
from django.db import models

class Command(BaseCommand):
    help = 'Verifica el estado actual de las actividades de desempeño y sus asignaciones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--area-id',
            type=int,
            help='ID del área específica para verificar (opcional)',
        )
        parser.add_argument(
            '--detallado',
            action='store_true',
            help='Mostrar información detallada de cada actividad',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔍 Verificando estado de actividades de desempeño...'))
        
        area_id = options['area_id']
        detallado = options['detallado']
        
        # 1. Verificar líderes de actividades
        self.stdout.write('\n📋 LÍDERES DE ACTIVIDADES:')
        self.stdout.write('=' * 50)
        
        if area_id:
            lideres = LiderActividad.objects.filter(area_id=area_id)
        else:
            lideres = LiderActividad.objects.all()
        
        if lideres.exists():
            for lider in lideres:
                estado = '✅ VIGENTE' if lider.es_vigente else '❌ NO VIGENTE'
                self.stdout.write(f'  • Líder ID {lider.lider_id} - Área: {lider.area.nombre}')
                self.stdout.write(f'    Tipo: {lider.get_tipo_actividad_display()}')
                self.stdout.write(f'    Fechas: {lider.fecha_inicio} - {lider.fecha_fin or "Indefinido"}')
                self.stdout.write(f'    Estado: {estado}')
                self.stdout.write('')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No hay líderes configurados para actividades'))
        
        # 2. Verificar contratos de usuarios
        self.stdout.write('📝 CONTRATOS DE USUARIOS:')
        self.stdout.write('=' * 50)
        
        if area_id:
            contratos = ContratoUsuario.objects.filter(area_id=area_id)
        else:
            contratos = ContratoUsuario.objects.all()
        
        if contratos.exists():
            contratos_vigentes = [c for c in contratos if c.es_vigente]
            self.stdout.write(f'  • Total contratos: {contratos.count()}')
            self.stdout.write(f'  • Contratos vigentes: {len(contratos_vigentes)}')
            self.stdout.write(f'  • Contratos vencidos: {contratos.count() - len(contratos_vigentes)}')
            
            if detallado:
                for contrato in contratos:
                    estado = '✅ VIGENTE' if contrato.es_vigente else '❌ VENCIDO'
                    dias_restantes = contrato.dias_restantes
                    dias_info = f"({dias_restantes} días restantes)" if dias_restantes is not None else ""
                    self.stdout.write(f'    - Usuario {contrato.user_id}: {contrato.tipo_contrato} {dias_info} - {estado}')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No hay contratos configurados'))
        
        # 3. Verificar actividades
        self.stdout.write('\n🎯 ACTIVIDADES:')
        self.stdout.write('=' * 50)
        
        if area_id:
            actividades = Actividad.objects.filter(area_grupo_id=area_id)
        else:
            actividades = Actividad.objects.all()
        
        if actividades.exists():
            self.stdout.write(f'  • Total actividades: {actividades.count()}')
            
            # Agrupar por área
            actividades_por_area = {}
            for actividad in actividades:
                area_nombre = actividad.area_grupo.nombre if actividad.area_grupo else 'Sin área'
                if area_nombre not in actividades_por_area:
                    actividades_por_area[area_nombre] = []
                actividades_por_area[area_nombre].append(actividad)
            
            for area_nombre, acts in actividades_por_area.items():
                self.stdout.write(f'  📍 Área: {area_nombre} ({len(acts)} actividades)')
                
                if detallado:
                    for act in acts:
                        usuarios_individual = f"Usuario: {act.usuario_asignado.id}" if act.usuario_asignado else "Sin usuario individual"
                        usuarios_grupo = f"Grupo: {list(act.usuarios_grupo.values_list('id', flat=True))}" if act.usuarios_grupo.exists() else "Sin usuarios de grupo"
                        self.stdout.write(f'    - ID {act.id}: {act.nombre} - {usuarios_individual} - {usuarios_grupo}')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No hay actividades configuradas'))
        
        # 4. Verificar asignaciones de actividades
        self.stdout.write('\n🔗 ASIGNACIONES DE ACTIVIDADES:')
        self.stdout.write('=' * 50)
        
        if area_id:
            asignaciones = AsignacionActividad.objects.filter(actividad__area_grupo_id=area_id)
        else:
            asignaciones = AsignacionActividad.objects.all()
        
        if asignaciones.exists():
            completadas = asignaciones.filter(completada=True).count()
            pendientes = asignaciones.filter(completada=False).count()
            
            self.stdout.write(f'  • Total asignaciones: {asignaciones.count()}')
            self.stdout.write(f'  • Completadas: {completadas}')
            self.stdout.write(f'  • Pendientes: {pendientes}')
            
            if detallado:
                for asignacion in asignaciones:
                    estado = '✅ COMPLETADA' if asignacion.completada else '⏳ PENDIENTE'
                    fecha_limite = asignacion.fecha_limite.strftime('%Y-%m-%d') if asignacion.fecha_limite else 'Sin límite'
                    self.stdout.write(f'    - Actividad {asignacion.actividad.id}: {asignacion.usuario_asignado.id} -> {asignacion.evaluador.id} - {estado} - Límite: {fecha_limite}')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No hay asignaciones de actividades'))
        
        # 5. Verificar evaluaciones de actividades
        self.stdout.write('\n📊 EVALUACIONES DE ACTIVIDADES:')
        self.stdout.write('=' * 50)
        
        if area_id:
            evaluaciones = EvaluacionActividad.objects.filter(asignacion__actividad__area_grupo_id=area_id)
        else:
            evaluaciones = EvaluacionActividad.objects.all()
        
        if evaluaciones.exists():
            self.stdout.write(f'  • Total evaluaciones: {evaluaciones.count()}')
            
            # Calcular promedio
            promedio = evaluaciones.aggregate(avg_calificacion=models.Avg('calificacion'))['avg_calificacion']
            if promedio:
                self.stdout.write(f'  • Calificación promedio: {promedio:.2f}/10.0')
            
            if detallado:
                for evaluacion in evaluaciones:
                    fecha = evaluacion.fecha_evaluacion.strftime('%Y-%m-%d %H:%M')
                    self.stdout.write(f'    - {evaluacion.asignacion.actividad.nombre}: {evaluacion.calificacion}/10.0 - {fecha}')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No hay evaluaciones de actividades'))
        
        # 6. Resumen y recomendaciones
        self.stdout.write('\n💡 RESUMEN Y RECOMENDACIONES:')
        self.stdout.write('=' * 50)
        
        # Verificar si hay problemas
        problemas = []
        
        if not lideres.exists():
            problemas.append("❌ No hay líderes configurados para actividades")
        
        if not contratos.exists():
            problemas.append("❌ No hay contratos configurados para usuarios")
        
        if not actividades.exists():
            problemas.append("❌ No hay actividades configuradas")
        
        if not asignaciones.exists():
            problemas.append("❌ No hay asignaciones de actividades")
        
        # Verificar cobertura
        if actividades.exists() and contratos.exists():
            actividades_sin_area = actividades.filter(area_grupo__isnull=True).count()
            if actividades_sin_area > 0:
                problemas.append(f"⚠️  {actividades_sin_area} actividades no tienen área asignada")
        
        if problemas:
            self.stdout.write(self.style.WARNING('  Problemas detectados:'))
            for problema in problemas:
                self.stdout.write(f'    {problema}')
        else:
            self.stdout.write(self.style.SUCCESS('  ✅ Sistema configurado correctamente'))
        
        # Recomendaciones
        self.stdout.write('\n  Recomendaciones:')
        if not lideres.exists():
            self.stdout.write('    • Ejecuta: python manage.py configurar_actividades')
        if not contratos.exists():
            self.stdout.write('    • Ejecuta: python manage.py configurar_actividades')
        if asignaciones.filter(completada=False).exists():
            self.stdout.write('    • Hay actividades pendientes de evaluación')
        
        self.stdout.write(self.style.SUCCESS('\n🎉 Verificación completada!'))

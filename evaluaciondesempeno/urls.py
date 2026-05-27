from django.urls import path, include
from rest_framework.routers import DefaultRouter
from evaluaciondesempeno.views import (
    EvaluacionViewSet, AsignacionEvaluacionViewSet, PreguntaComponente360ViewSet,
    CategoriaPreguntaViewSet, PlantillaEvaluacionViewSet, RespuestaEvaluacionViewSet,
    EvaluacionActividadViewSet, AsignacionActividadViewSet, Evaluacion360ViewSet,
    # ViewSets básicos
    AreaViewSet, TipoComponenteViewSet, ComponenteViewSet, ActividadViewSet,
    # ViewSets del dashboard
    DashboardLiderViewSet, DashboardUsuarioViewSet,
    # ViewSets que funcionaban antes
    UsuariosConPerfilViewSet,
    # NUEVO ViewSet para servicios de evaluación
    ServiciosEvaluacionViewSet,
    # NUEVOS ViewSets para actividades de desempeño
    LiderActividadViewSet, ContratoUsuarioViewSet,
    # NUEVOS ViewSets para gestión de horario laboral
    HorarioLaboralViewSet, procesar_horario_laboral
)

router = DefaultRouter()

# ViewSets básicos
router.register(r'areas', AreaViewSet)
router.register(r'tipos-componentes', TipoComponenteViewSet)
router.register(r'componentes', ComponenteViewSet)
router.register(r'actividades', ActividadViewSet)

# ViewSets del sistema de evaluación
router.register(r'evaluaciones', EvaluacionViewSet, basename='evaluaciones')
router.register(r'asignaciones', AsignacionEvaluacionViewSet, basename='asignaciones')
router.register(r'preguntas360', PreguntaComponente360ViewSet, basename='preguntas360')
router.register(r'categorias-preguntas', CategoriaPreguntaViewSet, basename='categorias-preguntas')
router.register(r'plantillas', PlantillaEvaluacionViewSet, basename='plantillas')
router.register(r'respuestas', RespuestaEvaluacionViewSet, basename='respuestas')
router.register(r'asignaciones-actividades', AsignacionActividadViewSet, basename='asignaciones-actividades')
router.register(r'evaluaciones-actividades', EvaluacionActividadViewSet, basename='evaluaciones-actividades')
router.register(r'evaluaciones-360', Evaluacion360ViewSet, basename='evaluaciones-360')

# ViewSets del dashboard
router.register(r'dashboard-lider', DashboardLiderViewSet, basename='dashboard-lider')
router.register(r'dashboard-usuario', DashboardUsuarioViewSet, basename='dashboard-usuario')

# ViewSets que funcionaban antes
router.register(r'usuarios_con_perfil', UsuariosConPerfilViewSet, basename='usuarios-con-perfil')
router.register(r'perfiles-usuario', UsuariosConPerfilViewSet, basename='perfiles-usuario')

# NUEVO ViewSet para servicios de evaluación
router.register(r'servicios-evaluacion', ServiciosEvaluacionViewSet, basename='servicios-evaluacion')

# NUEVOS ViewSets para actividades de desempeño
router.register(r'lideres-actividades', LiderActividadViewSet, basename='lideres-actividades')
router.register(r'contratos-usuarios', ContratoUsuarioViewSet, basename='contratos-usuarios')

# NUEVOS ViewSets para gestión de horario laboral
router.register(r'horarios-laborales', HorarioLaboralViewSet, basename='horarios-laborales')

urlpatterns = [
    path('', include(router.urls)),
    path('procesar-horario-laboral/', procesar_horario_laboral, name='procesar_horario_laboral'),
]

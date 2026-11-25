from django.urls import path
from .views_gestion import (
    bandeja_realizados,
    bandeja_radicados,
    bandeja_devoluciones,
    detalle_caso,
    actualizar_caso,
    represamiento_admisiones,
    represamiento_cuentas_medicas,
    hallazgos_pendientes,
)

from . import views_auditoria as aud

urlpatterns = [
    # Auditoría de honorarios médicos
    path('auditoria/resumen/',                 aud.resumen,           name='aud-resumen'),
    path('auditoria/medico/<int:cod_medico>/', aud.detalle_medico,    name='aud-medico'),
    path('auditoria/linea/',                   aud.guardar_linea,     name='aud-linea'),
    path('auditoria/cerrar/',                  aud.cerrar,            name='aud-cerrar'),
    path('auditoria/exportar/',                aud.exportar,          name='aud-exportar'),
    path('auditoria/tarifas/',                 aud.tarifas,           name='aud-tarifas'),
    path('auditoria/tarifas/importar/',        aud.importar_tarifas,  name='aud-tarifas-importar'),
    path('auditoria/tarifas/nueva/',           aud.crear_tarifa,      name='aud-tarifa-nueva'),
    path('auditoria/tarifas/asignar-medico/',  aud.asignar_medico,    name='aud-tarifas-medico'),
    path('auditoria/tarifas/<int:tarifa_id>/', aud.editar_tarifa,     name='aud-tarifa-editar'),
    path('auditoria/medicos/',                 aud.medicos,           name='aud-medicos'),
    path('auditoria/lumier/cargar/',           aud.cargar_lumier,     name='aud-lumier-cargar'),
    path('auditoria/lumier/hallazgos/',        aud.hallazgos_lumier,  name='aud-lumier-hallazgos'),
    path('auditoria/lumier/resolver/',         aud.resolver_lumier,   name='aud-lumier-resolver'),
    path('auditoria/sin-facturar/',            aud.sin_facturar,      name='aud-sin-facturar'),
    path('auditoria/sin-tarifa/',              aud.sin_tarifa,        name='aud-sin-tarifa'),
    path('auditoria/indicadores/',             aud.indicadores,       name='aud-indicadores'),
    path('auditoria/errores-resultados/',      aud.errores_resultados, name='aud-errores-resultados'),

    path('realizados/',                    bandeja_realizados,         name='gf-realizados'),
    path('radicados/',                     bandeja_radicados,          name='gf-radicados'),
    path('devoluciones/',                  bandeja_devoluciones,       name='gf-devoluciones'),
    path('caso/<int:admision_id>/',        detalle_caso,               name='gf-detalle'),
    path('caso/<int:admision_id>/actualizar/', actualizar_caso,        name='gf-actualizar'),
    path('represamiento-admisiones/',      represamiento_admisiones,   name='gf-represamiento-admisiones'),
    path('hallazgos-pendientes/',          hallazgos_pendientes,       name='gf-hallazgos-pendientes'),
    path('represamiento-cuentas/',         represamiento_cuentas_medicas, name='gf-represamiento-cuentas'),
]

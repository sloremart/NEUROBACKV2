from django.urls import path
from .views_gestion import (
    bandeja_realizados,
    bandeja_radicados,
    bandeja_devoluciones,
    detalle_caso,
    actualizar_caso,
)

urlpatterns = [
    path('realizados/',                    bandeja_realizados,   name='gf-realizados'),
    path('radicados/',                     bandeja_radicados,    name='gf-radicados'),
    path('devoluciones/',                  bandeja_devoluciones, name='gf-devoluciones'),
    path('caso/<int:admision_id>/',        detalle_caso,         name='gf-detalle'),
    path('caso/<int:admision_id>/actualizar/', actualizar_caso,  name='gf-actualizar'),
]

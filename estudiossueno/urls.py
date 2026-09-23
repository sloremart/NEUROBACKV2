from django.urls import path
from .views import (
    RegistroListCreateView,
    RegistroDetailView,
    get_estudios,
    get_equipos,
    get_especialistas,
    get_tecnicos,
    get_entidades,
    get_pacientes_agenda,
)

urlpatterns = [
    path("registros/",           RegistroListCreateView.as_view(), name="sueno-registros"),
    path("registros/<int:pk>/",  RegistroDetailView.as_view(),     name="sueno-registro-detail"),
    path("estudios/",            get_estudios,                     name="sueno-estudios"),
    path("equipos/",             get_equipos,                      name="sueno-equipos"),
    path("especialistas/",       get_especialistas,                name="sueno-especialistas"),
    path("tecnicos/",            get_tecnicos,                     name="sueno-tecnicos"),
    path("entidades/",           get_entidades,                    name="sueno-entidades"),
    path("pacientes-agenda/",    get_pacientes_agenda,             name="sueno-pacientes-agenda"),
]

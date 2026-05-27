from django.urls import path
from .views import ListaExamenes, SubirDocumentoExamen

urlpatterns = [
    path('examenes/', ListaExamenes.as_view(), name='lista-examenes'),
    path('examenes/subir/', SubirDocumentoExamen.as_view(), name='subir-examen'),
]

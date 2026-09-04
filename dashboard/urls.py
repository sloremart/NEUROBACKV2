from django.urls import path

from dashboard.view import (
    DashboardAgendadasView,
    DashboardFacturacionEntidadView, DashboardRiesgoCompartidoView,
    EstadoCarteraTodasEntidades, EstadoCarteraPorNit, RecaudoPeriodoView,
    MedicosView, CitasMedicoView,
    ProduccionMensualView, ResultadosEstudiosView,
    DashboardFacturacionNuevoView,
    DashboardAdmisionesVsFacturacionView,
    CargaAutorizacionesView,
)


urlpatterns = [
    path('facturacion/',           DashboardFacturacionEntidadView.as_view(),  name='dashboard-facturacion'),
    path('agendadas/',             DashboardAgendadasView.as_view(),            name='dashboard-agendadas'),
    path('consolidado_cartera/',   EstadoCarteraTodasEntidades.as_view(),       name='factura-revision-financiera'),
    path('cartera_por_nit/',       EstadoCarteraPorNit.as_view(),               name='cartera-por-nit'),
    path('recaudo/',               RecaudoPeriodoView.as_view(),                name='recaudo-entidades'),
    path('mrc/',                   DashboardRiesgoCompartidoView.as_view(),     name='mrc-sanitas'),
    path('medicos/',               MedicosView.as_view(),                       name='medicos-list'),
    path('citas-medico/',          CitasMedicoView.as_view(),                   name='citas-medico'),
    path('produccion-mensual/',    ProduccionMensualView.as_view(),             name='produccion-mensual'),
    path('resultados-estudios/',   ResultadosEstudiosView.as_view(),            name='resultados-estudios'),
    path('facturacion-nuevo/',      DashboardFacturacionNuevoView.as_view(),             name='facturacion-nuevo'),
    path('facturacion-admisiones/', DashboardAdmisionesVsFacturacionView.as_view(),     name='facturacion-admisiones'),
    path('carga-autorizaciones/',   CargaAutorizacionesView.as_view(),                 name='carga-autorizaciones'),
]
from django.urls import path

from dashboard.view import CarteraConsolidadaPorEntidadAPIView, DashboardAgendadasView, DashboardFacturacionEntidadView, DashboardRiesgoCompartidoView, EstadoCarteraTodasEntidades, EstadoCarteraPorNit, RecaudoPeriodoView, MedicosView, CitasMedicoView


urlpatterns = [
    path('facturacion/', DashboardFacturacionEntidadView.as_view(), name='dashboard-facturacion'),
    path('agendadas/', DashboardAgendadasView.as_view(), name='dashboard-facturacion'),
    path('consolidado_entidades/', CarteraConsolidadaPorEntidadAPIView.as_view(), name='facturacionradicada-noradicada'),
    path('consolidado_cartera/', EstadoCarteraTodasEntidades.as_view(), name='factura-revision-financiera'),
    path('cartera_por_nit/', EstadoCarteraPorNit.as_view(), name='cartera-por-nit'),
    path('recaudo/', RecaudoPeriodoView.as_view(), name='recaudo-entidades'),
    path('mrc/', DashboardRiesgoCompartidoView.as_view(), name='recaudo-entidades'),
    path('medicos/', MedicosView.as_view(), name='medicos-list'),
    path('citas-medico/', CitasMedicoView.as_view(), name='citas-medico'),
]
from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.conf.urls.static import static

from programacionpagos.views import FacturaPagoTesoreriaView, FacturaProgramacionPagoCreateView, FacturaProveedorDeleteView, FacturaProveedorUploadView, FacturaRevisionFinancieraView, FacturasRevisorView, FacturasPorPrioridadYFechasView, FacturasUsuarioFiltradasView, GenerarEgresoView, ListaCuentas, ListaFacturas, ListaFacturasAprobadasFinancieramente, ListaNits, RevisarFacturaProveedorView, actualizar_numero_egreso

from django.urls import include, path


urlpatterns = [
   
    path("lista_cuentas/", ListaCuentas.as_view(), name="guardar_consolidado_estudios"),
    path("lista_nits/", ListaNits.as_view(), name="guardar_consolidado_estudios"),
    path('facturas/', ListaFacturas.as_view(), name='lista_facturas'),
    path('consolidado_facturas_pagos/', FacturaProgramacionPagoCreateView.as_view(), name='lista_facturas'),
    path('obtener_ciclo_pagos/', FacturasPorPrioridadYFechasView.as_view(), name='lista_facturas'),
    path('factura/revision_financiera/<int:factura_id>/', FacturaRevisionFinancieraView.as_view(), name='factura-revision-financiera'),
    path('facturas_aprobadas_financieramente/', ListaFacturasAprobadasFinancieramente.as_view(), name='factura-revision-financiera'),
    path('pago_tesoreria/<int:factura_id>/', FacturaPagoTesoreriaView.as_view(), name='factura-revision-financiera'),
    path('obtener_egreso/<int:factura_id>/', GenerarEgresoView.as_view(), name='factura-revision-financiera'),
    #path('informe/', DocumentosPorNit.as_view(), name='factura-revision-financiera'),
    path('factura_proveedor/upload/<str:nit>/', FacturaProveedorUploadView.as_view()),
    path('facturas_revisor/<int:id_revisor>/', FacturasRevisorView.as_view()),
    path('aprobacion_revisor/<int:id_archivo>/', RevisarFacturaProveedorView.as_view(), name='aprobacion_revisor'),
    path('facturas_usuario/<int:id_usuario>/', FacturasUsuarioFiltradasView.as_view(), name='facturas_usuario'),
    path('actualizar_egreso/<int:id_archivo>/', actualizar_numero_egreso),
    path('eliminar_archivo/<int:id_archivo>/', FacturaProveedorDeleteView.as_view(), name='eliminar_archivo'),
   
]





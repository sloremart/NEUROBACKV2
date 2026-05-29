from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static

# ── Auth ──────────────────────────────────────────────────────────────────────
from login.registroViews import (
    CustomUserListView, LoginView, LogoutView,
    RegisterView, ChangePasswordView, ProfileView,
)

# ── Gedocumental ──────────────────────────────────────────────────────────────
from gedocumental.views import (
    GeDocumentalView,
    ArchivoUploadView,
    ArchivoEditView,
    ArchivoFacturacionDeleteView,
    archivos_por_admision,
    downloadFile,
    CodigoListView,
    ActualizarRegimenArchivosView,
    AdmisionCuentaMedicaView,
    AdmisionTesoreriaView,
    archivos_por_usuario_observacion,
    archivos_por_usuario_observacion_tesoreria,
    ObservacionesPorUsuario,
    AgregarObservacionSinArchivoView,
    RevisarObservacion,
    FiltroAuditoriaCuentasMedicas,
    FiltroTesoreria,
    actualizar_modificado_revisor,
    actualizar_correciones_cm,
    AdmisionesRadicarView,
    radicar_capitalsalud_view,
    radicar_colsanitas_view,
    radicar_salud_total_view,
    radicar_sanitas_evento_view,
    radicar_compensar_view,
    radicar_fomag_view,
    radicar_policia_view,
    radicar_ejercito_view,
    radicar_mes01_view,
    radicar_san02_view,
    radicar_other_view,
)
from gedocumental.utils.codigoentidad import obtener_hallazgos

# =============================================================================
# MÓDULO: Auth  →  api/v2/auth/
# =============================================================================
auth_urls = [
    path('login/',           LoginView.as_view(),          name='v2-login'),
    path('logout/',          LogoutView.as_view(),          name='v2-logout'),
    path('register/',        RegisterView.as_view(),        name='v2-register'),
    path('change-password/', ChangePasswordView.as_view(),  name='v2-change-password'),
    path('profile/',         ProfileView.as_view(),         name='v2-profile'),
    path('usuarios/',        CustomUserListView.as_view(),  name='v2-usuarios'),
]

# =============================================================================
# MÓDULO: Gedocumental  →  api/v2/gedocumental/
# =============================================================================
gedocumental_urls = [
    path('admisiones/<int:consecutivo>/',                        GeDocumentalView.as_view(),            name='v2-admision'),
    path('archivos/<str:consecutivo>/',                          ArchivoUploadView.as_view(),            name='v2-archivo-upload'),
    path('archivos-por-admision/<int:numero_admision>/',         archivos_por_admision,                  name='v2-archivos-por-admision'),
    path('descargar/<int:id_archivo>/',                          downloadFile,                           name='v2-descargar'),
    path('archivos/<str:consecutivo>/editar/<int:archivo_id>/',  ArchivoEditView.as_view(),              name='v2-archivo-edit'),
    path('eliminar-archivo/',                                    ArchivoFacturacionDeleteView.as_view(), name='v2-archivo-delete'),
    path('hallazgos/',                                           obtener_hallazgos,                      name='v2-hallazgos'),
    path('lista-codigo-entidad/',                                CodigoListView.as_view(),               name='v2-codigo-entidad'),
    path('actualizar-regimen/<int:consecutivo>/',                ActualizarRegimenArchivosView.as_view(),name='v2-actualizar-regimen'),
    path('admision-revision/<int:consecutivo>/',                 AdmisionCuentaMedicaView.as_view(),     name='v2-admision-revision'),
    path('admision-revision-tesoreria/<int:consecutivo>/',       AdmisionTesoreriaView.as_view(),        name='v2-admision-revision-tesoreria'),
    path('archivos-por-usuario/<int:user_id>/',                  archivos_por_usuario_observacion,       name='v2-archivos-por-usuario'),
    path('archivos-por-usuario-tesoreria/<int:user_id>/',        archivos_por_usuario_observacion_tesoreria, name='v2-archivos-por-usuario-tesoreria'),
    path('observaciones/<int:user_id>/',                         ObservacionesPorUsuario.as_view(),          name='v2-observaciones'),
    path('admisiones-radicar/',                                  AdmisionesRadicarView.as_view(),            name='v2-admisiones-radicar'),
    # ── Radicación / Renombramiento por entidad ───────────────────────────────
    path('radicar-capital-salud/<int:numero_admision>/<str:idusuario>/',   radicar_capitalsalud_view,    name='v2-radicar-capital-salud'),
    path('radicar-colsanitas/<int:numero_admision>/<str:idusuario>/',      radicar_colsanitas_view,      name='v2-radicar-colsanitas'),
    path('radicar-salud-total/<int:numero_admision>/<str:idusuario>/',     radicar_salud_total_view,     name='v2-radicar-salud-total'),
    path('radicar-sanitas-evento/<int:numero_admision>/<str:idusuario>/',  radicar_sanitas_evento_view,  name='v2-radicar-sanitas-evento'),
    path('radicar-compensar/<int:numero_admision>/<str:idusuario>/',       radicar_compensar_view,       name='v2-radicar-compensar'),
    path('radicar-fomag/<int:numero_admision>/<str:idusuario>/',           radicar_fomag_view,           name='v2-radicar-fomag'),
    path('radicar-policia/<int:numero_admision>/<str:idusuario>/',         radicar_policia_view,         name='v2-radicar-policia'),
    path('radicar-ejercito/<int:numero_admision>/<str:idusuario>/',        radicar_ejercito_view,        name='v2-radicar-ejercito'),
    path('radicar-medisanitas/<int:numero_admision>/<str:idusuario>/',     radicar_mes01_view,           name='v2-radicar-medisanitas'),
    path('radicar-san02/<int:numero_admision>/<str:idusuario>/',           radicar_san02_view,           name='v2-radicar-san02'),
    path('radicar-otros/<int:numero_admision>/<str:idusuario>/',           radicar_other_view,           name='v2-radicar-otros'),
]

# =============================================================================
# URL raíz — se agregan módulos gradualmente bajo api/v2/
# =============================================================================
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v2/auth/',                include(auth_urls)),
    path('api/v2/gedocumental/',        include(gedocumental_urls)),
    path('api/v2/programacionpagos/',   include('programacionpagos.urls')),
    path('api/v2/',                     include('resultadosgedocumental.urls')),
    # ── Endpoints legacy (sin prefijo gedocumental/) ─────────────────────────
    path('api/v2/descargar/<int:id_archivo>/',        downloadFile,                            name='v2-descargar-legacy'),
    path('api/v2/agregar_observacion_sin_archivo/',   AgregarObservacionSinArchivoView.as_view(), name='v2-obs-sin-archivo'),
    path('api/v2/revisar_observacion/<int:admision_id>/', RevisarObservacion.as_view(),        name='v2-revisar-observacion'),
    path('api/v2/filtro_auditoria/',                  FiltroAuditoriaCuentasMedicas.as_view(), name='v2-filtro-auditoria'),
    path('api/v2/filtro_tesoreria/',                  FiltroTesoreria.as_view(),               name='v2-filtro-tesoreria'),
    path('api/v2/actualizar_modificado_revisor/',     actualizar_modificado_revisor,            name='v2-actualizar-modificado'),
    path('api/v2/actualizar_correciones_cm/',         actualizar_correciones_cm,               name='v2-actualizar-correcciones-cm'),
    path('api/v2/actualizar_regimen/<int:consecutivo>/', ActualizarRegimenArchivosView.as_view(), name='v2-actualizar-regimen-legacy'),
    path('api/v2/eliminar_archivo_facturacion/',      ArchivoFacturacionDeleteView.as_view(),  name='v2-eliminar-archivo-legacy'),
    # path('api/v2/citas/',        include(citas_urls)),       # próximo módulo
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        path('__debug__/', include(debug_toolbar.urls)),
    ]

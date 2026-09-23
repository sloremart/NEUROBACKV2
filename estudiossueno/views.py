from django.http import JsonResponse
from django.db import connections
from rest_framework import generics
from rest_framework.decorators import api_view

from .models import RegistroEstudioSueno, ESTUDIOS_CHOICES, EQUIPOS_CHOICES
from .serializers import RegistroEstudioSuenoSerializer

# CUPS de sueño registrados en ZeusSalud
CUPS_SUENO = ("891703", "891704", "891901")

# IDs de asunto de sueño en sis_tipo
ASUNTOS_SUENO = (14, 15, 16)


class RegistroListCreateView(generics.ListCreateAPIView):
    serializer_class = RegistroEstudioSuenoSerializer

    def get_queryset(self):
        qs = RegistroEstudioSueno.objects.all()
        fecha_inicio = self.request.query_params.get("fecha_inicio")
        fecha_fin    = self.request.query_params.get("fecha_fin")
        if fecha_inicio:
            qs = qs.filter(fecha_realizacion__gte=fecha_inicio)
        if fecha_fin:
            qs = qs.filter(fecha_realizacion__lte=fecha_fin)
        return qs


class RegistroDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset         = RegistroEstudioSueno.objects.all()
    serializer_class = RegistroEstudioSuenoSerializer


@api_view(["GET"])
def get_estudios(request):
    """Lista normalizada de tipos de estudio."""
    data = [{"value": v, "label": l} for v, l in ESTUDIOS_CHOICES]
    return JsonResponse({"success": True, "data": data})


@api_view(["GET"])
def get_equipos(request):
    data = [{"value": v, "label": l} for v, l in EQUIPOS_CHOICES]
    return JsonResponse({"success": True, "data": data})


@api_view(["GET"])
def get_especialistas(request):
    """
    Especialistas de sueño: neurólogos y neuropediatras activos en ZeusSalud.
    """
    try:
        with connections["zeussalud"].cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT
                    m.codigo,
                    LTRIM(RTRIM(m.nombre))       AS nombre,
                    LTRIM(RTRIM(m.especialidad)) AS especialidad
                FROM Medicos m
                WHERE m.especialidad IN (
                    'NEUROLOGIA',
                    'NEUROLOGIA PEDIATRICA'
                )
                ORDER BY 2
                """
            )
            rows = cur.fetchall()

        data = [{"codigo": r[0], "nombre": r[1], "especialidad": r[2]} for r in rows]
        return JsonResponse({"success": True, "data": data})
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)


@api_view(["GET"])
def get_tecnicos(request):
    """
    Técnicos de sueño — usuarios de SIESA cuyo email contiene
    el dominio neuroelectrodx y están activos como auxiliares de electrodiagnóstico.
    """
    try:
        with connections["zeussalud"].cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT
                    LTRIM(RTRIM(u.nombre)) AS nombre,
                    u.email
                FROM usuario u
                WHERE u.email LIKE '%neuroelectrodx%'
                  AND (
                      u.nombre LIKE '%AMAYA%'
                      OR u.nombre LIKE '%MORENO%'
                      OR u.nombre LIKE '%BAQUERO%'
                      OR u.nombre LIKE '%MOTAVITA%'
                      OR u.nombre LIKE '%MOLANO%'
                      OR u.nombre LIKE '%DIAZ%'
                      OR u.nombre LIKE '%GONZAEZ%'
                      OR u.nombre LIKE '%GONZALEZ%'
                      OR u.nombre LIKE '%BOBADILLA%'
                      OR u.nombre LIKE '%BENITEZ%'
                      OR u.nombre LIKE '%PEREZ%'
                      OR u.nombre LIKE '%WILLIAM%'
                      OR u.nombre LIKE '%YESENIA%'
                      OR u.nombre LIKE '%LINA%'
                      OR u.nombre LIKE '%MARTHA%'
                  )
                ORDER BY nombre
                """
            )
            rows = cur.fetchall()

        data = [{"nombre": r[0], "email": r[1]} for r in rows]
        return JsonResponse({"success": True, "data": data})
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)


@api_view(["GET"])
def get_entidades(request):
    """Lista de entidades/EPS desde sis_empre de ZeusSalud."""
    try:
        with connections["zeussalud"].cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT LTRIM(RTRIM(nombre)) AS nombre
                FROM sis_empre
                WHERE nombre IS NOT NULL AND nombre != ''
                ORDER BY 1
                """
            )
            rows = cur.fetchall()

        data = [{"nombre": r[0]} for r in rows]
        return JsonResponse({"success": True, "data": data})
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)


@api_view(["GET"])
def get_pacientes_agenda(request):
    """
    Pacientes con cita agendada para estudios de sueño (CUPS 891703/891704/891901)
    en el rango de fecha indicado.
    fecha_inicio, fecha_fin: YYYY-MM-DD (ambos requeridos)
    """
    fecha_inicio = request.query_params.get("fecha_inicio")
    fecha_fin    = request.query_params.get("fecha_fin")
    if not fecha_inicio or not fecha_fin:
        return JsonResponse(
            {"success": False, "detail": "Se requieren fecha_inicio y fecha_fin"}, status=400
        )

    try:
        placeholders = ",".join(["%s"] * len(CUPS_SUENO))
        with connections["zeussalud"].cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT
                    sm.con_estudio AS admision,
                    LTRIM(RTRIM(
                        COALESCE(sp.primer_nom,'') + ' ' +
                        COALESCE(sp.segundo_nom,'') + ' ' +
                        COALESCE(sp.primer_ape,'') + ' ' +
                        COALESCE(sp.segundo_ape,'')
                    )) AS nombres_completos,
                    sp.fecha_naci                             AS fecha_nacimiento,
                    LTRIM(RTRIM(sp.num_id))                   AS numero_documento,
                    LTRIM(RTRIM(sm.EPSPaciente))              AS entidad,
                    LTRIM(RTRIM(sd.descripcion))              AS descripcion_cups,
                    sd.cups
                FROM sis_maes sm
                JOIN sis_deta sd ON sd.estudio = sm.con_estudio
                LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
                WHERE CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
                  AND sm.estado = 'A'
                  AND sd.cups IN ({placeholders})
                ORDER BY sm.con_estudio
                """,
                [fecha_inicio, fecha_fin, *CUPS_SUENO],
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        return JsonResponse({"success": True, "data": rows})
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)

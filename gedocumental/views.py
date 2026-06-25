

from django.utils.timezone import make_aware
import shutil
from django.utils.dateparse import parse_date
from datetime import datetime, time
from django.db.models import Q, Max
from PyPDF2 import PdfMerger
from urllib.parse import unquote
from django.db import IntegrityError, transaction
from django.db.models.functions import TruncDate
from django.db import connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import api_view
from gedocumental.modelsFacturacion import Admisiones
from gedocumental.utils.codigoentidad import obtener_tipos_documentos_por_entidad, TIPOS_DOCUMENTOS_ESTANDAR
from login.models import CustomUser
from resultadosgedocumental.models import ConsolidadoEstudios
from .serializers import   ArchivoFacturacionSerializer, ObservacionSinArchivoSerializer,  RevisionCuentaMedicaSerializer
from django.http import Http404
from .models import ArchivoFacturacion, AuditoriaCuentasMedicas, ObservacionSinArchivo, ObservacionesArchivos
from django.conf import settings
import os
from django.db.models import Count
from django.views.decorators.http import require_GET
from datetime import datetime, timezone
from urllib.parse import unquote
from rest_framework.permissions import AllowAny
from datetime import date
from rest_framework.decorators import api_view, permission_classes
import zipfile


# ── Helper ZeusSalud ─────────────────────────────────────────────────────────
def get_admision_zeus(cursor, consecutivo):
    """Retorna los datos de una admisión desde sis_maes + sis_paci usando con_estudio."""
    cursor.execute('''
        SELECT TOP 1
            sm.con_estudio,
            sp.num_id,
            sm.EPSPaciente,
            LTRIM(RTRIM(COALESCE(sp.primer_nom,'')+' '+COALESCE(sp.segundo_nom,'')+' '+COALESCE(sp.primer_ape,'')+' '+COALESCE(sp.segundo_ape,''))) AS NombreCompleto,
            sm.nro_factura,
            sm.tipoUsuario,
            sm.fecha_ing
        FROM sis_maes sm
        LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
        WHERE sm.con_estudio = %s
        ORDER BY sm.con_estudio DESC
    ''', [consecutivo])
    return cursor.fetchone()

def get_admisiones_zeus_bulk(cursor, ids):
    """Retorna un dict {con_estudio: row} para una lista de IDs de admisión."""
    if not ids:
        return {}
    placeholders = ', '.join(['%s'] * len(ids))
    cursor.execute(f'''
        SELECT
            sm.con_estudio,
            sp.num_id,
            sm.EPSPaciente,
            LTRIM(RTRIM(COALESCE(sp.primer_nom,'')+' '+COALESCE(sp.segundo_nom,'')+' '+COALESCE(sp.primer_ape,'')+' '+COALESCE(sp.segundo_ape,''))) AS NombreCompleto,
            sm.nro_factura,
            sm.tipoUsuario,
            sm.fecha_ing
        FROM sis_maes sm
        LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
        WHERE sm.con_estudio IN ({placeholders})
    ''', list(ids))
    return {row[0]: row for row in cursor.fetchall()}
# ─────────────────────────────────────────────────────────────────────────────


class GeDocumentalView(APIView):
    def get(self, request, consecutivo, format=None):
        with connections['zeussalud'].cursor() as cursor:
            # Consulta admisión + paciente en una sola query (ZeusSalud_Neuro)
            query_admision = '''
                SELECT TOP 1
                    sm.con_estudio,
                    sp.num_id,
                    sm.EPSPaciente,
                    sm.acompanante,
                    COALESCE(sm.NumeroFactura, fp.NumeroFactura) AS NumeroFactura,
                    sm.tipoUsuario,
                    sp.tipo_afilia,
                    sp.fecha_naci,
                    sp.tipo_id,
                    sp.telefono,
                    sp.email,
                    sp.primer_ape,
                    sp.segundo_ape,
                    sp.primer_nom,
                    sp.segundo_nom,
                    sp.sexo,
                    sp.autoid AS id_paciente,
                    se.nombre AS NombreEntidad,
                    c.alias AS ContratoAlias,
                    c.regimen AS ContratoRegimen,
                    sm.fecha_ing AS FechaIngreso,
                    COALESCE(sm.Prefijo, fp.Prefijo) AS Prefijo
                FROM sis_maes sm
                LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
                LEFT JOIN sis_empre se ON LTRIM(RTRIM(sm.EPSPaciente)) = LTRIM(RTRIM(se.codigo))
                LEFT JOIN contratos c ON sm.contrato = c.codigo
                LEFT JOIN sis_maes_FacturaPcte fp ON sm.con_estudio = fp.Estudio AND fp.Anulada = 0
                WHERE sm.con_estudio = %s
                ORDER BY sm.con_estudio DESC
            '''
            cursor.execute(query_admision, [consecutivo])
            admision_data = cursor.fetchone()

            if admision_data:
                codigo_entidad = admision_data[2]
                tipos_documentos = obtener_tipos_documentos_por_entidad(codigo_entidad)

                partes_nombre = [
                    (admision_data[13] or '').strip(),
                    (admision_data[14] or '').strip(),
                    (admision_data[11] or '').strip(),
                    (admision_data[12] or '').strip(),
                ]
                nombre_completo = ' '.join(p for p in partes_nombre if p)

                transformed_data = {
                    'Consecutivo':       admision_data[0],
                    'IdPaciente':        admision_data[1],
                    'CodigoEntidad':     admision_data[2],
                    'NombreResponsable': admision_data[3],
                    'NombreCompleto':    nombre_completo,
                    'FacturaNo':         admision_data[4],
                    'tRegimen':          admision_data[5],
                    'Prefijo':           admision_data[21],
                    'TiposDocumentos':   tipos_documentos,
                    'TipoAfiliacion':    admision_data[6],
                    'FechaNacimiento':   admision_data[7],
                    'TipoID':            admision_data[8],
                    'Telefono':          admision_data[9],
                    'CorreoE':           admision_data[10],
                    'Apellido1':         admision_data[11],
                    'Apellido2':         admision_data[12],
                    'Nombre1':           admision_data[13],
                    'Nombre2':           admision_data[14],
                    'SexoPaciente':      admision_data[15],
                    'NumeroPaciente':    admision_data[16],
                    'NombreEntidad':     admision_data[17],
                    'ContratoAlias':     admision_data[18],
                    'ContratoRegimen':   admision_data[19],
                    'FechaIngreso':      admision_data[20].isoformat() if admision_data[20] else None,
                }

                return Response({
                    "success": True,
                    "detail": f"Información de la admisión con consecutivo {consecutivo}",
                    "data": transformed_data
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    "success": False,
                    "detail": f"No se encontró información para la admisión con consecutivo {consecutivo}",
                    "data": None
                }, status=status.HTTP_404_NOT_FOUND)
import uuid
import hashlib
def calcular_hash_archivo(file):
    """ Calcula el hash SHA256 de un archivo para detectar duplicados. """
    hasher = hashlib.sha256()
    for chunk in file.chunks():
        hasher.update(chunk)
    return hasher.hexdigest()

class ArchivoUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, consecutivo, format=None):
        try:
            user_id = request.data.get('userId')
            regimen = request.data.get('regimen')
            tipo_documento = request.data.get('tipoDocumentos')
            tipo_hallazgo = request.data.get('tipoHallazgo', None)

            if not tipo_documento:
                return JsonResponse({"success": False, "detail": "El tipo de documento es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)

            admision = Admisiones.objects.using('zeussalud').filter(Consecutivo=consecutivo).first()
            if not admision:
                return JsonResponse({"success": False, "detail": "Admisión no encontrada."}, status=status.HTTP_404_NOT_FOUND)

            # Obtener archivos existentes en la admisión
            archivos_existentes = ArchivoFacturacion.objects.filter(Admision_id=consecutivo)

            archivos = request.FILES.getlist('files')
            archivos_guardados = []

            for archivo in archivos:
                archivo.seek(0)  # Reiniciar puntero de lectura
                hash_archivo = calcular_hash_archivo(archivo)

                # Comprobar si un archivo con el mismo hash ya existe
                for existente in archivos_existentes:
                    ruta_existente = os.path.join(settings.MEDIA_ROOT, existente.RutaArchivo.name)
                    if os.path.exists(ruta_existente):
                        with open(ruta_existente, 'rb') as existing_file:
                            hash_existente = hashlib.sha256(existing_file.read()).hexdigest()

                        if hash_existente == hash_archivo:
                            return JsonResponse({
                                "success": False,
                                "detail": f"El archivo {archivo.name} ya fue subido previamente.",
                                "data": None
                            }, status=status.HTTP_400_BAD_REQUEST)

                # Manejo de nombre de archivo duplicado con sufijo aleatorio
                base_name, ext = os.path.splitext(archivo.name)
                unique_filename = f"{base_name}_{str(uuid.uuid4())[:8]}{ext}"

                # Crear directorio si no existe
                folder_path = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'archivosFacturacion', str(admision.Consecutivo))
                os.makedirs(folder_path, exist_ok=True)

                # Guardar el archivo con el nuevo nombre
                archivo_path = os.path.join(folder_path, unique_filename)
                with open(archivo_path, 'wb') as file:
                    for chunk in archivo.chunks():
                        file.write(chunk)

                # Construir ruta relativa del archivo
                ruta_relativa = os.path.join('gdocumental', 'archivosFacturacion', str(admision.Consecutivo), unique_filename)

                fecha_creacion_archivo = datetime.now().replace(second=0, microsecond=0)
                archivo_obj = ArchivoFacturacion(
                    Admision_id=admision.Consecutivo,
                    Tipo=tipo_documento,
                    RutaArchivo=ruta_relativa,
                    NombreArchivo=unique_filename,
                    FechaCreacionArchivo=fecha_creacion_archivo,
                    FechaCreacionAntares=admision.FechaCreado.date() if admision.FechaCreado else None,
                    Usuario_id=user_id,
                    Regimen=regimen,
                    TipoHallazgo=tipo_hallazgo
                )
                archivo_obj.NumeroAdmision = admision.Consecutivo
                archivo_obj.save()

                archivos_guardados.append({
                    "id": archivo_obj.IdArchivo,
                    "ruta": archivo_obj.RutaArchivo.url,
                    "nombre": archivo_obj.NombreArchivo
                })

            return JsonResponse({
                "success": True,
                "detail": f"Archivos guardados exitosamente para la admisión con consecutivo {consecutivo}",
                "data": archivos_guardados
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return JsonResponse({"success": False, "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



##### EDICION DE ARCHIVOS ##########

class ArchivoEditView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def put(self, request, consecutivo, archivo_id, format=None):
        try:
            admision = Admisiones.objects.using('zeussalud').filter(Consecutivo=consecutivo).first()
            if not admision:
                return JsonResponse({"success": False, "detail": "Admisión no encontrada."}, status=status.HTTP_404_NOT_FOUND)
            archivo = ArchivoFacturacion.objects.get(IdArchivo=archivo_id, Admision_id=admision.Consecutivo)

            if 'archivo' in request.FILES:
                archivo_nuevo = request.FILES['archivo']

                # Obtener la ruta de la carpeta actual del archivo
                carpeta_actual = os.path.dirname(archivo.RutaArchivo.path)

                # Eliminar el archivo antiguo
                archivo.RutaArchivo.delete(save=False)

                # Guardar el archivo nuevo en la misma carpeta
                archivo_nombre_nuevo = os.path.join(carpeta_actual, archivo_nuevo.name)
                with open(archivo_nombre_nuevo, 'wb') as file:
                    for chunk in archivo_nuevo.chunks():
                        file.write(chunk)

                # Aquí debes asegurarte de que la ruta que guardas en la base de datos sea relativa
                ruta_relativa = os.path.join('gdocumental', 'archivosFacturacion', str(admision.Consecutivo), archivo_nuevo.name)

                # Actualizar los campos del archivo
                archivo.NombreArchivo = archivo_nuevo.name
                archivo.RutaArchivo = ruta_relativa  # Guardar la ruta relativa, no absoluta
                archivo.save(update_fields=['NombreArchivo', 'RutaArchivo'])

                # Actualizar la fecha sin usar timezone
                auditoria = AuditoriaCuentasMedicas.objects.get(AdmisionId=archivo.Admision_id)
                auditoria.FechaCargueArchivo = datetime.now()  # Aquí se cambia a datetime.now()
                auditoria.save(update_fields=['FechaCargueArchivo'])

            response_data = {
                "success": True,
                "detail": f"Archivo {archivo_id} editado exitosamente para la admisión con consecutivo {consecutivo}",
                "data": None
            }

            return JsonResponse(response_data, status=status.HTTP_200_OK)

        except Admisiones.DoesNotExist:
            response_data = {
                "success": False,
                "detail": f"No se encontró la admisión con consecutivo {consecutivo}",
                "data": None
            }
            return JsonResponse(response_data, status=status.HTTP_404_NOT_FOUND)

        except ArchivoFacturacion.DoesNotExist:
            response_data = {
                "success": False,
                "detail": f"No se encontró el archivo con ID {archivo_id} asociado a la admisión con consecutivo {consecutivo}",
                "data": None
            }
            return JsonResponse(response_data, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            return JsonResponse({
                "success": False,
                "detail": f"Error desconocido al editar el archivo: {e}",
                "data": None
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

      
      

## BUSCAR ARCHIVOS PARA RADICAR###   
############# archivos ############################
@api_view(['GET'])
def archivos_por_admision_radicacion(request, numero_admision):
    try:
        archivos = ArchivoFacturacion.objects.filter(NumeroAdmision=numero_admision)
        serializer = ArchivoFacturacionSerializer(archivos, many=True)
        
        response_data = {
            "success": True,
            "detail": f"Archivos encontrados para la admisión con número {numero_admision}",
            "data": serializer.data
        }

        return Response(response_data, status=status.HTTP_200_OK)

    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}",
            "data": None
        }

        return Response(response_data, status=status.HTTP_404_NOT_FOUND)

@api_view(['GET'])
def archivos_por_admision(request, numero_admision):
    try:
        archivos = ArchivoFacturacion.objects.filter(NumeroAdmision=numero_admision)
        observaciones = ObservacionSinArchivo.objects.filter(AdmisionId=numero_admision)
        
        archivo_serializer = ArchivoFacturacionSerializer(archivos, many=True)
        observacion_serializer = ObservacionSinArchivoSerializer(observaciones, many=True)
        
        response_data = {
            "success": True,
            "detail": f"Archivos encontrados para la admisión con número {numero_admision}",
            "data": {
                "archivos": archivo_serializer.data,
                "observaciones": observacion_serializer.data
            }
        }
      
        return Response(response_data, status=status.HTTP_200_OK)

    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}",
            "data": None
        }
       
        return Response(response_data, status=status.HTTP_404_NOT_FOUND)

    except Exception as e:
       
        return Response({"success": False, "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)  
      





      

## visualizar archivo###
@api_view(['GET'])
def downloadFile(request, id_archivo):
    try:
        archivo = ArchivoFacturacion.objects.get(IdArchivo=id_archivo)
        ruta = str(archivo.RutaArchivo)

        # Normalize Windows-style absolute paths (C:\... or C:/...)
        if len(ruta) >= 2 and ruta[1] == ':':
            ruta = ruta[2:]
        ruta = ruta.replace('\\', '/')

        # Strip leading /media/ or /media/disco1/examenes/ prefix if stored as absolute URL
        for prefix in ['/media/disco1/examenes/', '/media/']:
            if ruta.startswith(prefix):
                ruta = ruta[len(prefix):]
                break
        ruta = ruta.lstrip('/')

        archivo_path = os.path.join(settings.MEDIA_ROOT, ruta)
        logger.info(f"Ruta completa esperada: {archivo_path}")

        if not os.path.exists(archivo_path):
            logger.error(f"Archivo no encontrado: {archivo_path}")
            raise Http404("El archivo no existe")

        with open(archivo_path, 'rb') as file:
            file_content = file.read()

        content_type = 'application/pdf'
        ext = os.path.splitext(archivo_path)[1].lower()
        if ext in ('.jpg', '.jpeg'):
            content_type = 'image/jpeg'
        elif ext == '.png':
            content_type = 'image/png'

        response = HttpResponse(file_content, content_type=content_type)
        response['Content-Disposition'] = 'inline; filename=' + os.path.basename(archivo_path)
        return response

    except ArchivoFacturacion.DoesNotExist:
        logger.error(f"ID de archivo no encontrado en base de datos: {id_archivo}")
        raise Http404("El archivo no existe")

    except Exception as e:
        logger.exception(f"Error inesperado: {e}")
        raise Http404(f"Error inesperado: {str(e)}")

######## REVISION CUENTAS MEDICAS - TALENTO HUMANO #######
         
class AdmisionCuentaMedicaView(APIView):
    def post(self, request, *args, **kwargs):
       
        data = request.data
        archivos = data.get('archivos', [])
        consecutivo_consulta = data.get('consecutivoConsulta')
        usuario_cuentas_medicas_id = request.data.get('UsuarioCuentasMedicas')

        try:
            with transaction.atomic():
                for archivo_data in archivos:
                    archivo_id = archivo_data.get('IdArchivo')
                    try:
                        archivo_existente = ArchivoFacturacion.objects.get(IdArchivo=archivo_id)
                        archivo_serializer = RevisionCuentaMedicaSerializer(archivo_existente, data=archivo_data, partial=True)

                        if archivo_serializer.is_valid():
                            archivo_obj = archivo_serializer.save()
                            # Si hay observación o RevisionPrimera es True, guarda UsuarioCuentasMedicas y FechaRevisionPrimera
                            observacion = archivo_data.get('Observacion')
                            if observacion or archivo_data.get('RevisionPrimera', False):
                                archivo_obj.UsuarioCuentasMedicas_id = usuario_cuentas_medicas_id
                                archivo_obj.FechaRevisionPrimera = date.today()  # Establecer la fecha de revisión
                                archivo_obj.save()
                              

                            if observacion:
                                observacion_obj = ObservacionesArchivos.objects.create(
                                    IdArchivo=archivo_existente,
                                    Descripcion=observacion,
                                    ObservacionCuentasMedicas=True  
                                )
                              
                        else:
                            errors = archivo_serializer.errors
                            return Response({"success": False, "message": "Error de validación en los datos del archivo", "error_details": errors}, status=status.HTTP_400_BAD_REQUEST)

                    except ArchivoFacturacion.DoesNotExist:
                        return Response({"success": False, "message": f"Archivo con ID {archivo_id} no encontrado"}, status=status.HTTP_404_NOT_FOUND)

                admision_ids = [archivo_data.get('Admision_id') for archivo_data in archivos]
                
                auditoria_cuentas_medicas = AuditoriaCuentasMedicas.objects.filter(AdmisionId__in=admision_ids)
                todos_revision_primera_true = all(archivo_data.get('RevisionPrimera', False) for archivo_data in archivos)
                auditoria_cuentas_medicas.update(RevisionCuentasMedicas=todos_revision_primera_true)

                return Response({"success": True, "message": "Datos guardados correctamente"}, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"success": False, "message": "Error interno del servidor", "error_details": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
######TESORERIA 
class AdmisionTesoreriaView(APIView):
    def post(self, request, *args, **kwargs):
       
        data = request.data
        archivos = data.get('archivos', [])
        consecutivo_consulta = data.get('consecutivoConsulta')
        usuario_tesoreria_id = request.data.get('UsuariosTesoreria')

        try:
            with transaction.atomic():
                for archivo_data in archivos:
                    archivo_id = archivo_data.get('IdArchivo')
                    try:
                        archivo_existente = ArchivoFacturacion.objects.get(IdArchivo=archivo_id)
                        archivo_serializer = RevisionCuentaMedicaSerializer(archivo_existente, data=archivo_data, partial=True)

                        if archivo_serializer.is_valid():
                            archivo_obj = archivo_serializer.save()
                            # Si hay observación o RevisionSegunda es True, guarda UsuariosTesoreria
                            observacion = archivo_data.get('Observacion')
                            if observacion or archivo_data.get('RevisionSegunda', False):
                                archivo_obj.UsuariosTesoreria_id = usuario_tesoreria_id
                                archivo_obj.save()
                                

                            if observacion:
                                observacion_obj = ObservacionesArchivos.objects.create(
                                    IdArchivo=archivo_existente,
                                    Descripcion=observacion,
                                    ObservacionTesoreria=True  # Se establece en True si es para tesorería
                                )
                                print("Observación creada:", observacion_obj)
                        else:
                            errors = archivo_serializer.errors
                            return Response({"success": False, "message": "Error de validación en los datos del archivo", "error_details": errors}, status=status.HTTP_400_BAD_REQUEST)

                    except ArchivoFacturacion.DoesNotExist:
                        return Response({"success": False, "message": f"Archivo con ID {archivo_id} no encontrado"}, status=status.HTTP_404_NOT_FOUND)

                todos_revision_segunda_true = all(archivo_data.get('RevisionSegunda', False) for archivo_data in archivos)
                print("Todos los archivos tienen RevisionSegunda en True:", todos_revision_segunda_true)

                if todos_revision_segunda_true:
                    auditoria_cuentas_medicas = AuditoriaCuentasMedicas.objects.filter(AdmisionId=consecutivo_consulta)
                    print("Registros de AuditoriaCuentasMedicas antes de la actualización:", auditoria_cuentas_medicas)

                    auditoria_cuentas_medicas.update(RevisionTesoreria=True)
                    print("Registros de AuditoriaCuentasMedicas después de la actualización:", auditoria_cuentas_medicas)

                return Response({"success": True, "message": "Datos guardados correctamente"}, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({"success": False, "message": "Error interno del servidor", "error_details": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        
####### FILTRO DE ADMISIONES Y ARCHIVOS POR FECHA ####


class FiltroAuditoriaCuentasMedicas(APIView):
    def get(self, request):
        user_id = request.query_params.get('user_id', None)
        fecha_inicio_str = request.query_params.get('FechaInicio', None)
        fecha_fin_str = request.query_params.get('FechaFin', None)
        revision_cuentas_medicas = request.query_params.get('RevisionCuentasMedicas', None)
        codigo_entidad = request.query_params.get('CodigoEntidad', None)

        if not user_id:
            return Response({"error": "user_id is required"}, status=400)

        archivos_facturacion = ArchivoFacturacion.objects.filter(Usuario_id=user_id)

        # Convertir fechas de inicio y fin a objetos datetime
        try:
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d') if fecha_inicio_str else None
            fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d') if fecha_fin_str else None
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        # Filtrar archivos por rango de fechas si están presentes
        if fecha_inicio and fecha_fin:
            archivos_facturacion = archivos_facturacion.filter(
                (Q(FechaCreacionAntares__gte=fecha_inicio) & Q(FechaCreacionAntares__lte=fecha_fin)) |
                (Q(FechaCreacionArchivo__gte=fecha_inicio) & Q(FechaCreacionArchivo__lte=fecha_fin))
            )

        if revision_cuentas_medicas is not None:
            if revision_cuentas_medicas == "0":
                archivos_facturacion = archivos_facturacion.filter(RevisionPrimera=False)
            elif revision_cuentas_medicas == "1":
                archivos_facturacion = archivos_facturacion.filter(RevisionPrimera=True)

        admision_ids = archivos_facturacion.values_list('Admision_id', flat=True).distinct()
        queryset = AuditoriaCuentasMedicas.objects.filter(AdmisionId__in=admision_ids)

        response_data = []

        with connections['zeussalud'].cursor() as cursor:
            for auditoria in queryset:
                admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)

                if admision_data:
                    if not codigo_entidad or codigo_entidad == admision_data[2]:
                        archivo_facturacion = archivos_facturacion.filter(Admision_id=auditoria.AdmisionId).first()

                        data = {
                            'AdmisionId': auditoria.AdmisionId,
                            'FechaCreacion': auditoria.FechaCreacion.strftime('%Y-%m-%d'),
                            'FechaCargueArchivo': auditoria.FechaCargueArchivo.strftime('%Y-%m-%d'),
                            'Observacion': auditoria.Observacion,
                            'RevisionCuentasMedicas': auditoria.RevisionCuentasMedicas,
                            'RevisionTesoreria': auditoria.RevisionTesoreria,
                            'Consecutivo': admision_data[0],
                            'IdPaciente': admision_data[1],
                            'CodigoEntidad': admision_data[2],
                            'NombreResponsable': admision_data[3],
                            'CedulaResponsable': None,
                            'FacturaNo': admision_data[4],
                            'FechaCreacionAntares': archivo_facturacion.FechaCreacionAntares.strftime('%Y-%m-%d') if archivo_facturacion and archivo_facturacion.FechaCreacionAntares else None,
                            'FechaCreacionArchivo': archivo_facturacion.FechaCreacionArchivo.strftime('%Y-%m-%d') if archivo_facturacion and archivo_facturacion.FechaCreacionArchivo else None
                        }
                        response_data.append(data)

        return Response(response_data, status=200)






class CodigoListView(APIView):
    def get(self, request, format=None):
        try:
            with connections['zeussalud'].cursor() as cursor:
                cursor.execute('''
                    SELECT codigo, LTRIM(RTRIM(ISNULL(alias, nombre)))
                    FROM contratos
                    WHERE alias IS NOT NULL AND LTRIM(RTRIM(alias)) != ''
                    ORDER BY alias
                ''')
                rows = cursor.fetchall()
            codigos = [{'codigo': str(row[0]), 'nombre': row[1]} for row in rows]
            return Response(codigos)
        except Exception as e:
            return Response({'error': str(e)}, status=500)


class AdmisionesRadicarView(APIView):
    def get(self, request):
        contrato_id = request.query_params.get('ContratoId')
        fecha_inicio = request.query_params.get('FechaInicio')
        fecha_fin = request.query_params.get('FechaFin')

        if not all([contrato_id, fecha_inicio, fecha_fin]):
            return Response({'error': 'Faltan parámetros: ContratoId, FechaInicio, FechaFin'}, status=400)

        with connections['zeussalud'].cursor() as cursor:
            cursor.execute('''
                SELECT
                    sm.con_estudio,
                    sm.EPSPaciente,
                    LTRIM(RTRIM(
                        COALESCE(sp.primer_nom,'') + ' ' +
                        COALESCE(sp.segundo_nom,'') + ' ' +
                        COALESCE(sp.primer_ape,'') + ' ' +
                        COALESCE(sp.segundo_ape,'')
                    )) AS NombreCompleto,
                    sm.fecha_ing,
                    c.alias AS ContratoAlias
                FROM sis_maes sm
                LEFT JOIN sis_paci sp ON sm.autoid = sp.autoid
                LEFT JOIN contratos c ON sm.contrato = c.codigo
                WHERE sm.contrato = %s
                  AND CAST(sm.fecha_ing AS DATE) BETWEEN %s AND %s
                ORDER BY sm.fecha_ing DESC
            ''', [contrato_id, fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

        if not rows:
            return Response([], status=200)

        admision_ids = [row[0] for row in rows]

        # Solo admisiones con auditoría completa (RevisionCuentasMedicas = True)
        ids_revisados = set(
            AuditoriaCuentasMedicas.objects
            .filter(AdmisionId__in=admision_ids, RevisionCuentasMedicas=True)
            .values_list('AdmisionId', flat=True)
        )

        if not ids_revisados:
            return Response([], status=200)

        # Igual que el proyecto anterior: tener al menos RESULTADO, HCNEURO o FACTURA con Radicado=False
        archivos_filtrados = ArchivoFacturacion.objects.filter(
            Admision_id__in=ids_revisados,
            Radicado=False,
            Tipo__in=['RESULTADO', 'HCNEURO', 'FACTURA']
        )

        archivos_por_admision = {}
        for archivo in archivos_filtrados:
            if archivo.Admision_id not in archivos_por_admision:
                archivos_por_admision[archivo.Admision_id] = archivo

        result = []
        for row in rows:
            admision_id, eps_code, nombre, fecha_ing, contrato_alias = row
            if admision_id not in ids_revisados:
                continue
            archivo = archivos_por_admision.get(admision_id)
            if not archivo:
                continue
            fecha_archivo = archivo.FechaCreacionArchivo
            result.append({
                'AdmisionId': admision_id,
                'CodigoEntidad': eps_code or '',
                'ContratoAlias': contrato_alias or eps_code or '',
                'NombreResponsable': (nombre or '').strip(),
                'RevisionCuentasMedicas': True,
                'FechaCreacionAntares': fecha_ing.isoformat() if fecha_ing else None,
                'FechaCreacionArchivo': fecha_archivo.isoformat() if fecha_archivo else None,
                'Radicado': archivo.Radicado,
            })

        return Response(result)
    

### FILTRO QUE TRAE LAS ADM QUE TIENEN OBSER CM  ######

def admisiones_con_observaciones_por_usuario(request, usuario_id):
    try:
        # Filtrar registros de ObservacionesArchivos para el usuario dado con ObservacionCuentasMedicas
        observaciones = ObservacionesArchivos.objects.filter(
            IdArchivo__Usuario_id=usuario_id,
            ObservacionCuentasMedicas=True
        )

        # Obtener los IDs de las admisiones con las observaciones
        admisiones_ids = observaciones.values_list('IdArchivo__Admision_id', flat=True).distinct()

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor_zeussalud, connections['default'].cursor() as cursor_neurodx:
            for admision_id in admisiones_ids:
                archivos_admision = ArchivoFacturacion.objects.filter(Admision_id=admision_id)
                if not archivos_admision.filter(RevisionPrimera=False).exists():
                    continue

                if not AuditoriaCuentasMedicas.objects.filter(AdmisionId=admision_id, RevisionCuentasMedicas=False).exists():
                    continue

                admision_data = get_admision_zeus(cursor_zeussalud, admision_id)

                if admision_data:
                    numero_factura = admision_data[4] or ''
                    factura_completa = str(numero_factura)

                    fecha_reciente_observacion = ObservacionesArchivos.objects.filter(
                        IdArchivo__Admision_id=admision_id
                    ).aggregate(max_fecha=Max('FechaObservacion'))['max_fecha']

                    cursor_neurodx.execute('SELECT Modificado1 FROM archivos WHERE Admision_id = %s', [admision_id])
                    modificado_info = cursor_neurodx.fetchone()
                    modificado1 = modificado_info[0] if modificado_info else ''

                    transformed_data = {
                        'Consecutivo': admision_data[0],
                        'IdPaciente': admision_data[1],
                        'CodigoEntidad': admision_data[2],
                        'NombreResponsable': admision_data[3],
                        'FacturaNo': factura_completa,
                        'FechaRecienteObservacion': fecha_reciente_observacion,
                        'Modificado1': modificado1
                    }
                    admisiones_data.append(transformed_data)

        response_data = {
            "success": True,
            "detail": f"Admisiones con observaciones encontradas para el usuario con ID {usuario_id}",
            "data": admisiones_data
        }

        return JsonResponse(response_data, status=200)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }

        return JsonResponse(response_data, status=500)
#####FILTRO QUE TRAE LAS ADM QUE TIENEN OBSER CM Y TESOERIA###################
def admisiones_con_revision_tesoreria(request, usuario_id):
    try:
        # Filtrar registros de ObservacionesArchivos para el usuario dado
        observaciones = ObservacionesArchivos.objects.filter(
            IdArchivo__Usuario_id=usuario_id,
            ObservacionTesoreria=True
        )

        # Obtener los Ids de las admisiones con las observaciones
        admisiones_ids = observaciones.values_list('IdArchivo__Admision_id', flat=True).distinct()

        # Filtrar registros de AuditoriaCuentasMedicas con la condición especificada (solo RevisionTesoreria)
        admisiones_con_observaciones = AuditoriaCuentasMedicas.objects.filter(
            AdmisionId__in=admisiones_ids,
            RevisionTesoreria=False  # Solo filtramos por RevisionTesoreria
        )

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor:
            for auditoria in admisiones_con_observaciones:
                # Verificar que al menos un archivo asociado tenga RevisionSegunda=False y ObservacionTesoreria=True
                archivos_admision = ArchivoFacturacion.objects.filter(Admision_id=auditoria.AdmisionId)
                if not archivos_admision.filter(RevisionSegunda=False, Observaciones__ObservacionTesoreria=True).exists():
                    continue

                # Obtener datos de la admisión
                admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)
                # [0]=con_estudio, [1]=num_id, [2]=EPSPaciente, [3]=NombreCompleto, [4]=nro_factura, [5]=tipoUsuario, [6]=fecha_ing

                if admision_data:
                    factura_completa = str(admision_data[4] or '')

                    transformed_data = {
                        'Consecutivo': admision_data[0],
                        'IdPaciente': admision_data[1],
                        'CodigoEntidad': admision_data[2],
                        'NombreResponsable': admision_data[3],
                        'FacturaNo': factura_completa,
                    }
                    admisiones_data.append(transformed_data)

        response_data = {
            "success": True,
            "detail": f"Admisiones con revisión de tesorería pendiente encontradas para el usuario con ID {usuario_id}",
            "data": admisiones_data
        }

        return JsonResponse(response_data, status=200)

    except AuditoriaCuentasMedicas.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron admisiones con revisión de tesorería pendiente para el usuario con ID {usuario_id}",
            "data": None
        }

        return JsonResponse(response_data, status=404)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }

        return JsonResponse(response_data, status=500)
###### FILTRO TESORERIA #####
class FiltroTesoreria(APIView):
    def get(self, request):
        user_id = request.query_params.get('user_id', None)
        fecha_creacion_antares_str = request.query_params.get('FechaCreacionAntares', None)
        fecha_creacion_archivo_str = request.query_params.get('FechaCreacionArchivo', None)
        revision_cuentas_medicas = request.query_params.get('RevisionCuentasMedicas', None)
        revision_tesoreria = request.query_params.get('RevisionTesoreria', None)
        codigo_entidad = request.query_params.get('CodigoEntidad', None)

        if not user_id:
            return Response({"error": "user_id is required"}, status=400)

        archivos_facturacion = ArchivoFacturacion.objects.filter(Usuario_id=user_id)

        if fecha_creacion_antares_str:
            try:
                fecha_creacion_antares = datetime.strptime(fecha_creacion_antares_str, '%Y-%m-%d').date()
                archivos_facturacion = archivos_facturacion.filter(FechaCreacionAntares__date=fecha_creacion_antares)
            except ValueError:
                return Response({'error': 'Formato de fecha inválido para FechaCreacionAntares, debe ser YYYY-MM-DD'}, status=400)

        if fecha_creacion_archivo_str:
            try:
                fecha_creacion_archivo = datetime.strptime(fecha_creacion_archivo_str, '%Y-%m-%d').date()
                archivos_facturacion = archivos_facturacion.filter(FechaCreacionArchivo__date=fecha_creacion_archivo)
            except ValueError:
                return Response({'error': 'Formato de fecha inválido para FechaCreacionArchivo, debe ser YYYY-MM-DD'}, status=400)

        # Filtro por defecto: solo traer donde RevisionPrimera es True
        archivos_facturacion = archivos_facturacion.filter(RevisionPrimera=True)

        admision_ids = archivos_facturacion.values_list('Admision_id', flat=True).distinct()
        queryset = AuditoriaCuentasMedicas.objects.filter(AdmisionId__in=admision_ids)

        if revision_tesoreria is not None:
            revision_tesoreria = bool(int(revision_tesoreria))
            queryset = queryset.filter(RevisionTesoreria=revision_tesoreria)

        response_data = []

        try:
            with connections['zeussalud'].cursor() as cursor:
                for auditoria in queryset:
                    admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)
                    # [0]=con_estudio, [1]=num_id, [2]=EPSPaciente, [3]=NombreCompleto, [4]=nro_factura, [5]=tipoUsuario, [6]=fecha_ing

                    if admision_data:
                        if not codigo_entidad or codigo_entidad == admision_data[2]:
                            data = {
                                'AdmisionId': auditoria.AdmisionId,
                                'FechaCreacion': auditoria.FechaCreacion.strftime('%Y-%m-%d'),
                                'FechaCargueArchivo': auditoria.FechaCargueArchivo.strftime('%Y-%m-%d'),
                                'Observacion': auditoria.Observacion,
                                'RevisionCuentasMedicas': auditoria.RevisionCuentasMedicas,
                                'RevisionTesoreria': auditoria.RevisionTesoreria,
                                'Consecutivo': admision_data[0],
                                'IdPaciente': admision_data[1],
                                'CodigoEntidad': admision_data[2],
                                'NombreResponsable': admision_data[3],
                                'FacturaNo': admision_data[4],
                            }
                            response_data.append(data)
        except Exception as e:
            return Response({'error': str(e)}, status=500)

        # Filtrar la respuesta para que solo incluya entradas donde RevisionCuentasMedicas es True
        response_data = [item for item in response_data if item['RevisionCuentasMedicas']]

        return Response(response_data, status=200)


###### RADICACION - CUENTAS MEDICAS #####
@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_compensar_view(request, numero_admision, idusuario):
    try:
        # Verificar si el idusuario existe en la base de datos y obtener el nombre de usuario
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = user.username  # Obtener el nombre del usuario para usar en la carpeta
            
            # Sanitizar el nombre del usuario si es necesario
            nombre_usuario = "".join(c for c in nombre_usuario if c.isalnum() or c in (' ', '.', '_')).rstrip()

        except CustomUser.DoesNotExist:
            response_data = {
                "success": False,
                "detail": "Usuario no encontrado."
            }
            return JsonResponse(response_data, status=404)

        # Verificar si ya está radicado
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            response_data = {
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }
            return JsonResponse(response_data, status=400)

        # Obtener datos de la admisión
        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')

        if not factura_numero:
            response_data = {
                "success": False,
                "detail": "La admisión no tiene el número de factura."
            }
            return JsonResponse(response_data, status=400)

        # Obtener el régimen desde el primer archivo de facturación relacionado
        archivo_facturacion = ArchivoFacturacion.objects.filter(Admision_id=numero_admision).first()
        if not archivo_facturacion:
            response_data = {
                "success": False,
                "detail": f"No se encontró el archivo de facturación para la admisión {numero_admision}"
            }
            return JsonResponse(response_data, status=404)

        regimen = archivo_facturacion.Regimen
        if regimen == 'C':
            carpeta_tipo_archivo = 'CONTRIBUTIVO'
        elif regimen == 'S':
            carpeta_tipo_archivo = 'SUBSIDIADO'
        else:
            response_data = {
                "success": False,
                "detail": f"Régimen desconocido: {regimen}"
            }
            return JsonResponse(response_data, status=400)

        # Crear la ruta base para las carpetas utilizando el nombre del usuario
        fecha_actual = datetime.now().strftime('%Y%m%d')
        carpeta_usuario = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'COMPENSAR', fecha_actual, carpeta_tipo_archivo, nombre_usuario)
        if not os.path.exists(carpeta_usuario):
            try:
                os.makedirs(carpeta_usuario)
             
            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al crear la carpeta para el usuario: {str(e)}"
                }
                return JsonResponse(response_data, status=500)

        # Obtener archivos de la admisión
        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO']
        archivos_faltantes = []

        # Verificar la existencia de todos los archivos requeridos antes de copiar
        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = archivo.get('RutaArchivo')
                ruta_origen_relative = unquote(ruta_origen_relative)  # Decodificar URL

                # Normalizar y formar ruta de origen
                if ruta_origen_relative.startswith('C:'):
                    ruta_origen_relative = ruta_origen_relative[2:]

                ruta_origen_relative = ruta_origen_relative.replace("\\", "/").replace(settings.MEDIA_URL.lstrip('/'), '').lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))

  

                # Comprobación adicional de permisos
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)
                elif not os.access(ruta_origen, os.R_OK):
                    archivos_faltantes.append(f"{tipo_archivo} (no hay permisos de lectura)")

        if archivos_faltantes:
            response_data = {
                "success": False,
                "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"
            }
            return JsonResponse(response_data, status=400)

        archivos_copiados = []
        archivos_fallidos = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo not in archivos_requeridos:
                continue

            ruta_origen_relative = archivo.get('RutaArchivo')
            ruta_origen_relative = unquote(ruta_origen_relative)  # Decodificar URL

            # Normalizar y formar ruta de origen
            if ruta_origen_relative.startswith('C:'):
                ruta_origen_relative = ruta_origen_relative[2:]

            ruta_origen_relative = ruta_origen_relative.replace("\\", "/").replace(settings.MEDIA_URL.lstrip('/'), '').lstrip('/')
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))



            # Verificar la existencia del archivo
            if not os.path.exists(ruta_origen):
              
                archivos_fallidos.append(ruta_origen)
                continue

            # Formar la ruta de destino
            nombre_archivo = f"{tipo_archivo}{factura_numero}.pdf"
            ruta_destino = os.path.join(carpeta_usuario, nombre_archivo)
            try:
                shutil.copy(ruta_origen, ruta_destino)
                archivos_copiados.append(ruta_destino)

            except Exception as e:
                archivos_fallidos.append(ruta_destino)
         
        # Actualizar el campo Radicado en la tabla archivos
        actualizados = archivos_a_verificar.update(Radicado=True)
     

        response_data = {
            "success": True,
            "detail": f"Archivos copiados y carpetas creadas para la admisión con número {numero_admision}",
            "archivos_copiados": archivos_copiados,
            "archivos_fallidos": archivos_fallidos
        }
        return JsonResponse(response_data, status=200)

    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }
        return JsonResponse(response_data, status=404)
    except Exception as e:
        response_data = {
            "success": False,
            "detail": str(e)
        }
        return JsonResponse(response_data, status=500)
######## TABLA RADICACION######

class TablaRadicacion(APIView):
    def get(self, request):
        # Obtener parámetros de consulta
        codigo_entidad = request.query_params.get('CodigoEntidad', None)
        fecha_inicio = request.query_params.get('FechaInicio', None)
        fecha_fin = request.query_params.get('FechaFin', None)

        # Convertir fechas de inicio y fin a objetos datetime naive
        if fecha_inicio:
            fecha_inicio_dt = datetime.combine(parse_date(fecha_inicio), time.min)
        else:
            fecha_inicio_dt = None

        if fecha_fin:
            fecha_fin_dt = datetime.combine(parse_date(fecha_fin), time.max)
        else:
            fecha_fin_dt = None

        # Filtrar archivos de ArchivoFacturacion con Radicado=False y Tipo específico dentro del rango de fechas
        archivos_radicados_false = ArchivoFacturacion.objects.filter(
            Radicado=False,
            Tipo__in=['RESULTADO', 'HCNEURO', 'FACTURA']
        )
        if fecha_inicio_dt and fecha_fin_dt:
            archivos_radicados_false = archivos_radicados_false.filter(
                FechaCreacionAntares__range=(fecha_inicio_dt, fecha_fin_dt)
            )
        archivos_radicados_false = archivos_radicados_false.only(
            'Admision_id', 'FechaCreacionAntares', 'Radicado'
        )

        # Extraer los IDs de admisión de los archivos filtrados
        admision_ids = [archivo.Admision_id for archivo in archivos_radicados_false]
        if not admision_ids:
            return Response([])

        # Consultar AuditoriaCuentasMedicas solo para las admisiones filtradas
        auditorias = AuditoriaCuentasMedicas.objects.filter(
            AdmisionId__in=admision_ids,
            RevisionCuentasMedicas=True
        ).only('AdmisionId', 'FechaCreacion', 'FechaCargueArchivo', 'Observacion', 'RevisionCuentasMedicas')

        if not auditorias.exists():
            return Response([])

        # Consultar admisiones relacionadas en bloque usando los admision_ids obtenidos
        with connections['zeussalud'].cursor() as cursor:
            admisiones_data = get_admisiones_zeus_bulk(cursor, list(admision_ids))
        # admisiones_data: dict {con_estudio: (con_estudio, num_id, EPSPaciente, NombreCompleto, nro_factura, tipoUsuario, fecha_ing)}

        # Crear un diccionario para agrupar archivos por admisión
        archivos_por_admision = {archivo.Admision_id: archivo for archivo in archivos_radicados_false}

        # Construir la respuesta procesando solo las admisiones y auditorías relevantes
        response_data = []
        for auditoria in auditorias:
            admision = admisiones_data.get(auditoria.AdmisionId)
            archivo = archivos_por_admision.get(auditoria.AdmisionId)

            if admision and archivo:
                # Filtrar por CodigoEntidad, si es necesario
                if codigo_entidad and admision[2] != codigo_entidad:
                    continue

                # Construir el diccionario de respuesta
                response_data.append({
                    'AdmisionId': auditoria.AdmisionId,
                    'FechaCreacion': auditoria.FechaCreacion.strftime('%Y-%m-%d') if auditoria.FechaCreacion else None,
                    'FechaCargueArchivo': auditoria.FechaCargueArchivo.strftime('%Y-%m-%d') if auditoria.FechaCargueArchivo else None,
                    'Observacion': auditoria.Observacion,
                    'RevisionCuentasMedicas': auditoria.RevisionCuentasMedicas,
                    'Consecutivo': admision[0],
                    'IdPaciente': admision[1],
                    'CodigoEntidad': admision[2],
                    'NombreResponsable': admision[3],
                    'FacturaNo': admision[4],
                    'FechaCreacionAntares': archivo.FechaCreacionAntares.strftime('%Y-%m-%d') if archivo.FechaCreacionAntares else None,
                    'Radicado': archivo.Radicado
                })

        # Devolver los datos de respuesta
        return Response(response_data)

""" ####### SALUD TOTAL ###
@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_salud_total_view(request, numero_admision, idusuario):
    try:
        # Verificar si ya está radicado
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            response_data = {
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }
            return JsonResponse(response_data, status=400)

        # Obtener los datos de admisión
        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code == 200:
            if hasattr(admision_response, 'data') and isinstance(admision_response.data, dict):
                admision_data = admision_response.data.get('data')
                if isinstance(admision_data, dict):
                    factura_numero = admision_data.get('FacturaNo')
                    prefijo = admision_data.get('Prefijo') or ''  # Usa un valor predeterminado si es None

                    # Asegúrate de que prefijo sea una cadena válida
                    if prefijo is None:
                        prefijo = ''  # O asigna un valor por defecto si es necesario

                    if factura_numero is not None:
                        # Obtener los archivos de la admisión
                        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
                        if archivos_response.status_code == 200:
                            if hasattr(archivos_response, 'data') and isinstance(archivos_response.data, dict):
                                archivos_data = archivos_response.data.get('data', [])
                                if isinstance(archivos_data, list):
                                    # Verificar si el usuario existe y obtener el nombre
                                    try:
                                        user = CustomUser.objects.get(id=idusuario)
                                        nombre_usuario = user.username  # Obtener el nombre del usuario para usar en la carpeta
                                        # Sanitizar el nombre del usuario si es necesario
                                        nombre_usuario = "".join(c for c in nombre_usuario if c.isalnum() or c in (' ', '.', '_')).rstrip()
                                      
                                    except CustomUser.DoesNotExist:
                                        response_data = {
                                            "success": False,
                                            "detail": "Usuario no encontrado."
                                        }
                                        return JsonResponse(response_data, status=404)

                                    # Obtener la fecha de hoy para la carpeta
                                    fecha_hoy = datetime.now().strftime('%Y-%m-%d')

                                    # Crear la ruta completa usando el nombre de usuario
                                    carpeta_path = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'SALUDTOTAL', fecha_hoy, nombre_usuario)
                                    if not os.path.exists(carpeta_path):
                                        os.makedirs(carpeta_path)

                                    # Definir los tipos de archivos requeridos
                                    documentos_requeridos = {
                                        'FACTURA': 1,
                                        'AUTORIZACION': 17,
                                        'ORDEN': 5,
                                        'RESULTADO': 7,
                                        'COMPROBANTE': 15
                                    }

                                    # Verificar la presencia de todos los documentos requeridos
                                    tipos_archivos_presentes = {archivo.get('Tipo') for archivo in archivos_data}
                                    documentos_faltantes = [tipo for tipo in documentos_requeridos if tipo not in tipos_archivos_presentes]

                                    if documentos_faltantes:
                                        response_data = {
                                            "success": False,
                                            "detail": f"Faltan los siguientes documentos requeridos: {', '.join(documentos_faltantes)}"
                                        }
                                        return JsonResponse(response_data, status=400)

                                    # Procesar y copiar los archivos requeridos
                                    for archivo in archivos_data:
                                        tipo_archivo = archivo.get('Tipo')
                                        if tipo_archivo in documentos_requeridos:
                                            ruta_origen_relative = unquote(archivo.get('RutaArchivo'))
                                            numero_tipo_documento = documentos_requeridos[tipo_archivo]

                                            nombre_archivo = f"901119103_{prefijo}_{factura_numero}_{numero_tipo_documento}_1.pdf"
                                            print("Nombre de archivo:", nombre_archivo)

                                            # Normalizar la ruta y eliminar cualquier referencia redundante
                                            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative.replace(settings.MEDIA_URL, "").lstrip('/')))

                                            if os.path.exists(ruta_origen):
                                                ruta_destino = os.path.join(carpeta_path, nombre_archivo)
                                                shutil.copy(ruta_origen, ruta_destino)
                                                print("Archivo copiado exitosamente.")
                                            else:
                                                raise FileNotFoundError(f"La ruta de origen '{ruta_origen}' no es válida")

                                    # Verificar registros antes de la actualización
                               
                                    for archivo in archivos_a_verificar:
                                        print(f"Antes de la actualización - IdArchivo: {archivo.IdArchivo}, Radicado: {archivo.Radicado}")

                                    # Actualizar el campo Radicado en la tabla archivos
                                    actualizados = archivos_a_verificar.update(Radicado=True)
                                    print(f"Registros actualizados a Radicado=True: {actualizados}")

                                    # Verificar registros después de la actualización
                                    archivos_actualizados = ArchivoFacturacion.objects.filter(Admision_id=numero_admision, Radicado=True)
                                    for archivo in archivos_actualizados:
                                        print(f"Después de la actualización - IdArchivo: {archivo.IdArchivo}, Radicado: {archivo.Radicado}")

                                    response_data = {
                                        "success": True,
                                        "detail": f"Archivos copiados y carpetas creadas para la admisión con número {numero_admision}"
                                    }
                                    return JsonResponse(response_data, status=200)
                                else:
                                    raise ValueError("La respuesta de archivos no contiene una lista de datos válida.")
                            else:
                                raise ValueError("La respuesta de archivos no contiene datos válidos.")
                        else:
                            return archivos_response
                    else:
                        raise ValueError("La admisión no tiene el número de factura.")
                else:
                    raise ValueError("Los datos de admisión no están en el formato esperado.")
            else:
                raise ValueError("La respuesta de admisión no contiene datos válidos.")
        else:
            return admision_response
    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }
        return JsonResponse(response_data, status=404)
    except Exception as e:
        response_data = {
            "success": False,
            "detail": str(e)
        }
        return JsonResponse(response_data, status=500) """




@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_salud_total_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_prefijo_numero_factura = f"{prefijo}{factura_numero}"
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'SALUD TOTAL', carpeta_tipo_archivo, fecha_hoy, nombre_usuario, carpeta_prefijo_numero_factura)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        RENOMBRAR_TIPOS = {
            "FACTURA": lambda p, n: f"FEV_901119103_{p}{n}.pdf",
            "COMPROBANTE": lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO": lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN": lambda p, n: f"OPF_901119103_{p}{n}.pdf",
            "AUTORIZACION": lambda p, n: f"PDE_901119103_{p}{n}.pdf",
        }

        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen):
                raise FileNotFoundError(f"No se encontró el archivo {tipo} en {ruta_origen}")

            nuevo_nombre = RENOMBRAR_TIPOS.get(tipo, lambda p, n: os.path.basename(ruta_origen))(prefijo, factura_numero)
            ruta_destino_archivo = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
            shutil.copy(ruta_origen, ruta_destino_archivo)

        carpeta_json_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        json_temp, txt_temp = None, None

        if os.path.exists(carpeta_json_txt):
            for archivo_nombre in os.listdir(carpeta_json_txt):
                ruta_origen = os.path.join(carpeta_json_txt, archivo_nombre)
                if archivo_nombre.lower().endswith('.json'):
                    with open(ruta_origen, 'r', encoding='utf-8') as f:
                        contenido_json = f.read()
                    contenido_stripped = contenido_json.lstrip()
                    if contenido_stripped.startswith('{"numDocumentoIdObligado') or \
                       os.path.splitext(archivo_nombre)[0].upper() == f'FES{factura_numero}'.upper():
                        json_temp = ruta_origen
                elif archivo_nombre.lower().endswith('.txt'):
                    txt_temp = ruta_origen
        else:
            print(f"No se encontró la carpeta de origen {carpeta_json_txt} para los archivos .json y .txt y xml")

        if not json_temp or not txt_temp:
            return JsonResponse({
                "success": False,
                "detail": "No se encontraron ambos archivos necesarios (RIPS.json y ResultadosMSP.txt) para generar el ZIP."
            }, status=400)

        zip_path = os.path.join(carpeta_nombre_archivo, f"901119103_FES{factura_numero}.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            zipf.write(json_temp, arcname=f"901119103_FES{factura_numero}_CUV.json")
            zipf.write(txt_temp, arcname=f"901119103_FES{factura_numero}_RIPS.json")

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y renombrados correctamente en {carpeta_nombre_archivo}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)









#### SANITAS EVENTO


""" @api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_sanitas_evento_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_prefijo_numero_factura = f"{prefijo}{factura_numero}"
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'SAN01', carpeta_tipo_archivo, fecha_hoy, nombre_usuario, carpeta_prefijo_numero_factura)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        # Reglas de renombramiento
        RENOMBRAR_TIPOS = {
            "FACTURA": lambda p, n: f"FEV_{p}{n}.pdf",
            "COMPROBANTE": lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO": lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN": lambda p, n: f"OPF_901119103_{p}{n}.pdf",
            "AUTORIZACION": lambda p, n: f"PEV_901119103_{p}{n}.pdf",
        }

        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen):
                raise FileNotFoundError(f"No se encontró el archivo {tipo} en {ruta_origen}")

            nuevo_nombre = RENOMBRAR_TIPOS.get(tipo, lambda p, n: os.path.basename(ruta_origen))(prefijo, factura_numero)
            ruta_destino_archivo = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
            shutil.copy(ruta_origen, ruta_destino_archivo)

        # === COPIA DE ARCHIVOS JSON Y TXT ADICIONALES ===
        carpeta_json_txt = os.path.join(
            '/mnt/prueba/DocsFESIESA',
            f'Factura-FES{factura_numero}'
        )

        if os.path.exists(carpeta_json_txt):
            for archivo_nombre in os.listdir(carpeta_json_txt):
                if archivo_nombre.lower().endswith('.json') or archivo_nombre.lower().endswith('.txt'):
                    ruta_archivo_origen = os.path.join(carpeta_json_txt, archivo_nombre)
                    ruta_archivo_destino = os.path.join(carpeta_nombre_archivo, archivo_nombre)
                    shutil.copy(ruta_archivo_origen, ruta_archivo_destino)
                    print(f"Copiado adicional: {archivo_nombre} → {ruta_archivo_destino}")
        else:
            print(f"No se encontró la carpeta de origen {carpeta_json_txt} para los archivos .json y .txt")

        actualizados = archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y renombrados correctamente en {carpeta_nombre_archivo}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)
 """




@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario

def radicar_sanitas_evento_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO',  'HCLINICA']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_prefijo_numero_factura = f"{prefijo}{factura_numero}"
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'SAN01', carpeta_tipo_archivo, fecha_hoy, nombre_usuario, carpeta_prefijo_numero_factura)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        tipos_presentes = {archivo.get("Tipo") for archivo in archivos_data}
        if "RESULTADO" not in tipos_presentes:
            if "HCNEURO" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCNEURO":
                        archivo["Tipo"] = "RESULTADO"
                        print("Sustituyendo HCNEURO por RESULTADO")
            elif "HCLINICA" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCLINICA":
                        archivo["Tipo"] = "RESULTADO"
                        print("Sustituyendo HCLINICA por RESULTADO")

        # Reglas de renombramiento
        RENOMBRAR_TIPOS = {
            "FACTURA": lambda p, n: f"FEV_{p}{n}.pdf",
            "COMPROBANTE": lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO": lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN": lambda p, n: f"OPF_901119103_{p}{n}.pdf",
           "AUTORIZACION": lambda p, n: f"PDE_901119103_{p}{n}.pdf",
        }

        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen):
                print(f"Archivo omitido: {tipo} no encontrado en {ruta_origen}")
                continue

            if tipo not in RENOMBRAR_TIPOS:
                print(f"Tipo de archivo omitido: {tipo}")
                continue

            nuevo_nombre = RENOMBRAR_TIPOS.get(tipo)(prefijo, factura_numero)
            ruta_destino_archivo = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
            shutil.copy(ruta_origen, ruta_destino_archivo)

        # === COPIA DE ARCHIVOS JSON, TXT Y XML ADICIONALES ===
        carpeta_json_txt = os.path.join(
            '/mnt/prueba/DocsFESIESA',
            f'Factura-FES{factura_numero}'
        )

        if os.path.exists(carpeta_json_txt):
            for archivo_nombre in os.listdir(carpeta_json_txt):
                extension = os.path.splitext(archivo_nombre)[1].lower()
                if extension in ('.json', '.txt',):
                    ruta_archivo_origen = os.path.join(carpeta_json_txt, archivo_nombre)
                    nombre_sin_extension = os.path.splitext(archivo_nombre)[0]
                    nuevo_nombre = f"{nombre_sin_extension}{extension}"
                    ruta_archivo_destino = os.path.join(carpeta_nombre_archivo, nuevo_nombre)

                    if os.path.exists(ruta_archivo_origen):
                        shutil.copy(ruta_archivo_origen, ruta_archivo_destino)
                        print(f"Copiado adicional: {archivo_nombre} → {ruta_archivo_destino}")
                    else:
                        print(f"⚠️ Archivo no encontrado: {ruta_archivo_origen}")
        else:
            print(f"No se encontró la carpeta de origen {carpeta_json_txt} para los archivos .json, .txt ")

        actualizados = archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y renombrados correctamente en {carpeta_nombre_archivo}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)
      
      

      
      

# === RADICACION EJERCITO ===
@api_view(['GET'])
@permission_classes([AllowAny])
def radicar_ejercito_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO', 'HCLINICA']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_prefijo_numero_factura = f"{prefijo}{factura_numero}"
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'EJERCITO', carpeta_tipo_archivo, fecha_hoy, nombre_usuario, carpeta_prefijo_numero_factura)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        tipos_presentes = {archivo.get("Tipo") for archivo in archivos_data}
        if "RESULTADO" not in tipos_presentes:
            if "HCNEURO" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCNEURO":
                        archivo["Tipo"] = "RESULTADO"
            elif "HCLINICA" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCLINICA":
                        archivo["Tipo"] = "RESULTADO"

        RENOMBRAR_TIPOS = {
            "FACTURA":      lambda p, n: f"FEV_901119103_{p}{n}.pdf",
            "COMPROBANTE":  lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO":    lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN":        lambda p, n: f"OPF_901119103_{p}{n}.pdf",
            "AUTORIZACION": lambda p, n: f"PDE_901119103_{p}{n}.pdf",
        }

        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen) or tipo not in RENOMBRAR_TIPOS:
                continue
            nuevo_nombre = RENOMBRAR_TIPOS[tipo](prefijo, factura_numero)
            ruta_destino_archivo = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
            shutil.copy(ruta_origen, ruta_destino_archivo)

        carpeta_json_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')

        if os.path.exists(carpeta_json_txt):
            for archivo_nombre in os.listdir(carpeta_json_txt):
                extension = os.path.splitext(archivo_nombre)[1].lower()
                ruta_archivo_origen = os.path.join(carpeta_json_txt, archivo_nombre)

                if extension == '.xml':
                    nuevo_nombre = f"XML_901119103_{prefijo}{factura_numero}.xml"
                    ruta_archivo_destino = os.path.join(carpeta_nombre_archivo, nuevo_nombre)

                elif extension == '.json':
                    with open(ruta_archivo_origen, 'r', encoding='utf-8') as f:
                        contenido_json = f.read()
                    contenido_stripped = contenido_json.lstrip()
                    if not (contenido_stripped.startswith('{"numDocumentoIdObligado') or
                            os.path.splitext(archivo_nombre)[0].upper() == f'FES{factura_numero}'.upper()):
                        continue
                    nuevo_nombre = f"RIPS_901119103_FES{factura_numero}.json"
                    ruta_archivo_destino = os.path.join(carpeta_nombre_archivo, nuevo_nombre)

                elif extension == '.txt':
                    nuevo_nombre = f"CUV_901119103_FES{factura_numero}.json"
                    ruta_archivo_destino = os.path.join(carpeta_nombre_archivo, nuevo_nombre)

                else:
                    continue

                if os.path.exists(ruta_archivo_origen):
                    shutil.copy(ruta_archivo_origen, ruta_archivo_destino)
                else:
                    print(f"Archivo no encontrado: {ruta_archivo_origen}")
        else:
            print(f"No se encontró la carpeta {carpeta_json_txt}")

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y renombrados correctamente en {carpeta_nombre_archivo}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)
      
      


# === RADICACION FOMAG ===

@api_view(['GET'])
@permission_classes([AllowAny])  
def radicar_fomag_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO', 'HCLINICA']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')

        # Estructura de carpetas
        carpeta_usuario  = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'FOM01', fecha_hoy, nombre_usuario)
        carpeta_factura  = os.path.join(carpeta_usuario, f'FES{factura_numero}')
        carpeta_soportes = os.path.join(carpeta_factura, 'SOPORTES')
        carpeta_json     = os.path.join(carpeta_factura, 'JSON_901119103')
        os.makedirs(carpeta_soportes, exist_ok=True)
        os.makedirs(carpeta_json,     exist_ok=True)

        tipos_presentes = {archivo.get("Tipo") for archivo in archivos_data}
        if "RESULTADO" not in tipos_presentes:
            if "HCNEURO" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCNEURO":
                        archivo["Tipo"] = "RESULTADO"
            elif "HCLINICA" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCLINICA":
                        archivo["Tipo"] = "RESULTADO"

        RENOMBRAR_TIPOS = {
            "FACTURA":      lambda p, n: f"FEV_901119103_{p}{n}.pdf",
            "COMPROBANTE":  lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO":    lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN":        lambda p, n: f"OPF_901119103_{p}{n}.pdf",
            "AUTORIZACION": lambda p, n: f"PDE_901119103_{p}{n}.pdf",
        }

        # Copiar PDFs a carpeta SOPORTES
        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen) or tipo not in RENOMBRAR_TIPOS:
                continue
            nuevo_nombre = RENOMBRAR_TIPOS[tipo](prefijo, factura_numero)
            destino = os.path.join(carpeta_soportes, nuevo_nombre)
            shutil.copy(ruta_origen, destino)

        carpeta_json_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        archivo_txt_json = None
        archivo_rips_json = None
        archivo_xml = None

        if not os.path.exists(carpeta_json_txt):
            return JsonResponse({"success": False, "detail": f"No se encontró la carpeta {carpeta_json_txt}"}, status=400)

        # Copiar JSON / TXT / XML a carpeta JSON_901119103
        # DocsFESIESA contiene:
        #   FES{n}.json              → RIPS  (empieza con "numDocumentoIdObligado")
        #   ResultadosIMSPS_*.json   → ResultadosMSP (empieza con "ResultState")
        #   ResultadosIMSPS_*.txt    → mismo ResultadosMSP en texto (respaldo)
        #   FES{n}.xml               → XML de factura electrónica (opcional)
        for archivo_nombre in os.listdir(carpeta_json_txt):
            extension = os.path.splitext(archivo_nombre)[1].lower()
            ruta_origen = os.path.join(carpeta_json_txt, archivo_nombre)

            if extension == '.json':
                with open(ruta_origen, 'r', encoding='utf-8') as f:
                    contenido_json = f.read()
                contenido_stripped = contenido_json.lstrip()

                if contenido_stripped.startswith('{"numDocumentoIdObligado') or \
                   os.path.splitext(archivo_nombre)[0].upper() == f'FES{factura_numero}'.upper():
                    # Es el archivo RIPS
                    nuevo_nombre = f"RIPS_901119103_FES{factura_numero}.json"
                    archivo_rips_json = os.path.join(carpeta_json, nuevo_nombre)
                    shutil.copy(ruta_origen, archivo_rips_json)

                elif contenido_stripped.startswith('{"ResultState') or 'Resultados' in archivo_nombre:
                    # Es el archivo ResultadosMSP
                    match = re.search(r'"ProcesoId"\s*:\s*"?(?P<id>\d+)', contenido_json)
                    if match:
                        id_proceso = match.group('id')
                        nombre_txt = f"ResultadosMSP_FES{factura_numero}_ID{id_proceso}_A_CUV.json"
                        archivo_txt_json = os.path.join(carpeta_json, nombre_txt)
                        with open(archivo_txt_json, 'w', encoding='utf-8') as f:
                            f.write(contenido_json)

            elif extension == '.txt':
                # Sólo se usa si aún no se encontró el ResultadosMSP vía .json
                if not archivo_txt_json:
                    with open(ruta_origen, 'r', encoding='utf-8') as f:
                        contenido = f.read()
                    match = re.search(r'"ProcesoId"\s*:\s*"?(?P<id>\d+)', contenido)
                    if match:
                        id_proceso = match.group('id')
                        nombre_txt = f"ResultadosMSP_FES{factura_numero}_ID{id_proceso}_A_CUV.json"
                        archivo_txt_json = os.path.join(carpeta_json, nombre_txt)
                        with open(archivo_txt_json, 'w', encoding='utf-8') as f:
                            f.write(contenido)

            elif extension == '.xml':
                nuevo_nombre_xml = f"FEV_901119103_{factura_numero}.xml"
                archivo_xml = os.path.join(carpeta_json, nuevo_nombre_xml)
                shutil.copy(ruta_origen, archivo_xml)

        faltantes = []
        if not archivo_rips_json:
            faltantes.append("RIPS (JSON con numDocumentoIdObligado)")
        if not archivo_txt_json:
            faltantes.append("ResultadosMSP (JSON/TXT con ResultState)")
        if faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan archivos en DocsFESIESA: {', '.join(faltantes)}"}, status=400)

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos radicados en {carpeta_factura}",
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)



##### COLSANITAS###
@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_colsanitas_view(request, numero_admision, idusuario):
    try:
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        cuv = 'CUV'  # Asignar CUV como texto fijo

        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura.")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])

        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_path = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'COLSANITAS', fecha_hoy, nombre_usuario)
        os.makedirs(carpeta_path, exist_ok=True)

        documentos_requeridos = {'FACTURA', 'RESULTADO', 'AUTORIZACION', 'COMPROBANTE', 'ORDEN'}
        tipos_archivos_presentes = {archivo.get('Tipo') for archivo in archivos_data}
        documentos_faltantes = documentos_requeridos - tipos_archivos_presentes
        if documentos_faltantes:
            return JsonResponse({
                "success": False,
                "detail": f"Faltan los siguientes documentos requeridos: {', '.join(documentos_faltantes)}"
            }, status=400)

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))

            if tipo_archivo == 'FACTURA':
                nombre_archivo = f"{prefijo}{factura_numero}.pdf"
            else:
                sop = {
                    'COMPROBANTE': 'SOP_1',
                    'AUTORIZACION': 'SOP_2',
                    'ORDEN': 'SOP_3',
                    'ADICIONALES': 'SOP_4',
                    'RESULTADO': 'SOP_5',
                    'HCNEURO': 'SOP_6'
                }.get(tipo_archivo, 'OTRO')
                nombre_archivo = f"{prefijo}{factura_numero}_{sop}.pdf"

            if 'media/media' in ruta_origen:
                ruta_origen = ruta_origen.replace('media/media', 'media')

            if os.path.exists(ruta_origen):
                ruta_destino = os.path.join(carpeta_path, nombre_archivo)
                shutil.copy(ruta_origen, ruta_destino)
                print(f"Archivo copiado: {nombre_archivo}")
            else:
                raise FileNotFoundError(f"La ruta de origen '{ruta_origen}' no es válida")

        # === PROCESAR ARCHIVO .TXT ADICIONAL ===
        carpeta_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        if os.path.exists(carpeta_txt):
            print(f"[\u2714] Carpeta TXT encontrada: {carpeta_txt}")
            for archivo_nombre in os.listdir(carpeta_txt):
                if archivo_nombre.lower().endswith('.txt') and f'FES{factura_numero}' in archivo_nombre:
                    ruta_txt_origen = os.path.join(carpeta_txt, archivo_nombre)
                    print(f"[\u2714] Archivo TXT encontrado: {ruta_txt_origen}")
                    try:
                        with open(ruta_txt_origen, 'r', encoding='utf-8') as f:
                            contenido = f.read()

                        match = re.search(r'"ProcesoId"\s*:\s*"?(?P<id>\w+)"?', contenido, re.IGNORECASE)
                        if match:
                            proceso_id = match.group('id')
                            nombre_txt = f"ResultadosMSPS_{prefijo}{factura_numero}_ID{proceso_id}_A_{cuv}.txt"
                            print(f"[\u2714] ProcesoId extraído: {proceso_id}")
                        else:
                            print("[\u26a0\ufe0f] ProcesoId no encontrado. Usando nombre genérico.")
                            nombre_txt = f"ResultadosMSPS_{prefijo}{factura_numero}_A_{cuv}.txt"

                        ruta_txt_destino = os.path.join(carpeta_path, nombre_txt)
                        shutil.copy(ruta_txt_origen, ruta_txt_destino)
                        print(f"[\ud83d\udcc1] Archivo copiado correctamente: {ruta_txt_destino}")
                        break

                    except Exception as e:
                        print(f"[\u274c] Error procesando archivo TXT: {e}")
        else:
            print(f"[\u274c] Carpeta TXT no encontrada: {carpeta_txt}")

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y carpeta generada en {carpeta_path}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }, status=404)

    except Exception as e:
        return JsonResponse({
            "success": False,
            "detail": str(e)
        }, status=500)

      


import re    
      
##### MEDISANITAS###
@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_mes01_view(request, numero_admision, idusuario):
    try:
        # Verificar si ya está radicado
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }, status=400)

        # Obtener los datos de admisión
        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        cuv = 'CUV' 

        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura.")

        # Obtener los archivos de la admisión
        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])

        # Verificar si el usuario existe
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        # Crear carpeta de destino
        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_path = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'MEDISANITAS', fecha_hoy, nombre_usuario)
        os.makedirs(carpeta_path, exist_ok=True)

        # Validar archivos requeridos
        documentos_requeridos = {'FACTURA', 'RESULTADO', 'AUTORIZACION', 'COMPROBANTE', 'ORDEN'}
        tipos_archivos_presentes = {archivo.get('Tipo') for archivo in archivos_data}
        documentos_faltantes = documentos_requeridos - tipos_archivos_presentes
        if documentos_faltantes:
            return JsonResponse({
                "success": False,
                "detail": f"Faltan los siguientes documentos requeridos: {', '.join(documentos_faltantes)}"
            }, status=400)

        # Copiar y renombrar archivos PDF
        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))

            if tipo_archivo == 'FACTURA':
                nombre_archivo = f"{prefijo}{factura_numero}.pdf"
            else:
                sop = {
                    'COMPROBANTE': 'SOP_1',
                    'AUTORIZACION': 'SOP_2',
                    'ORDEN': 'SOP_3',
                    'ADICIONALES': 'SOP_4',
                    'RESULTADO': 'SOP_5',
                    'HCNEURO': 'SOP_6'
                }.get(tipo_archivo, 'OTRO')

                nombre_archivo = f"{prefijo}{factura_numero}_{sop}.pdf"

            if 'media/media' in ruta_origen:
                ruta_origen = ruta_origen.replace('media/media', 'media')

            if os.path.exists(ruta_origen):
                ruta_destino = os.path.join(carpeta_path, nombre_archivo)
                shutil.copy(ruta_origen, ruta_destino)
                print(f"Archivo copiado: {nombre_archivo}")
            else:
                raise FileNotFoundError(f"La ruta de origen '{ruta_origen}' no es válida")

        # === PROCESAR ARCHIVO .TXT ADICIONAL ===
        carpeta_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        if os.path.exists(carpeta_txt):
            for archivo_nombre in os.listdir(carpeta_txt):
                if archivo_nombre.lower().endswith('.txt'):
                    ruta_txt_origen = os.path.join(carpeta_txt, archivo_nombre)
                    try:
                        with open(ruta_txt_origen, 'r', encoding='utf-8') as f:
                            contenido = f.read()

                        match = re.search(r'"ProcesoId"\s*:\s*"?(?P<id>\w+)"?', contenido)
                        if not match:
                            print("⚠️ Campo 'ProcesoId' no encontrado en el archivo .txt")
                            continue

                        proceso_id = match.group('id')
                        nombre_txt = f"ResultadosMSPS_{prefijo}{factura_numero}_ID{proceso_id}_A_{cuv}.txt"
                        ruta_txt_destino = os.path.join(carpeta_path, nombre_txt)
                        shutil.copy(ruta_txt_origen, ruta_txt_destino)
                        print(f"Archivo .txt copiado como: {nombre_txt}")
                        break
                    except Exception as e:
                        print(f"Error procesando archivo .txt: {e}")
        else:
            print(f"No se encontró la carpeta TXT: {carpeta_txt}")

        # Marcar como radicado
        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y carpeta generada en {carpeta_path}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }, status=404)

    except Exception as e:
        return JsonResponse({
            "success": False,
            "detail": str(e)
        }, status=500)

      
      
    
##CAPITAL SALUD#####




@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_capitalsalud_view(request, numero_admision, idusuario):

    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        archivos_requeridos = ['FACTURA', 'COMPROBANTE', 'ORDEN', 'RESULTADO', 'AUTORIZACION', 'HCNEURO', 'HCLINICA']
        archivos_faltantes = []

        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            if tipo_archivo in archivos_requeridos:
                ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, "").lstrip('/')
                ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
                if not os.path.exists(ruta_origen):
                    archivos_faltantes.append(tipo_archivo)

        if archivos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes archivos requeridos: {', '.join(archivos_faltantes)}"}, status=400)

        archivo_facturacion = archivos_a_verificar.first()
        if not archivo_facturacion:
            raise FileNotFoundError(f"No se encontró el archivo de facturación para la admisión {numero_admision}")

        regimen = archivo_facturacion.Regimen
        carpeta_tipo_archivo = 'CONTRIBUTIVO' if regimen == 'C' else 'SUBSIDIADO' if regimen == 'S' else None
        if carpeta_tipo_archivo is None:
            raise ValueError(f"Regimen desconocido: {regimen}")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_prefijo_numero_factura = f"{prefijo}{factura_numero}"
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'CAP01', carpeta_tipo_archivo, fecha_hoy, nombre_usuario, carpeta_prefijo_numero_factura)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        tipos_presentes = {archivo.get("Tipo") for archivo in archivos_data}
        if "RESULTADO" not in tipos_presentes:
            if "HCNEURO" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCNEURO":
                        archivo["Tipo"] = "RESULTADO"
                        print("Sustituyendo HCNEURO por RESULTADO")
            elif "HCLINICA" in tipos_presentes:
                for archivo in archivos_data:
                    if archivo.get("Tipo") == "HCLINICA":
                        archivo["Tipo"] = "RESULTADO"
                        print("Sustituyendo HCLINICA por RESULTADO")

        RENOMBRAR_TIPOS = {
            "FACTURA": lambda p, n: f"FEV_901119103_{p}{n}.pdf",
            "COMPROBANTE": lambda p, n: f"CRC_901119103_{p}{n}.pdf",
            "RESULTADO": lambda p, n: f"HEV_901119103_{p}{n}.pdf",
            "ORDEN": lambda p, n: f"OPF_901119103_{p}{n}.pdf",
            "AUTORIZACION": lambda p, n: f"PDE_901119103_{p}{n}.pdf",
        }

        archivos_para_zip = []
        for archivo in archivos_data:
            tipo = archivo.get("Tipo")
            ruta_origen_relative = unquote(archivo.get("RutaArchivo")).replace(settings.MEDIA_URL, "").lstrip("/")
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if not os.path.exists(ruta_origen):
                print(f"Archivo omitido: {tipo} no encontrado en {ruta_origen}")
                continue

            if tipo not in RENOMBRAR_TIPOS:
                print(f"Tipo de archivo omitido: {tipo}")
                continue

            nuevo_nombre = RENOMBRAR_TIPOS.get(tipo)(prefijo, factura_numero)
            destino_temporal = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
            shutil.copy(ruta_origen, destino_temporal)
            archivos_para_zip.append(destino_temporal)

        carpeta_json_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        archivo_txt = None
        if os.path.exists(carpeta_json_txt):
            for archivo_nombre in os.listdir(carpeta_json_txt):
                extension = os.path.splitext(archivo_nombre)[1].lower()
                ruta_archivo_origen = os.path.join(carpeta_json_txt, archivo_nombre)
                if extension == '.json':
                    with open(ruta_archivo_origen, 'r', encoding='utf-8') as f:
                        contenido_json = f.read()
                    contenido_stripped = contenido_json.lstrip()
                    if not (contenido_stripped.startswith('{"numDocumentoIdObligado') or
                            os.path.splitext(archivo_nombre)[0].upper() == f'FES{factura_numero}'.upper()):
                        continue
                    nuevo_nombre = f"901119103_FES{factura_numero}.json"
                    shutil.copy(ruta_archivo_origen, os.path.join(carpeta_nombre_archivo, nuevo_nombre))
                elif extension == '.txt':
                    nuevo_nombre = f"ResultadosLocales_FES{factura_numero}.txt"
                    archivo_txt = os.path.join(carpeta_nombre_archivo, nuevo_nombre)
                    shutil.copy(ruta_archivo_origen, archivo_txt)
                elif extension == '.xml':
                    destino = os.path.join(carpeta_nombre_archivo, os.path.basename(ruta_archivo_origen))
                    shutil.copy(ruta_archivo_origen, destino)
                    archivos_para_zip.append(destino)
        else:
            print(f"No se encontró la carpeta de origen {carpeta_json_txt} para los archivos .json, .txt y .xml")

        zip_name = f"FEV_901119103_{prefijo}{factura_numero}.zip"
        zip_path = os.path.join(carpeta_nombre_archivo, zip_name)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in archivos_para_zip:
                zipf.write(file, arcname=os.path.basename(file))
                os.remove(file)

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos copiados y organizados correctamente en {carpeta_nombre_archivo}"
        }, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)






###### RADICAR SAN02#####

@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_san02_view(request, numero_admision, idusuario):
    try:
        # Verificar si el idusuario existe en la base de datos y obtener el nombre de usuario
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = user.username  # Obtener el nombre del usuario para usar en la carpeta
        except CustomUser.DoesNotExist:
            response_data = {
                "success": False,
                "detail": "Usuario no encontrado."
            }
            return JsonResponse(response_data, status=404)

        # Verificar si ya está radicado
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            response_data = {
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }
            return JsonResponse(response_data, status=400)

        # Obtener los datos de admisión
        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code == 200:
            admision_data = admision_response.data.get('data')
            factura_numero = admision_data.get('FacturaNo')
            prefijo = admision_data.get('Prefijo')
            codigo_entidad = admision_data.get('CodigoEntidad')

            if factura_numero is not None:
                # Obtener los archivos de la admisión
                archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
                if archivos_response.status_code == 200:
                    archivos_data = archivos_response.data.get('data', [])
                    factura_archivo = next((archivo for archivo in archivos_data if archivo.get('Tipo') == 'FACTURA'), None)

                    if factura_archivo:
                        ruta_origen_relative = unquote(factura_archivo.get('RutaArchivo'))
                        ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative.replace(settings.MEDIA_URL, "")))
                        print(f"Ruta del archivo de factura: {ruta_origen}")

                        if not os.path.exists(ruta_origen):
                            response_data = {
                                "success": False,
                                "detail": f"No se encontró el archivo de tipo FACTURA en {ruta_origen}"
                            }
                            return JsonResponse(response_data, status=404)

                        # Crear un nuevo documento PDF
                        merger = PdfMerger()
                        merger.append(ruta_origen)

                        # Agregar los demás archivos al nuevo documento
                        tipos_requeridos = ['COMPROBANTE', 'ORDEN', 'HCNEURO', 'AUTORIZACION', 'ADICIONALES', 'RESULTADO']
                        for tipo in tipos_requeridos:
                            for archivo in archivos_data:
                                if archivo.get('Tipo') == tipo:
                                    ruta_origen_relative = unquote(archivo.get('RutaArchivo'))
                                    ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative.replace(settings.MEDIA_URL, "")))
                                    print(f"Ruta del archivo {archivo.get('Tipo')}: {ruta_origen}")

                                    if os.path.exists(ruta_origen):
                                        merger.append(ruta_origen)
                                    else:
                                        print(f"No se encontró el archivo {archivo.get('Tipo')} en {ruta_origen}")

                        # Obtener el régimen de la admisión desde el primer archivo asociado
                        archivo_facturacion = ArchivoFacturacion.objects.filter(Admision_id=numero_admision).first()
                        if not archivo_facturacion:
                            response_data = {
                                "success": False,
                                "detail": f"No se encontró el archivo de facturación para la admisión {numero_admision}"
                            }
                            return JsonResponse(response_data, status=404)

                        regimen = archivo_facturacion.Regimen
                        if regimen == 'C':
                            carpeta_tipo_archivo = 'CONTRIBUTIVO'
                        elif regimen == 'S':
                            carpeta_tipo_archivo = 'SUBSIDIADO'
                        else:
                            response_data = {
                                "success": False,
                                "detail": f"Régimen desconocido: {regimen}"
                            }
                            return JsonResponse(response_data, status=400)

                        # Crear la ruta completa del archivo destino
                        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
                        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'SAN02', carpeta_tipo_archivo, nombre_usuario)
                        if not os.path.exists(carpeta_nombre_archivo):
                            os.makedirs(carpeta_nombre_archivo)

                        ruta_destino_merged = os.path.join(carpeta_nombre_archivo, f"{prefijo}{factura_numero}.pdf")
                        merger.write(ruta_destino_merged)
                        merger.close()

                        # Verificar registros antes de la actualización
                        print(f"Registros encontrados para actualizar: {archivos_a_verificar.count()}")
                        for archivo in archivos_a_verificar:
                            print(f"Antes de la actualización - IdArchivo: {archivo.IdArchivo}, Radicado: {archivo.Radicado}")

                        # Actualizar el campo Radicado en la tabla archivos
                        actualizados = archivos_a_verificar.update(Radicado=True)
                        print(f"Registros actualizados a Radicado=True: {actualizados}")

                        # Verificar registros después de la actualización
                        archivos_actualizados = ArchivoFacturacion.objects.filter(Admision_id=numero_admision, Radicado=True)
                        for archivo in archivos_actualizados:
                            print(f"Después de la actualización - IdArchivo: {archivo.IdArchivo}, Radicado: {archivo.Radicado}")

                        response_data = {
                            "success": True,
                            "detail": f"Archivos combinados en un solo documento y guardados en {ruta_destino_merged}"
                        }
                        return JsonResponse(response_data, status=200)
                    else:
                        response_data = {
                            "success": False,
                            "detail": "No se encontró el archivo de tipo FACTURA para la admisión"
                        }
                        return JsonResponse(response_data, status=404)
                else:
                    return archivos_response
            else:
                response_data = {
                    "success": False,
                    "detail": "La admisión no tiene el número de factura o el tipo de régimen"
                }
                return JsonResponse(response_data, status=400)
        else:
            return admision_response
    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }
        return JsonResponse(response_data, status=404)
    except Exception as e:
        response_data = {
            "success": False,
            "detail": str(e)
        }
        return JsonResponse(response_data, status=500)
##### OTROS #############

def limpiar_nombre_archivo(nombre):
    nombre = unquote(nombre)  # Decodificar URL
    nombre = nombre.replace("%20", " ")  # Reemplazar codificaciones de espacio por espacios
    return nombre


@api_view(['GET'])
@permission_classes([AllowAny])  # Permitir acceso a cualquier usuario
def radicar_other_view(request, numero_admision, idusuario):
    try:
        # Verificar si ya está radicado
        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            response_data = {
                "success": False,
                "detail": f"La admisión con número {numero_admision} ya está radicada."
            }
            return JsonResponse(response_data, status=400)

        # Obtener los datos de admisión
        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        contrato_alias = admision_data.get('ContratoAlias') or ''

        if not factura_numero:
            raise ValueError("La admisión no tiene el número de factura.")

        nombre_archivo = f"{prefijo}{factura_numero}"

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])

        documentos_requeridos = {'FACTURA', 'RESULTADO'}
        tipos_archivos_presentes = {archivo.get('Tipo') for archivo in archivos_data}
        documentos_faltantes = documentos_requeridos - tipos_archivos_presentes

        if documentos_faltantes:
            return JsonResponse({
                "success": False,
                "detail": f"Faltan los siguientes documentos requeridos: {', '.join(documentos_faltantes)}"
            }, status=400)

        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        merger = PdfMerger()
        for archivo in archivos_data:
            tipo_archivo = archivo.get('Tipo')
            ruta_origen_relative = unquote(archivo.get('RutaArchivo'))
            ruta_origen_relative = ruta_origen_relative.replace(settings.MEDIA_URL, "").lstrip('/')
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if os.path.exists(ruta_origen):
                merger.append(ruta_origen)
            else:
                raise FileNotFoundError(f"No se encontró el archivo {tipo_archivo} en {ruta_origen}")

        carpetas_por_alias = {
            "AIR LIQUIDE COLOMBIA": "AIR01",
            "ARL POSITIVA": "ARL01",
            "AXA COLPATRIA SEGUROS S.A.": "AXA01",
            "EQUIPO INTERDISCIPLINARIO PARA EL MEJORAMIENTO DE LA CALIDAD": "EQUIPO",
            "BOLIVAR POLIZA": "BOL01",
            "BOLIVAR SOAT": "BOL01",
            "EQUIVIDA": "EQV01",
            "IPS CONSULTORIO MEDICO SALUD OCUPACIONAL S.A.S": "IPS01",
            "IPS ONCOLIFE": "ONCO01",
            "IPS SOLIMED JD SAS": "IPSOL1",
            "MULTISALUD IPS": "MUL01",
            "MUNDIAL SOAT": "MUNDIAL",
            "PARTICULAR 10% DESCUENTO": "PAR01",
            "PARTICULAR 20% DESCUENTO": "PAR01",
            "PARTICULAR 30% DESCUENTO": "PAR01",
            "PARTICULAR 40% DESCUENTO": "PAR01",
            "PARTICULAR 50% DESCUENTO": "PAR01",
            "PARTICULAR 60% DESCUENTO": "PAR01",
            "PARTICULAR 70% DESCUENTO": "PAR01",
            "PARTICULAR 80% DESCUENTO": "PAR01",
            "PARTICULAR 90% DESCUENTO": "PAR01",
            "PARTICULAR BONO REGALO": "PAR01",
            "PARTICULAR TARIFA PLENA": "PAR01",
            "PREVISORA SOAT": "PREVISORA",
            "SEGUROS DEL ESTADO SOAT": "SEGESTAL",
            "SOLIDARIA SOAT": "SOLIDARIA",
            "SURA POLIZA EVOLUCIONA": "SURA01",
            "SURA POLIZA GLOBAL Y CLASICO": "SURA01",
            "SURA SOAT": "SURA01",
        }
        entidad_carpeta = carpetas_por_alias.get(contrato_alias, "OTRO")

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_nombre_archivo = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', entidad_carpeta, fecha_hoy, nombre_usuario)
        os.makedirs(carpeta_nombre_archivo, exist_ok=True)

        ruta_destino_merged = os.path.join(carpeta_nombre_archivo, f"{nombre_archivo}.pdf")
        merger.write(ruta_destino_merged)
        merger.close()

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({
            "success": True,
            "detail": f"Archivos combinados en un solo documento y guardados en {ruta_destino_merged}"
        }, status=200)
    except ArchivoFacturacion.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"
        }
        return JsonResponse(response_data, status=404)
    except Exception as e:
        response_data = {
            "success": False,
            "detail": str(e)
        }
        return JsonResponse(response_data, status=500)

      
      ####### LA POLITZIA ####
      
@api_view(['GET'])
@permission_classes([AllowAny])  
def radicar_policia_view(request, numero_admision, idusuario):
    try:
        try:
            user = CustomUser.objects.get(id=idusuario)
            nombre_usuario = "".join(c for c in user.username if c.isalnum() or c in (' ', '.', '_')).rstrip()
        except CustomUser.DoesNotExist:
            return JsonResponse({"success": False, "detail": "Usuario no encontrado."}, status=404)

        archivos_a_verificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)
        if archivos_a_verificar.exists() and archivos_a_verificar.filter(Radicado=True).exists():
            return JsonResponse({"success": False, "detail": f"La admisión con número {numero_admision} ya está radicada."}, status=400)

        admision_response = GeDocumentalView().get(request._request, consecutivo=numero_admision)
        if admision_response.status_code != 200:
            return admision_response

        admision_data = admision_response.data.get('data')
        factura_numero = admision_data.get('FacturaNo')
        prefijo = admision_data.get('Prefijo') or ''
        codigo_entidad = admision_data.get('CodigoEntidad')
        if factura_numero is None:
            raise ValueError("La admisión no tiene el número de factura")

        archivos_response = archivos_por_admision_radicacion(request._request, numero_admision)
        if archivos_response.status_code != 200:
            return archivos_response

        archivos_data = archivos_response.data.get('data', [])
        documentos_requeridos = {'FACTURA', 'RESULTADO'}
        tipos_archivos_presentes = {archivo.get('Tipo') for archivo in archivos_data}
        documentos_faltantes = documentos_requeridos - tipos_archivos_presentes
        if documentos_faltantes:
            return JsonResponse({"success": False, "detail": f"Faltan los siguientes documentos requeridos: {', '.join(documentos_faltantes)}"}, status=400)

        fecha_hoy = datetime.now().strftime('%Y-%m-%d')
        carpeta_usuario = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'Radicacion', 'POL', fecha_hoy, nombre_usuario)
        os.makedirs(carpeta_usuario, exist_ok=True)

        archivos_pdf = []
        for archivo in archivos_data:
            ruta_origen_relative = unquote(archivo.get('RutaArchivo')).replace(settings.MEDIA_URL, '').lstrip('/')
            ruta_origen = os.path.normpath(os.path.join(settings.MEDIA_ROOT, ruta_origen_relative))
            if os.path.exists(ruta_origen):
                archivos_pdf.append((archivo.get('Tipo'), ruta_origen))

        archivos_pdf.sort(key=lambda x: (0 if x[0] == 'FACTURA' else 1))

        temp_pdf_path = os.path.join(carpeta_usuario, f"FES{factura_numero}.pdf")
        merger = PdfMerger()
        for _, ruta in archivos_pdf:
            merger.append(ruta)
        merger.write(temp_pdf_path)
        merger.close()

        # Archivos adicionales: JSON, TXT, XML
        carpeta_json_txt = os.path.join('/mnt/prueba/DocsFESIESA', f'Factura-FES{factura_numero}')
        adicionales = []
        if os.path.exists(carpeta_json_txt):
            for nombre in os.listdir(carpeta_json_txt):
                ruta_origen = os.path.join(carpeta_json_txt, nombre)
                if nombre.lower().endswith('.json'):
                    with open(ruta_origen, 'r', encoding='utf-8') as f:
                        contenido_json = f.read()
                    contenido_stripped = contenido_json.lstrip()
                    if not (contenido_stripped.startswith('{"numDocumentoIdObligado') or
                            os.path.splitext(nombre)[0].upper() == f'FES{factura_numero}'.upper()):
                        continue
                    nuevo_nombre = f"FES{factura_numero}.json"
                elif nombre.lower().endswith('.txt'):
                    nuevo_nombre = f"ResultadosLocales_FES{factura_numero}.txt"
                else:
                    nuevo_nombre = nombre  # XML u otros se dejan igual
                ruta_destino = os.path.join(carpeta_usuario, nuevo_nombre)
                shutil.copy(ruta_origen, ruta_destino)
                adicionales.append(ruta_destino)

        # Crear ZIP con PDF y archivos adicionales
        zip_path = os.path.join(carpeta_usuario, f"FES{factura_numero}.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(temp_pdf_path, os.path.basename(temp_pdf_path))
            for adicional in adicionales:
                zipf.write(adicional, os.path.basename(adicional))

        os.remove(temp_pdf_path)
        for file in adicionales:
            os.remove(file)

        archivos_a_verificar.update(Radicado=True)

        return JsonResponse({"success": True, "detail": f"Archivos comprimidos en {zip_path}"}, status=200)

    except ArchivoFacturacion.DoesNotExist:
        return JsonResponse({"success": False, "detail": f"No se encontraron archivos para la admisión con número {numero_admision}"}, status=404)
    except Exception as e:
        return JsonResponse({"success": False, "detail": str(e)}, status=500)
      
      
      
      
      
        
class AdmisionesPorFechaYUsuario(APIView):
    def get(self, request, format=None):
        fecha_creacion_archivo = request.GET.get('FechaCreacionAntares')
        usuario_id = request.GET.get('UsuarioId')

        if fecha_creacion_archivo and usuario_id:
            try:
                # Filtrar las admisiones por fecha de creación y usuario, ordenar y agrupar por Admision_id
                admisiones_queryset = (ArchivoFacturacion.objects
                                       .filter(FechaCreacionAntares__date=fecha_creacion_archivo, Usuario_id=usuario_id)
                                       .values('Admision_id')
                                       .annotate(cantidad=Count('Admision_id'))
                                       .order_by('Admision_id'))  # Ordenar por Admision_id

                admisiones_count = admisiones_queryset.count()

                admisiones_list = []
                for admision in admisiones_queryset:
                    admision_dict = {
                        'Consecutivo': admision['Admision_id'],
                    }
                    admisiones_list.append(admision_dict)

                response_data = {
                    "success": True,
                    "detail": "Admisiones encontradas.",
                    "cantidad": admisiones_count,
                    "data": admisiones_list
                }
                return JsonResponse(response_data)
            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al buscar admisiones: {str(e)}",
                    "cantidad": None,
                    "data": None
                }
                return JsonResponse(response_data, status=500)
        else:
            response_data = {
                "success": False,
                "detail": "Faltan parámetros: FechaCreacionArchivo y/o UsuarioId.",
                "cantidad": None,
                "data": None
            }
            return JsonResponse(response_data, status=400)

########## FILTRO DE PUNTEO ADMISIONES ####

class AdmisionesPorFechaYFacturado(APIView):
    def get(self, request, format=None):
        fecha = request.GET.get('Fecha')
        creado_por = request.GET.get('CreadoPor')

        if fecha and creado_por:
            try:
                with connections['zeussalud'].cursor() as cursor:
                    # ZeusSalud: sis_maes no tiene CreadoPor; se filtra solo por fecha_ing
                    query_count = '''
                    SELECT COUNT(*) as cantidad
                    FROM sis_maes
                    WHERE CONVERT(date, fecha_ing) = %s
                    '''
                    cursor.execute(query_count, [fecha])
                    admisiones_count = cursor.fetchone()[0]

                    query_details = '''
                    SELECT con_estudio, nro_factura
                    FROM sis_maes
                    WHERE CONVERT(date, fecha_ing) = %s
                    '''
                    cursor.execute(query_details, [fecha])
                    admisiones_data = cursor.fetchall()

                    admisiones_list = []
                    for admision_data in admisiones_data:
                        admision_dict = {
                            'Consecutivo': admision_data[0],
                            'Prefijo': None,
                            'FacturaNo': admision_data[1],
                        }
                        admisiones_list.append(admision_dict)

                response_data = {
                    "success": True,
                    "detail": "Admisiones encontradas.",
                    "cantidad": admisiones_count,
                    "data": admisiones_list
                }
                return JsonResponse(response_data)
            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al buscar admisiones: {str(e)}",
                    "cantidad": None,
                    "data": None
                }
                return JsonResponse(response_data, status=500)
        else:
            response_data = {
                "success": False,
                "detail": "Faltan parámetros: fecha y/o facturado_por.",
                "cantidad": None,
                "data": None
            }
            return JsonResponse(response_data, status=400)


###### PUNTEO SUBDIRECCION DE PROCESOS Y DIRECCION ####


class PunteoNeurodxSubdireccion(APIView):
    def get(self, request, format=None):
        fecha_inicio = request.GET.get('FechaInicio')
        fecha_fin = request.GET.get('FechaFin')
        usuario_id = request.GET.get('UsuarioId')

        if fecha_inicio and fecha_fin and usuario_id:
            try:
                # Filtrar las admisiones por el rango de fechas y usuario, ordenar y agrupar por Admision_id
                admisiones_queryset = (ArchivoFacturacion.objects
                                       .filter(FechaCreacionAntares__date__range=[fecha_inicio, fecha_fin], 
                                               Usuario_id=usuario_id)
                                       .values('Admision_id')
                                       .annotate(cantidad=Count('Admision_id'))
                                       .order_by('Admision_id'))  # Ordenar por Admision_id

                admisiones_count = admisiones_queryset.count()

                admisiones_list = []
                for admision in admisiones_queryset:
                    admision_dict = {
                        'Consecutivo': admision['Admision_id'],
                    }
                    admisiones_list.append(admision_dict)

                response_data = {
                    "success": True,
                    "detail": "Admisiones encontradas.",
                    "cantidad": admisiones_count,
                    "data": admisiones_list
                }
                return JsonResponse(response_data)
            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al buscar admisiones: {str(e)}",
                    "cantidad": None,
                    "data": None
                }
                return JsonResponse(response_data, status=500)
        else:
            response_data = {
                "success": False,
                "detail": "Faltan parámetros: FechaInicio, FechaFin y/o UsuarioId.",
                "cantidad": None,
                "data": None
            }
            return JsonResponse(response_data, status=400)


##### PUNTEO ADMISIONES ANTARES, SUBDIRECCION ####
class PunteoAntaresSubdireccion(APIView):
    def get(self, request, format=None):
        fecha_inicio = request.GET.get('FechaInicio')
        fecha_fin = request.GET.get('FechaFin')
        creado_por = request.GET.get('CreadoPor')

        if fecha_inicio and fecha_fin and creado_por:
            try:
                with connections['zeussalud'].cursor() as cursor:
                    # ZeusSalud: sis_maes no tiene CreadoPor; se filtra solo por rango de fecha_ing
                    query_count = '''
                    SELECT COUNT(*) as cantidad
                    FROM sis_maes
                    WHERE CONVERT(date, fecha_ing) BETWEEN %s AND %s
                    '''
                    cursor.execute(query_count, [fecha_inicio, fecha_fin])
                    admisiones_count = cursor.fetchone()[0]

                    query_details = '''
                    SELECT con_estudio, nro_factura
                    FROM sis_maes
                    WHERE CONVERT(date, fecha_ing) BETWEEN %s AND %s
                    '''
                    cursor.execute(query_details, [fecha_inicio, fecha_fin])
                    admisiones_data = cursor.fetchall()

                    admisiones_list = []
                    for admision_data in admisiones_data:
                        admision_dict = {
                            'Consecutivo': admision_data[0],
                            'Prefijo': None,
                            'FacturaNo': admision_data[1],
                        }
                        admisiones_list.append(admision_dict)

                response_data = {
                    "success": True,
                    "detail": "Admisiones encontradas.",
                    "cantidad": admisiones_count,
                    "data": admisiones_list
                }
                return JsonResponse(response_data)
            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al buscar admisiones: {str(e)}",
                    "cantidad": None,
                    "data": None
                }
                return JsonResponse(response_data, status=500)
        else:
            response_data = {
                "success": False,
                "detail": "Faltan parámetros: FechaInicio, FechaFin y/o CreadoPor.",
                "cantidad": None,
                "data": None
            }
            return JsonResponse(response_data, status=400)
        

### FILTRO POR TIPO DE DOCUMENTO######
class AdmisionesConTiposDeDocumento(APIView):
    def get(self, request, format=None):
        # Recuperar parámetros de la solicitud
        fecha_inicio = request.GET.get('FechaInicio')
        fecha_fin = request.GET.get('FechaFin')
        usuario_id = request.GET.get('UsuarioId')

        # Verificar la existencia de los parámetros requeridos
        if fecha_inicio and fecha_fin and usuario_id:
            try:
                # Filtrar admisiones por el rango de fechas de FechaCreacionAntares y el usuario
                admisiones_queryset = (
                    ArchivoFacturacion.objects
                    .filter(
                        FechaCreacionAntares__date__range=[fecha_inicio, fecha_fin],
                        Usuario_id=usuario_id
                    )
                    .values('Admision_id', 'FechaCreacionAntares')
                    .annotate(cantidad=Count('Admision_id'))
                    .order_by('Admision_id')
                )

                # Orden deseado para los tipos de documentos
                tipos_documento_ordenados = [
                    'FACTURA', 'COMPROBANTE', 'AUTORIZACION', 'ORDEN',
                    'ADICIONALES', 'RESULTADO', 'HCNEURO', 'HCLINICA'
                ]

                admisiones_list = []
                for admision in admisiones_queryset:
                    admision_id = admision['Admision_id']
                    fecha_creacion_antares = admision['FechaCreacionAntares']

                    # Formatear FechaCreacionAntares a solo incluir año, mes, día
                    fecha_creacion_antares_str = fecha_creacion_antares.strftime('%Y-%m-%d') if fecha_creacion_antares else None

                    # Recuperar el 'CodigoEntidad' asociado con el 'Admision_id'
                    codigo_entidad = None
                    try:
                        with connections['zeussalud'].cursor() as cursor:
                            cursor.execute('SELECT EPSPaciente FROM sis_maes WHERE con_estudio = %s', [admision_id])
                            entidad_info = cursor.fetchone()
                            codigo_entidad = entidad_info[0] if entidad_info else None
                    except Exception:
                        pass

                    # Obtener y verificar los tipos de documentos asociados a la admisión
                    tipos_documento = (
                        ArchivoFacturacion.objects
                        .filter(Admision_id=admision_id)
                        .values('Tipo')
                    )
                    tipos_documento_list = [tipo['Tipo'] for tipo in tipos_documento]

                    # Inicializar un diccionario con valores en blanco para cada tipo esperado
                    tipos_documento_dict = {tipo: '' for tipo in tipos_documento_ordenados}

                    # Verificar y completar el diccionario con valores reales desde los documentos obtenidos
                    for tipo in tipos_documento_list:
                        if tipo in tipos_documento_dict:
                            tipos_documento_dict[tipo] = tipo

                    # Convertir el diccionario a una lista siguiendo el orden deseado
                    tipos_documento_list_sorted = [tipos_documento_dict[tipo] for tipo in tipos_documento_ordenados]

                    admision_dict = {
                        'Consecutivo': admision_id,
                        'FechaCreacionAntares': fecha_creacion_antares_str,
                        'CodigoEntidad': codigo_entidad,
                        'TiposDeDocumento': tipos_documento_list_sorted,
                    }
                    admisiones_list.append(admision_dict)

                response_data = {
                    "success": True,
                    "detail": "Admisiones encontradas.",
                    "cantidad": len(admisiones_list),
                    "data": admisiones_list
                }
                return JsonResponse(response_data)

            except Exception as e:
                response_data = {
                    "success": False,
                    "detail": f"Error al buscar admisiones: {str(e)}",
                    "cantidad": None,
                    "data": None
                }
                return JsonResponse(response_data, status=500)

        else:
            response_data = {
                "success": False,
                "detail": "Faltan parámetros: FechaInicio, FechaFin y/o UsuarioId.",
                "cantidad": None,
                "data": None
            }
            return JsonResponse(response_data, status=400)

        

class ActualizarRegimenArchivosView(APIView):
    def post(self, request, consecutivo, format=None):
        regimen = request.data.get('regimen')
        
        if not regimen or regimen not in ['C', 'S']:
            return Response({"success": False, "message": "Regimen inválido"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            with transaction.atomic():
                archivos_actualizados = ArchivoFacturacion.objects.filter(Admision_id=consecutivo).update(Regimen=regimen)
                
                if archivos_actualizados == 0:
                    return Response({"success": False, "message": f"No se encontraron archivos para la admisión {consecutivo}"}, status=status.HTTP_404_NOT_FOUND)
                
                return Response({"success": True, "message": f"Regimen actualizado a {regimen} para {archivos_actualizados} archivos de la admisión {consecutivo}"}, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response({"success": False, "message": "Error interno del servidor", "error_details": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


### CREAR OBSERVACIONES SIN ARCHIVO PARA LOS RESULTADOS QUE NO ESTAN CARGADOS !
class AgregarObservacionSinArchivoView(APIView):
    def post(self, request, *args, **kwargs):
        try:
            usuario_id = request.data.get('Usuario') or request.data.get('Usuario_id')
            admision_id = request.data.get('AdmisionId')
            descripcion = request.data.get('Descripcion', '')
            tipo_archivo = request.data.get('TipoArchivo', 'Sin Archivo')

            if not usuario_id:
                return Response({"success": False, "message": "Usuario es requerido"}, status=status.HTTP_400_BAD_REQUEST)
            if not admision_id:
                return Response({"success": False, "message": "AdmisionId es requerido"}, status=status.HTTP_400_BAD_REQUEST)

            observacion = ObservacionSinArchivo.objects.create(
                AdmisionId=admision_id,
                Usuario_id=usuario_id,
                Descripcion=descripcion,
                TipoArchivo=tipo_archivo,
            )
            return Response({
                "success": True,
                "message": "Observación sin archivo agregada correctamente",
                "data": {
                    "id": observacion.id,
                    "AdmisionId": observacion.AdmisionId,
                    "Descripcion": observacion.Descripcion,
                    "FechaObservacion": observacion.FechaObservacion,
                    "Revisada": observacion.Revisada,
                    "Usuario": observacion.Usuario_id,
                }
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    


#### ADMISION SIN ARCHIVO#####
class ObservacionesPorUsuario(APIView):
    def get(self, request, user_id):
        try:
            # Filtrar las observaciones por usuario
            observaciones = ObservacionSinArchivo.objects.filter(Usuario_id=user_id)
            print(f"Usuario_id: {user_id}, Observaciones count: {observaciones.count()}")  # Debugging line
            
            # Filtrar las observaciones que están asociadas a una admisión donde no todos los archivos tienen RevisionPrimera en True
            observaciones_filtradas = []
            for observacion in observaciones:
                admision_id = observacion.AdmisionId
                archivos = ArchivoFacturacion.objects.filter(Admision_id=admision_id)
                
                # Agrega la observación a la lista si no todos los archivos tienen RevisionPrimera en True
                if archivos.exists() and not archivos.filter(RevisionPrimera=True).count() == archivos.count():
                    observaciones_filtradas.append(observacion)
            
            if not observaciones_filtradas:
                return Response([], status=200)

            # Serializar las observaciones filtradas
            serializer = ObservacionSinArchivoSerializer(observaciones_filtradas, many=True)
            return Response(serializer.data, status=200)

        except Exception as e:
            response_data = {
                "success": False,
                "detail": "Error interno del servidor",
                "error_details": str(e)
            }
            return JsonResponse(response_data, status=500)


        

####  MODIFICACION REALIZADA ADMISON SIN ARCHIVO #####
class RevisarObservacion(APIView):
    def patch(self, request, admision_id):
        # Obtener todas las observaciones con el AdmisionId dado
        observaciones = ObservacionSinArchivo.objects.filter(AdmisionId=admision_id)
        if not observaciones.exists():
            return Response({'error': 'Observación no encontrada'}, status=status.HTTP_404_NOT_FOUND)

        # Buscar un archivo con un UsuarioCuentasMedicas_id asociado
        try:
            archivo = ArchivoFacturacion.objects.filter(
                Admision_id=admision_id,
                UsuarioCuentasMedicas__isnull=False
            ).order_by('-FechaCreacionArchivo').first()

            if archivo is None:
                return Response({'error': 'No se encontró archivo con UsuarioCuentasMedicas asociado con la admisión'}, status=status.HTTP_404_NOT_FOUND)
            
            id_revisor = archivo.UsuarioCuentasMedicas_id  # Tomar el Id del UsuarioCuentasMedicas

            print(f"Archivo encontrado: {archivo}")
            print(f"UsuarioCuentasMedicas asociado: {archivo.UsuarioCuentasMedicas}")
            print(f"IdRevisor (UsuarioCuentasMedicas_id) obtenido: {id_revisor}")

        except ArchivoFacturacion.DoesNotExist:
            return Response({'error': 'Revisor no encontrado'}, status=status.HTTP_404_NOT_FOUND)

        # Iterar sobre todas las observaciones encontradas y actualizarlas
        for observacion in observaciones:
            request_data = request.data.copy()
            request_data['IdRevisor'] = id_revisor
            request_data['Revisada'] = True

            serializer = ObservacionSinArchivoSerializer(observacion, data=request_data, partial=True)
            if serializer.is_valid():
                serializer.save()
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'Observaciones actualizadas correctamente'}, status=status.HTTP_200_OK)




import logging
logger = logging.getLogger(__name__)
@api_view(['POST'])
def actualizar_modificado_revisor(request):
    data = request.data
    admision_id = data.get('admision_id')
    tipo_revisor = data.get('tipo_revisor')

    print(f"Datos recibidos: admision_id={admision_id}, tipo_revisor={tipo_revisor}")

    try:
        with transaction.atomic():
            archivos = ArchivoFacturacion.objects.filter(Admision_id=admision_id)
            if not archivos.exists():
                print(f"No se encontraron archivos para la admisión {admision_id}")
                return JsonResponse({"success": False, "detail": "Archivo no encontrado"}, status=404)

            for archivo in archivos:
                print(f"Procesando archivo con IdArchivo: {archivo.IdArchivo}")

                # Asignar idRevisor basado en tipo_revisor
                if tipo_revisor == "cuentas_medicas":
                    if archivo.UsuarioCuentasMedicas_id:
                        archivo.IdRevisor = archivo.UsuarioCuentasMedicas_id
                        print(f"Asignado UsuarioCuentasMedicas_id: {archivo.UsuarioCuentasMedicas_id} a IdRevisor")
                    else:
                        print("UsuarioCuentasMedicas_id es None")
                elif tipo_revisor == "tesoreria":
                    if archivo.UsuariosTesoreria_id:
                        archivo.IdRevisor = archivo.UsuariosTesoreria_id
                        print(f"Asignado UsuariosTesoreria_id: {archivo.UsuariosTesoreria_id} a IdRevisor")
                    else:
                        print("UsuariosTesoreria_id es None")

                print(f"IdRevisor asignado: {archivo.IdRevisor}")

                # Actualizar los campos de modificado
                if archivo.Modificado1 is None:
                    archivo.Modificado1 = 1
                    print("Modificado1 actualizado a 1")
                elif archivo.Modificado1 == 1 and archivo.Modificado2 is None:
                    archivo.Modificado2 = 1
                    print("Modificado2 actualizado a 1")
                elif archivo.Modificado2 == 1 and archivo.Modificado3 is None:
                    archivo.Modificado3 = 1
                    print("Modificado3 actualizado a 1")

                archivo.save()
                print(f"Archivo guardado con IdArchivo: {archivo.IdArchivo}, Modificado1: {archivo.Modificado1}, Modificado2: {archivo.Modificado2}, Modificado3: {archivo.Modificado3}, IdRevisor: {archivo.IdRevisor}")

            return JsonResponse({"success": True, "detail": "Archivos actualizados correctamente"})

    except Exception as e:
        print(f"Error al actualizar archivos: {str(e)}")
        return JsonResponse({"success": False, "detail": str(e)}, status=500)


######## admisiones ya modofocadas para cuentas medicas o tesoreria 

def admisiones_con_id_revisor(request, id_revisor):
    try:
        # Filtrar registros de ArchivoFacturacion para el revisor dado
        archivos = ArchivoFacturacion.objects.filter(IdRevisor=id_revisor)

        # Obtener los Ids de las admisiones con los archivos filtrados
        admisiones_ids = archivos.values_list('Admision_id', flat=True).distinct()

        # Filtrar registros de AuditoriaCuentasMedicas con la condición especificada (solo con IdRevisor)
        admisiones_con_revisor = AuditoriaCuentasMedicas.objects.filter(
            AdmisionId__in=admisiones_ids
        )

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor:
            for auditoria in admisiones_con_revisor:
                # Verificar si hay algún archivo asociado con RevisionPrimera=False
                archivos_admision = ArchivoFacturacion.objects.filter(Admision_id=auditoria.AdmisionId)
                if not archivos_admision.filter(RevisionPrimera=False).exists():
                    continue

                # Obtener datos de la admisión
                admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)
                # [0]=con_estudio, [1]=num_id, [2]=EPSPaciente, [3]=NombreCompleto, [4]=nro_factura, [5]=tipoUsuario, [6]=fecha_ing

                if admision_data:
                    transformed_data = {
                        'Consecutivo': admision_data[0],
                        'IdPaciente': admision_data[1],
                        'CodigoEntidad': admision_data[2],
                        'NombreResponsable': admision_data[3],
                        'FacturaNo': str(admision_data[4] or ''),
                    }
                    admisiones_data.append(transformed_data)

        response_data = {
            "success": True,
            "detail": f"Admisiones con el revisor ID {id_revisor} encontradas",
            "data": admisiones_data
        }

        return JsonResponse(response_data, status=200)

    except AuditoriaCuentasMedicas.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron admisiones con el revisor ID {id_revisor}",
            "data": None
        }

        return JsonResponse(response_data, status=404)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }

        return JsonResponse(response_data, status=500)


@api_view(['GET'])
def archivos_por_usuario_observacion(request, user_id):
    try:
        # Admisiones con archivos subidos por este usuario que tienen observaciones y no han sido aprobados
        admision_ids = (
            ArchivoFacturacion.objects
            .filter(Usuario_id=user_id, RevisionPrimera=False)
            .filter(Observaciones__isnull=False)
            .values_list('Admision_id', flat=True)
            .distinct()
        )

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor:
            admisiones_map = get_admisiones_zeus_bulk(cursor, list(admision_ids))

        for admision_id in admision_ids:
            row = admisiones_map.get(admision_id)
            if not row:
                continue
            archivo = (
                ArchivoFacturacion.objects
                .filter(Admision_id=admision_id, Usuario_id=user_id, RevisionPrimera=False)
                .filter(Observaciones__isnull=False)
                .order_by('-FechaCreacionArchivo')
                .first()
            )
            obs_reciente = archivo.Observaciones.order_by('-FechaObservacion').first() if archivo else None
            admisiones_data.append({
                'Consecutivo': row[0],
                'IdPaciente': row[1],
                'CodigoEntidad': row[2],
                'NombreResponsable': row[3],
                'FacturaNo': str(row[4] or ''),
                'FechaRecienteObservacion': obs_reciente.FechaObservacion.isoformat() if obs_reciente else None,
                'Modificado1': archivo.Modificado1 if archivo else None,
                'cuentas_medicas': archivo.RevisionPrimera if archivo else False,
            })

        return JsonResponse({'success': True, 'data': admisiones_data}, status=200)

    except Exception as e:
        return JsonResponse({'success': False, 'detail': str(e)}, status=500)


@api_view(['GET'])
def archivos_por_usuario_observacion_tesoreria(request, user_id):
    try:
        admision_ids = (
            ArchivoFacturacion.objects
            .filter(UsuariosTesoreria_id=user_id, RevisionSegunda=False)
            .values_list('Admision_id', flat=True)
            .distinct()
        )

        with connections['zeussalud'].cursor() as cursor:
            admisiones_map = get_admisiones_zeus_bulk(cursor, list(admision_ids))

        admisiones_data = []
        for admision_id in admision_ids:
            row = admisiones_map.get(admision_id)
            if not row:
                continue
            archivo = (
                ArchivoFacturacion.objects
                .filter(Admision_id=admision_id, UsuariosTesoreria_id=user_id)
                .order_by('-FechaCreacionArchivo')
                .first()
            )
            admisiones_data.append({
                'Consecutivo': row[0],
                'IdPaciente': row[1],
                'CodigoEntidad': row[2],
                'NombreResponsable': row[3],
                'FacturaNo': str(row[4] or ''),
                'FechaRecienteObservacion': archivo.FechaCreacionArchivo.isoformat() if archivo and archivo.FechaCreacionArchivo else None,
                'Modificado1': archivo.Modificado1 if archivo else None,
                'cuentas_medicas': archivo.RevisionSegunda if archivo else False,
            })

        return JsonResponse({'success': True, 'data': admisiones_data}, status=200)

    except Exception as e:
        return JsonResponse({'success': False, 'detail': str(e)}, status=500)


# ELIMINACION DE ARCHIVOS
class ArchivoFacturacionDeleteView(APIView):
    def delete(self, request):
        archivo_id = request.query_params.get('archivo_id', None)
        print(f"Received request to delete archivo_id: {archivo_id}")

        if not archivo_id:
            print("archivo_id is missing in the request")
            return Response({"error": "archivo_id is required"}, status=400)

        try:
            archivo = ArchivoFacturacion.objects.get(pk=archivo_id)
            print(f"Archivo encontrado: {archivo}")
            ruta_archivo = archivo.RutaArchivo.path if archivo.RutaArchivo else None
            print(f"Ruta del archivo: {ruta_archivo}")

            # Eliminar el archivo de la base de datos
            archivo.delete()
            print(f"Archivo {archivo_id} eliminado de la base de datos")

            # Verificar si el archivo existe en el sistema de archivos antes de eliminarlo
            if ruta_archivo and os.path.exists(ruta_archivo):
                os.remove(ruta_archivo)
                print(f"Archivo {ruta_archivo} eliminado del sistema de archivos")

            return Response(status=status.HTTP_204_NO_CONTENT)
        except ArchivoFacturacion.DoesNotExist:
            print(f"Archivo con ID {archivo_id} no encontrado")
            return Response({"detail": "Archivo no encontrado"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            print(f"Error al eliminar el archivo: {str(e)}")
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
          
          
######## ADMISIONES REVISADAS DE CUENTAS MEDICAS A TESORERIA 

def admisiones_revisada_cm(request, idusuariorevisor):
    try:
        # Filtrar registros de ArchivoFacturacion para el revisor dado
        archivos = ArchivoFacturacion.objects.filter(IdRevisor=idusuariorevisor)

        # Obtener los Ids de las admisiones con los archivos filtrados
        admisiones_ids = archivos.values_list('Admision_id', flat=True).distinct()

        # Filtrar registros de AuditoriaCuentasMedicas con la condición especificada (solo con IdRevisor)
        admisiones_con_revisor = AuditoriaCuentasMedicas.objects.filter(
            AdmisionId__in=admisiones_ids
        )

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor:
            for auditoria in admisiones_con_revisor:
                # Verificar si hay algún archivo asociado con RevisionPrimera=False
                archivos_admision = ArchivoFacturacion.objects.filter(Admision_id=auditoria.AdmisionId)
                if not archivos_admision.filter(RevisionPrimera=False).exists():
                    continue

                # Obtener datos de la admisión
                admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)
                # [0]=con_estudio, [1]=num_id, [2]=EPSPaciente, [3]=NombreCompleto, [4]=nro_factura, [5]=tipoUsuario, [6]=fecha_ing

                if admision_data:
                    transformed_data = {
                        'Consecutivo': admision_data[0],
                        'IdPaciente': admision_data[1],
                        'CodigoEntidad': admision_data[2],
                        'NombreResponsable': admision_data[3],
                        'FacturaNo': str(admision_data[4] or ''),
                    }
                    admisiones_data.append(transformed_data)

        response_data = {
            "success": True,
            "detail": f"Admisiones con el revisor ID {idusuariorevisor} encontradas",
            "data": admisiones_data
        }

        return JsonResponse(response_data, status=200)

    except AuditoriaCuentasMedicas.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron admisiones con el revisor ID {idusuariorevisor}",
            "data": None
        }

        return JsonResponse(response_data, status=404)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }

        return JsonResponse(response_data, status=500)



logger = logging.getLogger(__name__)

@api_view(['POST'])
def actualizar_correciones_cm(request):
    data = request.data
    admision_id = data.get('admision_id')
    user_id = data.get('user_id')

    print(f"Datos recibidos: admision_id={admision_id}, user_id={user_id}")

    try:
        with transaction.atomic():
            archivos = ArchivoFacturacion.objects.filter(Admision_id=admision_id)
            if not archivos.exists():
                print(f"No se encontraron archivos para la admisión {admision_id}")
                return JsonResponse({"success": False, "detail": "Archivo no encontrado"}, status=404)

            for archivo in archivos:
                print(f"Procesando archivo con IdArchivo: {archivo.IdArchivo}")

                # Asignar IdRevisor basado en user_id
                archivo.IdRevisor = user_id
                print(f"Asignado user_id: {user_id} a IdRevisor")

                # Actualizar los campos de modificado
                if archivo.Modificado1 is None:
                    archivo.Modificado1 = 1
                    print("Modificado1 actualizado a 1")
                elif archivo.Modificado1 == 1 and archivo.Modificado2 is None:
                    archivo.Modificado2 = 1
                    print("Modificado2 actualizado a 1")
                elif archivo.Modificado2 == 1 and archivo.Modificado3 is None:
                    archivo.Modificado3 = 1
                    print("Modificado3 actualizado a 1")

                archivo.save()
                print(f"Archivo guardado con IdArchivo: {archivo.IdArchivo}, Modificado1: {archivo.Modificado1}, Modificado2: {archivo.Modificado2}, Modificado3: {archivo.Modificado3}, IdRevisor: {archivo.IdRevisor}")

            return JsonResponse({"success": True, "detail": "Archivos actualizados correctamente"})

    except Exception as e:
        print(f"Error al actualizar archivos: {str(e)}")
        return JsonResponse({"success": False, "detail": str(e)}, status=500)

########## ENVIAR A TESORERIA ADMISIONES QUE AFECTAN CAJA##############
logger = logging.getLogger(__name__)

@api_view(['POST'])
def idrevisor_tesoreria(request):
    data = request.data
    admision_id = data.get('admision_id')
    user_id = data.get('user_id')

    print(f"Datos recibidos: admision_id={admision_id}, user_id={user_id}")

    try:
        with transaction.atomic():
            archivos = ArchivoFacturacion.objects.filter(Admision_id=admision_id)
            if not archivos.exists():
                print(f"No se encontraron archivos para la admisión {admision_id}")
                return JsonResponse({"success": False, "detail": "Archivo no encontrado"}, status=404)

            for archivo in archivos:
                print(f"Procesando archivo con IdArchivo: {archivo.IdArchivo}")

                # Asignar IdRevisor basado en user_id
                archivo.IdRevisorTesoreria = user_id
                print(f"Asignado user_id: {user_id} a IdRevisorTesoreria")

                # Actualizar los campos de modificado
                if archivo.Modificado1 is None:
                    archivo.Modificado1 = 1
                    print("Modificado1 actualizado a 1")
                elif archivo.Modificado1 == 1 and archivo.Modificado2 is None:
                    archivo.Modificado2 = 1
                    print("Modificado2 actualizado a 1")
                elif archivo.Modificado2 == 1 and archivo.Modificado3 is None:
                    archivo.Modificado3 = 1
                    print("Modificado3 actualizado a 1")

                archivo.save()
               

            return JsonResponse({"success": True, "detail": "Archivos actualizados correctamente"})

    except Exception as e:
        
        return JsonResponse({"success": False, "detail": str(e)}, status=500)

##### TRAE LAS ADMISIONES QUE HAN SIDO REVISDAS POR CM Y SON ENVIADAS A TESORERIA
@api_view(['GET'])
@permission_classes([AllowAny])
def admisiones_revision_para_cm(request, id_revisor):
    try:
        # Filtrar registros de ArchivoFacturacion para el revisor dado
        archivos = ArchivoFacturacion.objects.filter(IdRevisorTesoreria=id_revisor)

        # Obtener los Ids de las admisiones con los archivos filtrados
        admisiones_ids = archivos.values_list('Admision_id', flat=True).distinct()

        # Filtrar registros de AuditoriaCuentasMedicas con la condición especificada (solo con IdRevisor)
        admisiones_con_revisor = AuditoriaCuentasMedicas.objects.filter(
            AdmisionId__in=admisiones_ids
        )

        admisiones_data = []
        with connections['zeussalud'].cursor() as cursor:
            for auditoria in admisiones_con_revisor:
                # Obtener datos de la admisión
                admision_data = get_admision_zeus(cursor, auditoria.AdmisionId)
                # [0]=con_estudio, [1]=num_id, [2]=EPSPaciente, [3]=NombreCompleto, [4]=nro_factura, [5]=tipoUsuario, [6]=fecha_ing

                if admision_data:
                    factura_completa = str(admision_data[4] or '')

                    # Formatear la FechaCreado (fecha_ing) para enviar solo año-mes-día
                    fecha_creado = admision_data[6].strftime('%Y-%m-%d') if admision_data[6] else None

                    # Obtener observaciones con archivos relacionados a la admisión
                    observaciones_archivos = ObservacionesArchivos.objects.filter(
                        IdArchivo__Admision_id=auditoria.AdmisionId
                    ).select_related('IdArchivo')

                    # Obtener observaciones sin archivos relacionadas a la admisión
                    observaciones_sin_archivo = ObservacionSinArchivo.objects.filter(
                        AdmisionId=auditoria.AdmisionId
                    ).select_related('Usuario')

                    # Listar las observaciones con archivos
                    observaciones_archivo_list = list(observaciones_archivos.values('IdObservacion', 'Descripcion', 'FechaObservacion'))

                    # Listar las observaciones sin archivos
                    observaciones_sin_archivo_list = list(observaciones_sin_archivo.values('id', 'Descripcion', 'FechaObservacion'))

                    # Obtener los nombres de los usuarios asociados a las observaciones
                    usuarios_con_observacion_archivo_ids = set(
                        observaciones_archivos.values_list('IdArchivo__Usuario_id', flat=True).distinct()
                    )

                    usuarios_con_observacion_sin_archivo_ids = set(
                        observaciones_sin_archivo.values_list('Usuario_id', flat=True).distinct()
                    )

                    # Combinar todos los usuarios que tienen observaciones
                    usuario_ids = list(usuarios_con_observacion_archivo_ids.union(usuarios_con_observacion_sin_archivo_ids))

                    # Consultar los nombres de los usuarios basados en los IDs
                    usuarios = CustomUser.objects.filter(id__in=usuario_ids).values_list('nombre', flat=True) 
                    usuarios_list = list(usuarios)  # Convertir QuerySet a lista

                    # Añadir los datos y observaciones al diccionario de respuesta
                    transformed_data = {
                        'Consecutivo': admision_data[0],
                        'IdPaciente': admision_data[1],
                        'CodigoEntidad': admision_data[2],
                        'NombreResponsable': admision_data[3],
                        'FacturaNo': factura_completa,
                        'FechaCreado': fecha_creado,  # Formatear FechaCreado a año-mes-día
                        'Usuarios': usuarios_list,
                        'ObservacionesArchivos': observaciones_archivo_list,
                        'ObservacionesSinArchivos': observaciones_sin_archivo_list
                    }
                    admisiones_data.append(transformed_data)

        response_data = {
            "success": True,
            "detail": f"Admisiones con el revisor ID {id_revisor} encontradas",
            "data": admisiones_data
        }

        return JsonResponse(response_data, status=200)

    except AuditoriaCuentasMedicas.DoesNotExist:
        response_data = {
            "success": False,
            "detail": f"No se encontraron admisiones con el revisor ID {id_revisor}",
            "data": None
        }

        return JsonResponse(response_data, status=404)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }

        return JsonResponse(response_data, status=500)
#####

@api_view(['POST'])
def quitar_revisor_admision(request, numero_admision):
    try:
        # Filtrar registros de la admisión en la tabla ArchivoFacturacion
        archivos_a_modificar = ArchivoFacturacion.objects.filter(Admision_id=numero_admision)

        # Verificar si existen registros con ese número de admisión y un revisor asignado
        if not archivos_a_modificar.exists():
            return JsonResponse(
                {"success": False, "detail": f"No se encontraron registros para la admisión con número {numero_admision}."},
                status=404
            )

        # Actualizar el campo IdRevisor a 0 para desasociar del revisor actual
        archivos_a_modificar.update(IdRevisorTesoreria=0)

        response_data = {
            "success": True,
            "detail": f"El revisor ha sido desasociado de la admisión con número {numero_admision}."
        }

        return JsonResponse(response_data, status=200)

    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }
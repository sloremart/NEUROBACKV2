import os
import shutil
from datetime import datetime,  timedelta
from django.db import connections
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import status
from django.db.models import Q
from resultadosgedocumental.serializers import ConsolidadoEstudiosSerializer
from resultadosgedocumental.models import ConsolidadoEstudios
import json
import re
import subprocess
import hashlib
import bcrypt
class ListaExamenes(APIView):
    def get(self, request):
        try:
            with connections['hcresult'].cursor() as cursor:
                cursor.execute('''
                    SELECT id, codigo, nombre, cups, tipo_examen, estado
                    FROM examen
                ''')
                rows = cursor.fetchall()

            examenes = [
                {
                    'id': row[0],
                    'codigo': row[1],
                    'nombre': row[2],
                    'cups': row[3],
                    'tipo_examen': row[4],
                    'estado': row[5],
                }
                for row in rows
            ]

            return Response(examenes, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




class SubirDocumentoExamen(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, format=None):
        try:
            # Obtener datos del request
            archivo = request.FILES.get('file')
            tipo_archivo = 'application/pdf'
            admision_data = request.data.get('admision_data')
            examen_id = request.data.get('examen_id')
            fecha_examen = request.data.get('fecha_examen')
            fecha_resultado = request.data.get('fecha_resultado')

            if not archivo:
                return Response({"success": False, "detail": "No se proporcionó ningún archivo."}, status=400)
            if not admision_data or not examen_id or not fecha_examen or not fecha_resultado:
                return Response({"success": False, "detail": "Faltan datos obligatorios."}, status=400)

            admision = json.loads(admision_data)
            id_paciente = admision['IdPaciente']
            archivo_nombre_limpio = archivo.name

            # Ruta sin subcarpeta por paciente
            remote_folder = '/media/disco1/examenes/'
            os.makedirs(remote_folder, exist_ok=True)
            remote_file_path = os.path.join(remote_folder, archivo_nombre_limpio)

            # Guardar archivo físicamente
            try:
                with open(remote_file_path, 'wb') as f:
                    for chunk in archivo.chunks():
                        f.write(chunk)
                print(f">>> Archivo guardado en: {remote_file_path}")
            except Exception as e:
                print(f">>> ERROR al guardar el archivo en {remote_file_path}: {str(e)}")
                raise

            # Buscar datos frescos del paciente en ZeusSalud (sis_paci) por documento
            paciente_zeus = None
            with connections['zeussalud'].cursor() as cursor_zeus:
                cursor_zeus.execute('''
                    SELECT TOP 1
                        sp.num_id, sp.tipo_id, sp.fecha_naci, sp.email,
                        sp.primer_nom, sp.segundo_nom, sp.primer_ape, sp.segundo_ape,
                        sp.sexo, sp.telefono
                    FROM sis_paci sp
                    WHERE sp.num_id = %s
                ''', [admision['IdPaciente']])
                paciente_zeus = cursor_zeus.fetchone()

            # Guardar paciente y examen en la base de datos hcresult
            with connections['hcresult'].cursor() as cursor:
                query_paciente = 'SELECT id FROM paciente WHERE documento = %s'
                cursor.execute(query_paciente, [admision['IdPaciente']])
                paciente_row = cursor.fetchone()

                if paciente_row:
                    paciente_id = paciente_row[0]
                else:
                    # Usar datos de ZeusSalud si están disponibles, si no usar admision_data del frontend
                    if paciente_zeus:
                        pnombre    = (paciente_zeus[4] or '').strip()
                        snombre    = (paciente_zeus[5] or '').strip()
                        papellido  = (paciente_zeus[6] or '').strip()
                        sapellido  = (paciente_zeus[7] or '').strip()
                        tipo_doc   = paciente_zeus[1] or ''
                        fecha_naci = paciente_zeus[2].strftime('%Y-%m-%d') if paciente_zeus[2] else ''
                        correo     = paciente_zeus[3] or ''
                        sexo       = paciente_zeus[8] or ''
                    else:
                        pnombre    = (admision.get('Nombre1') or '').strip()
                        snombre    = (admision.get('Nombre2') or '').strip()
                        papellido  = (admision.get('Apellido1') or '').strip()
                        sapellido  = (admision.get('Apellido2') or '').strip()
                        tipo_doc   = admision.get('TipoID', '')
                        fecha_naci = admision.get('FechaNacimiento', '')
                        correo     = admision.get('CorreoE', '')
                        sexo       = admision.get('SexoPaciente', '')

                    nombre_completo = " ".join(filter(None, [pnombre, snombre, papellido, sapellido]))
                    password_encrypted = bcrypt.hashpw(
                        fecha_naci.encode('utf-8') if fecha_naci else b'neurodx',
                        bcrypt.gensalt()
                    ).decode('utf-8')

                    cursor.execute('''
                        INSERT INTO paciente
                        (pnombre, snombre, papellido, sapellido, documento, tipo_documento,
                         fecha_nacimiento, correo, password, entidad_nombre, entidad_codigo,
                         nombre_completo, sexo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', [
                        pnombre, snombre, papellido, sapellido,
                        admision['IdPaciente'], tipo_doc,
                        fecha_naci, correo,
                        password_encrypted, admision.get('CodigoEntidad', ''), admision.get('CodigoEntidad', ''),
                        nombre_completo, sexo
                    ])
                    paciente_id = cursor.lastrowid

                # Insertar examen_resultado
                cursor.execute('''
                    INSERT INTO examen_resultado (examen, profesional, paciente, fecha_examen, fecha_resultado, estado, numero_ingreso)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', [
                    examen_id, 1, paciente_id,
                    fecha_examen, fecha_resultado, 1, admision['Consecutivo']
                ])
                examen_resultado_id = cursor.lastrowid

                # Insertar resultado_documento
                cursor.execute('''
                    INSERT INTO resultado_documento (resultado, archivo, tipo_archivo)
                    VALUES (%s, %s, %s)
                ''', [
                    examen_resultado_id, archivo_nombre_limpio, tipo_archivo
                ])

            # Guardar en tabla gestion documental con idpaciente en la ruta
            with connections['zeussalud'].cursor() as cursor_zeussalud:
                cursor_zeussalud.execute('''
                    INSERT INTO tblgestiondocumental 
                    (FechaCreado, NumeroPaciente, RutaArchivo, txtContenido, IdItem) 
                    VALUES (%s, %s, %s, %s, %s)
                ''', [
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    admision.get('NumeroPaciente', None),
                    f"{id_paciente}/{archivo_nombre_limpio}",
                    archivo_nombre_limpio, 5
                ])

            # Enviar a servidor compartido vía SMB
            server_ip = "192.168.1.92"
            smb_remote_path = f"{id_paciente}/{archivo_nombre_limpio}" 

            def copiar_archivo_smb(archivo_local, remote_path, server_ip):
                try:
                    remote_folder = os.path.dirname(remote_path)
                    subprocess.run([
                        "smbclient",
                        f"//{server_ip}/gdocumentalantares",
                        "-U", "neuroelectro%nedx2023",
                        "-c", f"mkdir \"{remote_folder}\""
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                    result = subprocess.run([
                        "smbclient",
                        f"//{server_ip}/gdocumentalantares",
                        "-U", "neuroelectro%nedx2023",
                        "-c", f"put \"{archivo_local}\" \"{remote_path}\""
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                    if result.returncode != 0:
                        raise Exception(result.stderr)
                except Exception as e:
                    raise Exception(f"Error al ejecutar smbclient: {str(e)}")

            copiar_archivo_smb(remote_file_path, smb_remote_path, server_ip)

            return Response({
                "success": True,
                "detail": f"Archivo '{archivo.name}' guardado exitosamente, paciente y documentos registrados.",
                "data": {
                    "paciente_id": paciente_id,
                    "examen_resultado_id": examen_resultado_id,
                    "archivo": archivo.name,
                    "ruta": f"{id_paciente}/{archivo_nombre_limpio}",
                    "smb_ruta": archivo_nombre_limpio
                }
            }, status=201)

        except Exception as e:
            return Response({
                "success": False,
                "detail": f"Error al procesar la solicitud: {str(e)}"
            }, status=500)


class CodigoCufePorFechasAPIView(APIView):
    def get(self, request, format=None):
        # Capturar los parámetros de la consulta desde la URL
        fecha_inicio = request.query_params.get('fecha_inicio')
        fecha_fin = request.query_params.get('fecha_fin')

        # Validar que se proporcionaron ambos parámetros
        if not fecha_inicio or not fecha_fin:
            return Response({
                "success": False,
                "detail": "Los parámetros 'fecha_inicio' y 'fecha_fin' son obligatorios."
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            with connections['zeussalud'].cursor() as cursor:
                # Consulta SQL para obtener los datos entre el rango de fechas con JOIN
                query = '''
                    SELECT 
                        tbldocumentosfe.AdmisionNo, 
                        tbldocumentosfe.rCUFE, 
                        tbldocumentosfe.Valor, 
                        tbldocumentosfe.Subtotal, 
                        tbldocumentosfe.FechaCreado,
                        facturas.FacturaNo,
                        facturas.Prefijo
                    FROM 
                        tbldocumentosfe
                    LEFT JOIN 
                        facturas 
                    ON 
                        tbldocumentosfe.AdmisionNo = facturas.AdmisionNo
                    WHERE 
                        tbldocumentosfe.FechaCreado BETWEEN %s AND %s
                '''
                cursor.execute(query, [fecha_inicio, fecha_fin])
                cufe_data = cursor.fetchall()

                # Validar si se encontraron resultados
                if cufe_data:
                    # Transformar los resultados en una lista de diccionarios
                    transformed_data = [
                        {
                            'AdmisionNo': row[0],
                            'rCUFE': row[1],
                            'Valor': row[2],
                            'Subtotal': row[3],
                            'FechaCreado': row[4],
                            'FacturaNo': row[5],  # Incluyendo FacturaNo
                            'Prefijo': row[6],    # Incluyendo Prefijo
                        }
                        for row in cufe_data
                    ]

                    response_data = {
                        "success": True,
                        "detail": f"Información encontrada entre {fecha_inicio} y {fecha_fin}",
                        "data": transformed_data
                    }
                    return Response(response_data, status=status.HTTP_200_OK)
                else:
                    # No se encontraron registros en el rango de fechas
                    response_data = {
                        "success": False,
                        "detail": f"No se encontró información entre {fecha_inicio} y {fecha_fin}",
                        "data": []
                    }
                    return Response(response_data, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Manejo de errores
            response_data = {
                "success": False,
                "detail": "Ocurrió un error al procesar la solicitud.",
                "error": str(e)
            }
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

          



class CodigoCufePorFechasAPIView(APIView):
    def get(self, request, format=None):
        # Capturar los parámetros de la consulta desde la URL
        fecha_inicio = request.query_params.get('fecha_inicio')
        fecha_fin = request.query_params.get('fecha_fin')

        # Validar que se proporcionaron ambos parámetros
        if not fecha_inicio or not fecha_fin:
            return Response({
                "success": False,
                "detail": "Los parámetros 'fecha_inicio' y 'fecha_fin' son obligatorios."
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            with connections['zeussalud'].cursor() as cursor:
                # Consulta SQL para obtener los datos entre el rango de fechas con JOIN
                query = '''
                    SELECT 
                        tbldocumentosfe.AdmisionNo, 
                        tbldocumentosfe.rCUFE, 
                        tbldocumentosfe.Valor, 
                        tbldocumentosfe.Subtotal, 
                        tbldocumentosfe.FechaCreado,
                        facturas.FacturaNo,
                        facturas.Prefijo
                    FROM 
                        tbldocumentosfe
                    LEFT JOIN 
                        facturas 
                    ON 
                        tbldocumentosfe.AdmisionNo = facturas.AdmisionNo
                    WHERE 
                        tbldocumentosfe.FechaCreado BETWEEN %s AND %s
                '''
                cursor.execute(query, [fecha_inicio, fecha_fin])
                cufe_data = cursor.fetchall()

                # Validar si se encontraron resultados
                if cufe_data:
                    # Transformar los resultados en una lista de diccionarios
                    transformed_data = [
                        {
                            'AdmisionNo': row[0],
                            'rCUFE': row[1],
                            'Valor': row[2],
                            'Subtotal': row[3],
                            'FechaCreado': row[4],
                            'FacturaNo': row[5],  # Incluyendo FacturaNo
                            'Prefijo': row[6],    # Incluyendo Prefijo
                        }
                        for row in cufe_data
                    ]

                    response_data = {
                        "success": True,
                        "detail": f"Información encontrada entre {fecha_inicio} y {fecha_fin}",
                        "data": transformed_data
                    }
                    return Response(response_data, status=status.HTTP_200_OK)
                else:
                    # No se encontraron registros en el rango de fechas
                    response_data = {
                        "success": False,
                        "detail": f"No se encontró información entre {fecha_inicio} y {fecha_fin}",
                        "data": []
                    }
                    return Response(response_data, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Manejo de errores
            response_data = {
                "success": False,
                "detail": "Ocurrió un error al procesar la solicitud.",
                "error": str(e)
            }
            return Response(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# Importa Q para facilitar las consultas ORM dinámicas si las necesitas
from django.db.models import Q
class CitasEstudiosAPIView(APIView):
    def get(self, request, format=None):
        try:
            
            fecha_inicial = request.query_params.get('fecha_inicial')
            fecha_final = request.query_params.get('fecha_final')

            if not fecha_inicial or not fecha_final:
                return Response({
                    "success": False,
                    "detail": "Debe proporcionar las fechas 'fecha_inicial' y 'fecha_final'."
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                fecha_inicial = datetime.strptime(fecha_inicial, '%Y-%m-%d')
                fecha_final = datetime.strptime(fecha_final, '%Y-%m-%d')
            except ValueError:
                return Response({
                    "success": False,
                    "detail": "El formato de las fechas debe ser 'YYYY-MM-DD'."
                }, status=status.HTTP_400_BAD_REQUEST)

            with connections['zeussalud'].cursor() as cursor_ipsndx:
                query_citas = '''
                    SELECT c.IdCita, c.AdmisionNo, c.FeCita, c.IdMedico, c.NumeroPaciente, p.CUPS, p.Cantidad
                    FROM citas c
                    INNER JOIN pxcita p ON c.IdCita = p.IdCita
                    LEFT JOIN facturas f ON c.AdmisionNo = f.AdmisionNo
                    WHERE c.FeCita >= %s AND c.FeCita <= %s
                    AND p.CUPS IN ('891704', '891703', '891402', '891901')
                    AND c.AdmisionNo != 0
                    AND (f.FacturaAnulada IS NULL OR f.FacturaAnulada != -1) 
                    AND (f.Prefijo IS NULL OR f.Prefijo != 'MGL')
                '''
                cursor_ipsndx.execute(query_citas, [fecha_inicial, fecha_final])
                registros_citas = cursor_ipsndx.fetchall()

                if not registros_citas:
                    return Response({
                        "success": False,
                        "detail": "No se encontraron registros para el rango de fechas proporcionado.",
                        "data": []
                    }, status=status.HTTP_404_NOT_FOUND)

                # Procesar resultados de la primera consulta
                citas_filtradas = []
                numeros_pacientes = []
                codigos_cups = []
                admisiones = []
                for registro in registros_citas:
                    cita = {
                        'IdCita': registro[0],
                        'AdmisionNo': registro[1],
                        'FeCita': registro[2],
                        'IdMedico': registro[3],
                        'NumeroPaciente': registro[4],
                        'CUPS': registro[5],
                        'Cantidad': registro[6],
                        'NCompleto': None,
                        'FechaNacimiento': None,
                        'CodigoEntidad': None,
                        'ResultadoArchivos': None,
                        'DescripcionCUPS': None,
                        'NombreMedico': None  # Nuevo campo
                    }
                    citas_filtradas.append(cita)
                    numeros_pacientes.append(registro[4])
                    codigos_cups.append(registro[5])
                    admisiones.append(registro[1])

            # Segunda consulta: obtener datos de pacientes
            with connections['zeussalud'].cursor() as cursor_pacientes:
                query_pacientes = '''
                    SELECT NumeroPaciente, NCompleto, EntidadPaciente, FechaNacimiento
                    FROM pacientes
                    WHERE NumeroPaciente IN ({})
                '''.format(','.join(['%s'] * len(numeros_pacientes)))
                cursor_pacientes.execute(query_pacientes, list(numeros_pacientes))
                registros_pacientes = cursor_pacientes.fetchall()

                pacientes_map = {
                    registro[0]: {'NCompleto': registro[1], 'EntidadPaciente': registro[2],  'FechaNacimiento': registro[3]}
                    for registro in registros_pacientes
                }

                for cita in citas_filtradas:
                    paciente_data = pacientes_map.get(cita['NumeroPaciente'], {})
                    cita['NCompleto'] = paciente_data.get('NCompleto')
                    cita['CodigoEntidad'] = paciente_data.get('EntidadPaciente')
                    cita['FechaNacimiento'] = paciente_data.get('FechaNacimiento')

            # Tercera consulta: buscar archivos en neurodx si hay admisiones
            if admisiones:
                with connections['default'].cursor() as cursor_neurodx:
                    query_archivos = '''
                        SELECT Admision_id, Tipo
                        FROM archivos
                        WHERE Admision_id IN ({}) AND Tipo = 'resultado'
                    '''.format(','.join(['%s'] * len(admisiones)))
                    cursor_neurodx.execute(query_archivos, list(admisiones))
                    archivos_resultados = cursor_neurodx.fetchall()

                    archivos_map = {row[0]: row[1] for row in archivos_resultados}

                    for cita in citas_filtradas:
                        cita['ResultadoArchivos'] = archivos_map.get(cita['AdmisionNo'])

            # Cuarta consulta: buscar descripción de CUPS en codigosoat
            with connections['zeussalud'].cursor() as cursor_codigosoat:
                query_codigosoat = '''
                    SELECT CodigoCUPS, DescripcionCUPS
                    FROM codigossoat
                    WHERE CodigoCUPS IN ({})
                '''.format(','.join(['%s'] * len(codigos_cups)))
                cursor_codigosoat.execute(query_codigosoat, list(codigos_cups))
                registros_codigosoat = cursor_codigosoat.fetchall()

                codigosoat_map = {
                    registro[0]: registro[1] for registro in registros_codigosoat
                }

                for cita in citas_filtradas:
                    cita['DescripcionCUPS'] = codigosoat_map.get(cita['CUPS'])

            # Quinta consulta: verificar NombreMedico en consolidado_estudios
            consolidado_map = {
                registro['Admision']: registro['NombreMedico']
                for registro in ConsolidadoEstudios.objects.filter(
                    Admision__in=admisiones
                ).values('Admision', 'NombreMedico')
            }

            for cita in citas_filtradas:
                cita['NombreMedico'] = consolidado_map.get(cita['AdmisionNo'])

            return Response({
                "success": True,
                "detail": "Registros encontrados para el rango de fechas proporcionado.",
                "data": citas_filtradas
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                "success": False,
                "detail": "Error al procesar la solicitud.",
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class UsuariosMedicosAPIView(APIView):
    def get(self, request, format=None):
        try:
            # Consulta para obtener usuarios con EsMedico = -1
            with connections['contabilidadndx'].cursor() as cursor:
                query = '''
                    SELECT IdUsuario, NombreReal
                    FROM usuarios
                    WHERE EsMedico = -1
                '''
                cursor.execute(query)
                registros = cursor.fetchall()

                if not registros:
                    return Response({
                        "success": False,
                        "detail": "No se encontraron usuarios que sean médicos.",
                        "data": []
                    }, status=status.HTTP_404_NOT_FOUND)

                # Transformar resultados en una lista de diccionarios
                usuarios_medicos = [
                    {
                        "IdUsuario": registro[0],
                        "NombreReal": registro[1],
                  
                    }
                    for registro in registros
                ]

                # Respuesta exitosa
                return Response({
                    "success": True,
                    "detail": "Usuarios médicos encontrados.",
                    "data": usuarios_medicos
                }, status=status.HTTP_200_OK)

        except Exception as e:
            # Manejo de errores
            return Response({
                "success": False,
                "detail": "Error al procesar la solicitud.",
                "error": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)





class GuardarConsolidadoEstudiosAPIView(APIView):
    def post(self, request, format=None):
        try:
            consolidado_estudios = request.data.get("consolidado_estudios", [])
            if not consolidado_estudios:
                return Response(
                    {"success": False, "detail": "No se proporcionaron datos para guardar."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for estudio in consolidado_estudios:
                ConsolidadoEstudios.objects.update_or_create(
                    Idcita=estudio["Idcita"],
                    defaults={
                        "Admision": estudio["Admision"],
                        "FechaCita": estudio["FechaCita"],
                        "IdMedico": estudio["ProfesionalId"], 
                        "NombreMedico": estudio["NombreReal"],  # Llena NombreMedico
                        "NumeroPaciente": estudio["NumeroPaciente"],
                        "Cups": estudio["Cups"],
                        "Cantidad": estudio["Cantidad"],
                        "NombreCompleto": estudio.get("NombreCompleto"),
                        "CodigoEntidad": estudio.get("CodigoEntidad"),
                        "ResultadoArchivo": estudio.get("ResultadoArchivo"),
                        "DescripcionCups": estudio.get("DescripcionCups"),
                    },
                )

            return Response(
                {"success": True, "detail": "Datos guardados correctamente."},
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response(
                {"success": False, "detail": "Error al guardar los datos.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )






class ConsolidadoEstudiosAsignadosAPIView(APIView):
    def get(self, request, format=None):
        try:
            # Obtener filtros de la solicitud
            fecha_inicio = request.query_params.get("fecha_inicio")
            fecha_fin = request.query_params.get("fecha_fin")
            nombre_medico = request.query_params.get("nombre_medico", "").strip()

            # Construir el query de filtros dinámicamente
            query = Q()

            # Filtrar por rango de fechas si están presentes ambos parámetros
            if fecha_inicio and fecha_fin:
                query &= Q(FechaCita__range=[fecha_inicio, fecha_fin])

            # Filtrar por NombreMedico si se proporciona
            if nombre_medico:
                query &= Q(NombreMedico__icontains=nombre_medico)

            # Obtener registros según el query
            registros = ConsolidadoEstudios.objects.filter(query)

            # Extraer los números de admisión de los registros
            admisiones_ids = registros.values_list("Admision", flat=True)

            # Consulta en la tabla 'archivos' para verificar resultados
            with connections['default'].cursor() as cursor_neurodx:
                query_archivos = '''
                    SELECT DISTINCT Admision_id
                    FROM archivos
                    WHERE Admision_id IN %s AND Tipo = 'resultado'
                '''
                cursor_neurodx.execute(query_archivos, [tuple(admisiones_ids)])
                admisiones_con_resultado = {row[0] for row in cursor_neurodx.fetchall()}

            # Filtrar los registros para excluir las admisiones que tienen resultado
            registros = registros.exclude(Admision__in=admisiones_con_resultado)

            # Agregar el estado de resultado a los registros
            registros_actualizados = []
            for registro in registros:
                registro_data = {
                    "Idcita": registro.Idcita,
                    "Admision": registro.Admision,
                    "FechaCita": registro.FechaCita.strftime("%Y-%m-%d"),
                    "IdMedico": registro.IdMedico,
                    "NombreMedico": registro.NombreMedico,
                    "NumeroPaciente": registro.NumeroPaciente,
                    "Cups": registro.Cups,
                    "Cantidad": registro.Cantidad,
                    "NombreCompleto": registro.NombreCompleto,
                    "CodigoEntidad": registro.CodigoEntidad,
                    "ResultadoArchivo": None,  # No tienen resultados
                    "DescripcionCups": registro.DescripcionCups,
                }
                registros_actualizados.append(registro_data)

            return Response({"success": True, "data": registros_actualizados}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"success": False, "detail": "Error al obtener los datos.", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

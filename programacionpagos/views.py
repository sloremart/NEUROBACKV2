import re
from django.db import connections
from django.db import connection
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from gedocumental.models import ArchivoFacturacion, ObservacionesArchivos, HistorialRevision
from neurodx import settings
from gedocumental.serializers import ArchivoFacturacionSerializer, ObservacionesArchivosSerializer
from programacionpagos.models import FacturaProgramacionPagos
from programacionpagos.serializers import FacturaprogramacionPagoSerializer
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
import os
import uuid
import hashlib
from datetime import datetime
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone
from datetime import date
from login.models import CustomUser

class ListaCuentas(APIView):
    def get(self, request):
        try:
            # Lista de cuentas específicas que queremos traer
            cuentas_permitidas = [
                '22050505', '23352505', '23352510', '23353001', 
                '23353002', '23353501', '23355505', '23359505'
            ]

            # Conexión a la base de datos
            with connections['contabilidad'].cursor() as cursor:
                # Construcción de la consulta con la cláusula WHERE
                query = f'''
                    SELECT Cuenta, Nombre 
                    FROM cuentas
                    WHERE Cuenta IN ({','.join(['%s'] * len(cuentas_permitidas))})
                '''
                
                cursor.execute(query, cuentas_permitidas)
                rows = cursor.fetchall()

                # Estructura los datos como un diccionario para convertir a JSON
                cuentas = [
                    {
                        'Cuenta': row[0],
                        'Nombre': row[1],
                    }
                    for row in rows
                ]

                # Devuelve los datos como respuesta JSON
                return Response(cuentas, status=status.HTTP_200_OK)

        except Exception as e:
            # Manejo de errores
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)




class ListaNits(APIView):
    def get(self, request):
        try:
            search_query = request.query_params.get('search', '')
            with connections['contabilidad'].cursor() as cursor:
                cursor.execute(
                    '''
                    SELECT TOP 50 IDTERCERO, RAZONCIAL
                    FROM PROVEEDORES
                    WHERE RAZONCIAL LIKE %s OR IDTERCERO LIKE %s
                    ORDER BY RAZONCIAL
                    ''',
                    ['%' + search_query + '%', '%' + search_query + '%']
                )
                rows = cursor.fetchall()

            nits = [
                {'CuentaNit': row[0], 'NombreNit': row[1]}
                for row in rows
            ]

            return Response(nits, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
from collections import defaultdict
from decimal import Decimal
class ListaFacturas(APIView):
    """
    Devuelve las facturas de proveedor que aún tienen saldo pendiente
    y NO están programadas para pago.
    """

    def get(self, request):
        try:
            # ------------------------------------------------------------------
            # Parámetros de consulta
            # ------------------------------------------------------------------
            cuenta = request.query_params.get("cuenta")
            nit = request.query_params.get("nit")
            fecha_inicial = request.query_params.get("fecha_inicial")

            cuentas_permitidas = [
                "22050505",
                "23352505",
                "23352510",
                "23353001",
                "23353002",
                "23353501",
                "23355505",
                "23359505",
            ]

            if not fecha_inicial:
                fecha_inicial = datetime(datetime.now().year, 1, 1).strftime("%Y-%m-%d")

            # ------------------------------------------------------------------
            # 1. Facturas ya programadas para pago
            # ------------------------------------------------------------------
            query_programadas = """
                SELECT DISTINCT TRIM(Cuenta), TRIM(UPPER(Documento)), TRIM(NIT)
                FROM facturas_programacion_pagos
            """
            with connections["default"].cursor() as cur:
                cur.execute(query_programadas)
                docs_programados = {
                    (c.strip(), d.strip().upper(), n.strip()) for c, d, n in cur.fetchall()
                }

            # ------------------------------------------------------------------
            # 2. Tabla contable tmpauxiliar → saldo por factura/cuenta
            # ------------------------------------------------------------------
            placeholders = ",".join(["%s"] * len(cuentas_permitidas))
            anio_actual = datetime.now().year

            query_tmpaux = f"""
                SELECT  Factura,
                        Cuenta,
                        SUM(Debito)  AS DebitoTotal,
                        SUM(Credito) AS CreditoTotal,
                        MAX(NIT)       AS NIT,
                        MAX(Documento) AS Documento
                FROM    contabilidadndx.tmpauxiliar
                WHERE   Periodo LIKE %s
                  AND   Cuenta IN ({placeholders})
                GROUP   BY Factura, Cuenta
            """
            params_tmpaux = [f"{anio_actual}%"] + cuentas_permitidas

            movimientos = {}
            with connections["contabilidadndx"].cursor() as cur:
                cur.execute(query_tmpaux, params_tmpaux)
                for fact, cta, deb, cred, nit_mov, doc_mov in cur.fetchall():
                    key = (fact.strip().upper(), str(cta))
                    movimientos[key] = {
                        "Debito": Decimal(deb or 0),
                        "Credito": Decimal(cred or 0),
                        "Documento": (doc_mov or "").strip(),
                        "Factura": fact.strip().upper(),
                        "Cuenta": cta,
                        "NIT": nit_mov,
                    }

            # ------------------------------------------------------------------
            # 3. Consulta de facturas (cabecera)
            # ------------------------------------------------------------------
            query_facturas = f"""
                SELECT  f.Cuenta,
                        c.Nombre        AS NombreCuenta,
                        f.NIT,
                        n.Nombre        AS NombreNIT,
                        f.Sucursal,
                        f.Documento,
                        f.Fecha,
                        f.FechaVence
                FROM    contabilidadndx.facturas f
                LEFT JOIN cuentas      c ON c.Cuenta   = f.Cuenta
                LEFT JOIN tabladenits  n ON n.IDTercero = f.NIT
                WHERE   f.Fecha >= %s
                  AND   f.Cuenta IN ({placeholders})
            """
            params_facturas = [fecha_inicial] + cuentas_permitidas
            if cuenta:
                query_facturas += " AND f.Cuenta = %s"
                params_facturas.append(cuenta)
            if nit:
                query_facturas += " AND f.NIT = %s"
                params_facturas.append(nit)

            # ------------------------------------------------------------------
            # 4. Utilitarios
            # ------------------------------------------------------------------
            def prioridad(fecha_vence, hoy):
                if not fecha_vence:
                    return "Sin vencimiento"
                if fecha_vence < hoy:
                    return "Alta Urgencia"

                a = hoy.year
                m = hoy.month

                if fecha_vence.year == a and fecha_vence.month == m:
                    d = fecha_vence.day
                    if d <= 10:
                        return "Pago Inmediato"
                    if d <= 20:
                        return "Mitad de Mes"
                    return "Final de Mes"

                # Siguiente mes
                m += 1
                if m > 12:
                    m, a = 1, a + 1

                if fecha_vence.year == a and fecha_vence.month == m:
                    return "Final de Mes"

                return "Sin Urgencia"

            hoy = datetime.now().date()
            facturas_pendientes = []

            # ------------------------------------------------------------------
            # 5. Recorremos las facturas, calculamos saldo y filtramos
            # ------------------------------------------------------------------
            with connections["contabilidadndx"].cursor() as cur:
                cur.execute(query_facturas, params_facturas)
                for (
                    cta,
                    nom_cta,
                    nit_cta,
                    nom_nit,
                    sucursal,
                    doc,
                    fecha,
                    fecha_vence,
                ) in cur.fetchall():
                    key = (doc.strip().upper(), str(cta))
                    mov = movimientos.get(key)
                    if not mov:
                        continue  # Sin movimientos contables

                    # ¿Ya está programada? (cuenta + documento + nit)
                    if (str(cta), key[0], str(nit_cta)) in docs_programados:
                        continue

                    saldo = mov["Credito"] - mov["Debito"]
                    if saldo <= 0:
                        continue  # saldada

                    # Intentamos extraer admisión del documento
                    try:
                        admision_id = int(re.sub(r"\D", "", mov["Documento"]))
                    except ValueError:
                        admision_id = 0

                    id_archivo = ruta_archivo = None
                    if admision_id:
                        with connections["default"].cursor() as cur2:
                            cur2.execute(
                                """
                                SELECT IdArchivo, RutaArchivo
                                FROM   archivos
                                WHERE  Tipo = 'FACTURAPROVEEDOR'
                                  AND  Admision_id = %s
                                ORDER  BY FechaCreacionArchivo DESC
                                LIMIT 1
                                """,
                                [admision_id],
                            )
                            file_row = cur2.fetchone()
                            if file_row:
                                id_archivo, ruta_archivo = file_row

                    facturas_pendientes.append(
                        {
                            "Id": f"{cta}-{nit_cta}-{mov['Factura']}",
                            "Factura": mov["Factura"],
                            "Cuenta": cta,
                            "NombreCuenta": nom_cta or "N/A",
                            "Nit": nit_cta,
                            "NombreNit": nom_nit or "N/A",
                            "Sucursal": sucursal,
                            "Documento": mov["Documento"],
                            "Fecha": fecha.strftime("%Y-%m-%d") if fecha else None,
                            "FechaVence": fecha_vence.strftime("%Y-%m-%d")
                            if fecha_vence
                            else None,
                            "Debito": str(mov["Debito"]),
                            "Credito": str(saldo),  # ← saldo pendiente
                            "Prioridad": prioridad(fecha_vence, hoy),
                            "Estado": "Pendiente",
                            "IdArchivo": id_archivo,
                            "RutaArchivo": ruta_archivo,
                        }
                    )

            # ------------------------------------------------------------------
            # 6. Respuesta
            # ------------------------------------------------------------------
            if not facturas_pendientes:
                return Response(
                    {"message": "No se encontraron registros"},
                    status=status.HTTP_204_NO_CONTENT,
                )

            return Response(facturas_pendientes, status=status.HTTP_200_OK)

        except Exception as exc:  # noqa: BLE001
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
       
from django.utils.timezone import localdate
from django.utils.timezone import datetime  
from django.utils.dateparse import parse_date
from django.utils.timezone import now

class FacturaProgramacionPagoCreateView(APIView):
    parser_classes = (JSONParser,)

    def post(self, request, format=None):
        """
        Servicio para crear facturas y almacenarlas en la base de datos.
        """
        try:
      

            cuenta = request.data.get('Cuenta')
            nit = request.data.get('NIT')
            sucursal = request.data.get('Sucursal')
            documento = request.data.get('Documento')
            fecha = request.data.get('Fecha')
            fecha_vence = request.data.get('FechaVence')
            debito = request.data.get('Debito', 0.00)
            credito = request.data.get('Credito', 0.00)
            prefijo = request.data.get('Prefijo', "")
            prioridad_alta = request.data.get('PrioridadAlta', False)
            prioridad_media = request.data.get('PrioridadMedia', False)
            prioridad_baja = request.data.get('PrioridadBaja', False)
            prioridad_inmediata = request.data.get('PrioridadInmediata', False)
            usuario_asignador_id = request.data.get('UsuarioAsignador')
            revision_financiera = request.data.get('RevisionFinanciera', False),
            fecha_comite_pago = request.data.get('FechaComitePago', None)

           
            cuenta_nombre = request.data.get('CuentaNombre', '').strip()
            nombre_nit = request.data.get('NombreNit', '').strip()

           
            if not cuenta_nombre:
                cuenta_nombre = "Sin nombre"

            print("✅ CuentaNombre después de extraer:", cuenta_nombre)  # Debugging

    
            fecha = parse_date(fecha) 
            fecha_vence = parse_date(fecha_vence)

            if not fecha or not fecha_vence:
                return JsonResponse({
                    "success": False,
                    "detail": "Error: Formato de fecha inválido. Asegúrese de usar YYYY-MM-DD."
                }, status=status.HTTP_400_BAD_REQUEST)

         
            fecha_creacion = now().date()  

       
            factura = FacturaProgramacionPagos(
                Cuenta=cuenta,
                NIT=nit,
                Sucursal=sucursal,
                Documento=documento,
                Fecha=fecha,
                FechaVence=fecha_vence,
                FechaCreado=fecha_creacion,
                Debito=debito,
                Credito=credito,
                Prefijo=prefijo,
                PrioridadAlta=prioridad_alta,
                PrioridadMedia=prioridad_media,
                PrioridadBaja=prioridad_baja,
                PrioridadInmediata=prioridad_inmediata,
                UsuarioAsignador_id=usuario_asignador_id,
                CuentaNombre=cuenta_nombre,  
                NombreNit=nombre_nit,
                RevisionFinanciera=revision_financiera[0],  
                FechaComitePago=fecha_comite_pago if fecha_comite_pago else None,  
            )

            print("🚀 Factura creada antes de guardar:", factura.__dict__)  # Debugging

            factura.save()

            
            factura_guardada = FacturaProgramacionPagos.objects.get(Id=factura.Id)
            print("✅ CuentaNombre después de guardar:", factura_guardada.CuentaNombre)  # Debugging

          
            serializer = FacturaprogramacionPagoSerializer(factura_guardada)

            response_data = {
                "success": True,
                "detail": "Factura creada exitosamente.",
                "data": serializer.data
            }

            return JsonResponse(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            response_data = {
                "success": False,
                "detail": f"Error interno: {str(e)}",
                "data": None
            }
            return JsonResponse(response_data, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FacturasPorPrioridadYFechasView(APIView):
    def get(self, request):
        """
        Obtiene facturas filtradas por prioridad y/o un rango de fechas.
        Si no se proporciona prioridad, devuelve todas las facturas en el rango de fechas.
        """
        try:
            prioridad = request.GET.get("prioridad", None)
            fecha_inicio = request.GET.get("fecha_inicio", None)
            fecha_fin = request.GET.get("fecha_fin", None)

            # Convertir fechas de string a objeto `date`
            fecha_inicio = parse_date(fecha_inicio) if fecha_inicio else None
            fecha_fin = parse_date(fecha_fin) if fecha_fin else None

            # Iniciar la consulta sin filtros
            facturas = FacturaProgramacionPagos.objects.filter(PagoTesoreria=False)

            # Filtrar por prioridad solo si se proporciona
            if prioridad:
                filtro_prioridad = {f"Prioridad{prioridad}": True}
                facturas = facturas.filter(**filtro_prioridad)

            # Aplicar rango de fechas si se proporciona
            if fecha_inicio and fecha_fin:
                facturas = facturas.filter(FechaVence__range=[fecha_inicio, fecha_fin])

            # Ordenar por fecha de vencimiento
            facturas = facturas.order_by("FechaVence")

            # Serializar datos
            serializer = FacturaprogramacionPagoSerializer(facturas, many=True)

            return Response({"success": True, "data": serializer.data}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"success": False, "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
from django.utils import timezone


class FacturaRevisionFinancieraView(APIView):
    parser_classes = (JSONParser,)

    def put(self, request, factura_id, format=None):
        """
        Servicio para actualizar el estado de `RevisionFinanciera` en una factura.
        Si se marca como `1`, se asigna la fecha actual en `FechaRevisionFinanciera` y `FechaComitePago`.
        """
        try:
      
            factura = get_object_or_404(FacturaProgramacionPagos, Id=factura_id)


            revision_financiera = request.data.get('RevisionFinanciera')


            if revision_financiera is None:
                return JsonResponse({
                    "success": False,
                    "detail": "Debe proporcionar un valor para 'RevisionFinanciera'."
                }, status=status.HTTP_400_BAD_REQUEST)


            revision_financiera = bool(int(revision_financiera))


            factura.RevisionFinanciera = revision_financiera


            fecha_actual = timezone.now().date()

            if revision_financiera:
                factura.FechaRevisionFinanciera = fecha_actual
                factura.FechaComitePago = fecha_actual 
            else:
                factura.FechaRevisionFinanciera = None  
                factura.FechaComitePago = None  

    
            factura.save()

            
            serializer = FacturaprogramacionPagoSerializer(factura)

            return JsonResponse({
                "success": True,
                "detail": "Estado de revisión financiera actualizado correctamente.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return JsonResponse({
                "success": False,
                "detail": f"Error interno: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ListaFacturasAprobadasFinancieramente(APIView):
    def get(self, request):
        try:
            cuenta = request.query_params.get('cuenta', None)
            nit = request.query_params.get('nit', None)
            fecha_inicial = request.query_params.get('fecha_inicial', None)
            fecha_final = request.query_params.get('fecha_final', None)

        

           
            if not fecha_inicial:
                fecha_inicial = datetime(datetime.now().year, 1, 1).strftime('%Y-%m-%d')

   
            if not fecha_final:
                fecha_final = datetime.now().strftime('%Y-%m-%d')

          

           
            query = '''
                SELECT 
                    f.Id,
                    f.Cuenta, 
                    f.CuentaNombre, 
                    f.NIT, 
                    f.NombreNit, 
                    f.Sucursal, 
                    f.Documento, 
                    f.Fecha,  
                    f.FechaVence,
                    f.Debito, 
                    f.Credito,
                    f.RevisionFinanciera,
                    f.FechaComitePago,
                    f.PagoTesoreria
                FROM facturas_programacion_pagos f
                WHERE f.Fecha BETWEEN %s AND %s  
                AND f.Credito > 0  
                AND COALESCE(f.Debito, 0) = 0  
                AND f.RevisionFinanciera = 1
                
            '''

            params = [fecha_inicial, fecha_final]  

            if cuenta:
                query += " AND f.Cuenta = %s"
                params.append(cuenta)

            if nit:
                query += " AND f.NIT = %s"
                params.append(nit)


            print(f"Consulta SQL generada: {query}")
            print(f"Parámetros SQL enviados: {params}")

      
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                rows = cursor.fetchall()

                facturas = [
                    {
                        'Id': row[0],
                        'Cuenta': row[1],
                        'NombreCuenta': row[2] if row[2] else 'N/A',
                        'Nit': row[3],
                        'NombreNit': row[4] if row[4] else 'N/A',
                        'Sucursal': row[5],
                        'Documento': str(row[6]),
                        'Fecha': row[7].strftime('%Y-%m-%d') if row[7] else None,  
                        'FechaVence': row[8].strftime('%Y-%m-%d') if row[8] else None,
                        'Debito': float(row[9]) if row[9] else 0.0,
                        'Credito': float(row[10]) if row[10] else 0.0,
                        'RevisionFinanciera': bool(row[11]),
                        'FechaComitePago': row[12].strftime('%Y-%m-%d') if row[12] else None,
                        'PagoTesoreria': bool(row[13]),
                    }
                    for row in rows
                ]

                if not facturas:
                    return Response({'message': 'No se encontraron facturas aprobadas financieramente en el rango especificado'}, status=status.HTTP_204_NO_CONTENT)

                return Response(facturas, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class FacturaPagoTesoreriaView(APIView):
    parser_classes = (JSONParser,)

    def put(self, request, factura_id, format=None):
       
        try:
      
            factura = get_object_or_404(FacturaProgramacionPagos, Id=factura_id)


            pago_tesoreria = request.data.get('PagoTesoreria')


            if pago_tesoreria is None:
                return JsonResponse({
                    "success": False,
                    "detail": "Debe proporcionar un valor para 'PagoTesoreria'."
                }, status=status.HTTP_400_BAD_REQUEST)


            pago_tesoreria = bool(int(pago_tesoreria))


            factura.PagoTesoreria = pago_tesoreria


            fecha_actual = timezone.now().date()

           

    
            factura.save()

            
            serializer = FacturaprogramacionPagoSerializer(factura)

            return JsonResponse({
                "success": True,
                "detail": "Estado de revisión financiera actualizado correctamente.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return JsonResponse({
                "success": False,
                "detail": f"Error interno: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        
class GenerarEgresoView(APIView):
    def get(self, request, factura_id):
        try:
           
            anio_actual = datetime.now().year

        
            cuentas_permitidas = [
                '22050505', '23352505', '23352510', '23353001',
                '23353002', '23353501', '23355505', '23359505'
            ]

          
            cuentas_str = "', '".join(cuentas_permitidas)

         
            query = f"""
                SELECT TipoDoc, Documento, Periodo, Cuenta, NIT, Factura, Debito, Credito, AdmisionNo, Registro, FechaVence, OrdenIngreso
                FROM contabilidadndx.tmpauxiliar
                WHERE Factura = %s 
                AND Cuenta IN ('{cuentas_str}')
                AND Periodo LIKE %s  
            """

            with connections['contabilidad'].cursor() as cursor:
                cursor.execute(query, [factura_id, f"{anio_actual}%"])
                columnas = [col[0] for col in cursor.description]
                resultados = [dict(zip(columnas, row)) for row in cursor.fetchall()]

            if not resultados:
                return Response({"message": "No se encontraron registros para la factura proporcionada en el año actual."}, status=status.HTTP_404_NOT_FOUND)

            tipo_docs = {str(item["TipoDoc"]) for item in resultados}

            if tipo_docs:
                
                query_docs = f"""
                    SELECT TipoDoc, Descripcion
                    FROM tiposdedocumento
                    WHERE TipoDoc IN ({", ".join(tipo_docs)})
                """

                with connections['contabilidad'].cursor() as cursor:
                    cursor.execute(query_docs)
                    tipo_doc_map = {str(row[0]): row[1] for row in cursor.fetchall()}

                
                for item in resultados:
                    item["DescripcionTipoDoc"] = tipo_doc_map.get(str(item["TipoDoc"]), "No encontrada")

            return Response({"success": True, "data": resultados}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class FacturaProveedorUploadView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, nit, format=None):
        try:
            user_id = request.data.get('userId')
            idRevisor = request.data.get('IdRevisor')
            id_revisor_tesoreria = request.data.get('IdRevisorTesoreria')
           
            id_revisor_cuentas_medicas = request.data.get('IdRevisorCuentasMedicas')
            tipo_documento = request.data.get('tipoDocumentos')
            tipo_hallazgo = request.data.get('TipoHallazgo', '')

            if not tipo_documento:
                return JsonResponse(
                    {"success": False, "detail": "El tipo de documento es obligatorio."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 💡 Limpiar NIT antes de usarlo
            nit_limpio = re.sub(r'\D', '', nit)
            periodo = datetime.now().strftime("%Y%m")
            identificador = int(f"{nit_limpio}{periodo}")

            archivos = request.FILES.getlist('files')
            archivos_guardados = []

            for archivo in archivos:
                base_name, ext = os.path.splitext(archivo.name)
                unique_filename = f"{base_name}_{uuid.uuid4().hex[:8]}{ext}"

                carpeta_fisica = os.path.join(
                    settings.MEDIA_ROOT,
                    'gdocumental',
                    'facturasProveedor',
                    f"{nit_limpio}-{periodo}"
                )
                os.makedirs(carpeta_fisica, exist_ok=True)

                archivo_path = os.path.join(carpeta_fisica, unique_filename)
                with open(archivo_path, 'wb') as f:
                    for chunk in archivo.chunks():
                        f.write(chunk)

                ruta_relativa = os.path.join(
                    'gdocumental',
                    'facturasProveedor',
                    f"{nit_limpio}-{periodo}",
                    unique_filename
                )

                archivo_obj = ArchivoFacturacion(
                    Admision_id=0,
                    NumeroAdmision=identificador,
                    Tipo=tipo_documento,
                    RutaArchivo=ruta_relativa,
                    NombreArchivo=unique_filename,
                    FechaCreacionArchivo=datetime.now().replace(second=0, microsecond=0),
                    Usuario_id=user_id,
                    IdRevisor=idRevisor,
                    IdRevisorTesoreria=id_revisor_tesoreria or None,
                    UsuarioCuentasMedicas_id=id_revisor_cuentas_medicas or None,
                    FechaCreacionAntares=None,
                    TipoHallazgo=tipo_hallazgo,
                    RevisionPrimera=False,
                    RevisionSegunda=False,
                    RevisionTercera=False,
                )
                archivo_obj.save(using='default')

                archivos_guardados.append({
                    "id": archivo_obj.IdArchivo,
                    "ruta": str(archivo_obj.RutaArchivo),
                    "nombre": archivo_obj.NombreArchivo
                })

            return JsonResponse(
                {
                    "success": True,
                    "detail": (
                        f"Facturas guardadas exitosamente para el proveedor "
                        f"{nit_limpio} en {periodo}"
                    ),
                    "data": archivos_guardados,
                },
                status=status.HTTP_201_CREATED
            )

        except Exception as e:
            return JsonResponse(
                {"success": False, "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        

        

class FacturasRevisorView(APIView):
    def get(self, request, id_revisor):
        try:
            # Obtener todas las facturas que corresponden al revisor
            facturas = (
                ArchivoFacturacion.objects
                .filter(Tipo="FACTURAPROVEEDOR")
                .filter(
                    # 1️⃣ Revisor principal
                    Q(IdRevisor=id_revisor)
                    |
                    # 2️⃣ Revisor Tesorería
                    Q(IdRevisorTesoreria=id_revisor)
                    |
                    # 3️⃣ Revisor Cuentas Médicas
                    Q(UsuarioCuentasMedicas_id=id_revisor)
                )
                .order_by("-FechaCreacionArchivo")
            )

            # Separar facturas PENDIENTES e HISTÓRICAS
            facturas_pendientes = []
            facturas_historicas = []

            for factura in facturas:
                # Determinar el rol del usuario actual para esta factura
                es_primer_revisor = factura.IdRevisor == id_revisor
                es_segundo_revisor = factura.IdRevisorTesoreria == id_revisor
                es_tercer_revisor = factura.UsuarioCuentasMedicas_id == id_revisor

                # Verificar si el usuario ya tomó alguna acción en esta factura
                ya_reviso = False
                if es_primer_revisor and factura.FechaRevisionPrimera:
                    ya_reviso = True
                elif es_segundo_revisor and factura.FechaRevisionSegunda:
                    ya_reviso = True
                elif es_tercer_revisor and factura.FechaRevisionTercera:
                    ya_reviso = True

                if ya_reviso:
                    # Si ya revisó (aprobó o rechazó), va al historial
                    facturas_historicas.append(factura)
                else:
                    # Si no ha revisado, verificar si debe estar en pendientes
                    if es_primer_revisor:
                        # Primer revisor: pendiente SOLO si NO ha revisado
                        if not factura.RevisionPrimera:
                            facturas_pendientes.append(factura)

                    elif es_segundo_revisor:
                        # Segundo revisor: pendiente SOLO si el primero aprobó Y él no ha revisado
                        if factura.RevisionPrimera and not factura.RevisionSegunda:
                            facturas_pendientes.append(factura)

                    elif es_tercer_revisor:
                        # Tercer revisor: pendiente SOLO si los dos primeros aprobaron Y él no ha revisado Y no está rechazada
                        if (factura.RevisionPrimera and 
                            factura.RevisionSegunda and 
                            not factura.RevisionTercera and
                            not factura.FechaRechazo):
                            facturas_pendientes.append(factura)

            # Serializar pendientes e históricas
            serializer_pendientes = ArchivoFacturacionSerializer(facturas_pendientes, many=True)
            serializer_historicas = ArchivoFacturacionSerializer(facturas_historicas, many=True)

            return Response(
                {
                    "success": True,
                    "data": {
                        "pendientes": serializer_pendientes.data,
                        "historicas": serializer_historicas.data
                    }
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


from rest_framework.decorators import api_view

class RevisarFacturaProveedorView(APIView):
    def post(self, request, id_archivo):
        try:
            aprobado    = request.data.get("aprobado")      # 0 | 1
            usuario_id  = request.data.get("usuario_id")    # ID del usuario revisor
            descripcion = request.data.get("descripcion", "").strip()

            if aprobado is None or usuario_id is None:
                return Response(
                    {"message": "Faltan campos requeridos."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            archivo = ArchivoFacturacion.objects.get(IdArchivo=id_archivo)
            fecha_actual = timezone.now()

            # 1️⃣ Revisor principal
            if int(usuario_id) == archivo.IdRevisor:
                archivo.RevisionPrimera = bool(aprobado)
                # SIEMPRE llenar la fecha de revisión, independientemente de si aprueba o rechaza
                archivo.FechaRevisionPrimera = fecha_actual
                if aprobado:
                    archivo.EstadoRevision = 'EN_REVISION'  # Cambia a en revisión para segunda aprobación
                else:
                    archivo.EstadoRevision = 'RECHAZADA'
                    archivo.FechaRechazo = fecha_actual
                    # Obtener el usuario real
                    usuario_revisor = CustomUser.objects.get(id=usuario_id)
                    archivo.UsuarioRechazo = usuario_revisor

                # Crear registro en historial
                HistorialRevision.objects.create(
                    archivo=archivo,
                    usuario_id=usuario_id,
                    accion='APROBAR' if aprobado else 'RECHAZAR',
                    observacion=descripcion,
                    nivel_revision='PRIMERA'
                )

            # 2️⃣ Revisor Tesorería (solo si 1ª aprobada)
            elif (
                int(usuario_id) == archivo.IdRevisorTesoreria
                and archivo.RevisionPrimera
            ):
                archivo.RevisionSegunda = bool(aprobado)
                # SIEMPRE llenar la fecha de revisión, independientemente de si aprueba o rechaza
                archivo.FechaRevisionSegunda = fecha_actual
                if aprobado:
                    archivo.EstadoRevision = 'APROBADA'  # Cambia a aprobada
                else:
                    archivo.EstadoRevision = 'RECHAZADA'
                    archivo.FechaRechazo = fecha_actual
                    # Obtener el usuario real
                    usuario_revisor = CustomUser.objects.get(id=usuario_id)
                    archivo.UsuarioRechazo = usuario_revisor

                # Crear registro en historial
                HistorialRevision.objects.create(
                    archivo=archivo,
                    usuario_id=usuario_id,
                    accion='APROBAR' if aprobado else 'RECHAZAR',
                    observacion=descripcion,
                    nivel_revision='SEGUNDA'
                )

            # 3️⃣ Revisor Cuentas Médicas (solo si 1ª y 2ª aprobadas)
            elif (
                int(usuario_id) == archivo.UsuarioCuentasMedicas_id
                and archivo.RevisionPrimera
                and archivo.RevisionSegunda
            ):
                archivo.RevisionTercera = bool(aprobado)
                # SIEMPRE llenar la fecha de revisión, independientemente de si aprueba o rechaza
                archivo.FechaRevisionTercera = fecha_actual
                if aprobado:
                    archivo.EstadoRevision = 'APROBADA'  # Cambia a aprobada final
                else:
                    archivo.EstadoRevision = 'RECHAZADA'
                    archivo.FechaRechazo = fecha_actual
                    # Obtener el usuario real
                    usuario_revisor = CustomUser.objects.get(id=usuario_id)
                    archivo.UsuarioRechazo = usuario_revisor

                # Crear registro en historial
                HistorialRevision.objects.create(
                    archivo=archivo,
                    usuario_id=usuario_id,
                    accion='APROBAR' if aprobado else 'RECHAZAR',
                    observacion=descripcion,
                    nivel_revision='TERCERA'
                )

            # Sin permisos / orden incorrecto
            else:
                return Response(
                    {
                        "message": (
                            "El usuario no tiene permisos para revisar esta "
                            "factura o no cumple el orden de revisiones."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Observación opcional
            if descripcion:
                ObservacionesArchivos.objects.create(
                    FechaObservacion=now(),
                    Descripcion=descripcion,
                    IdArchivo_id=archivo.IdArchivo,
                )

            archivo.save()
            return Response(
                {"message": "Revisión registrada correctamente."},
                status=status.HTTP_200_OK,
            )

        except ArchivoFacturacion.DoesNotExist:
            return Response(
                {"message": "Archivo no encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        
from django.db.models import Q

class FacturasUsuarioFiltradasView(APIView):
    def get(self, request, id_usuario): 
        fecha_inicio = request.query_params.get("fecha_inicio")
        fecha_fin = request.query_params.get("fecha_fin")
        estado = request.query_params.get("estado")  
        numero_admision = request.query_params.get("numero_admision")

        try:
            print("Fecha inicio:", fecha_inicio)
            print("Fecha fin:", fecha_fin)

            # Solo facturas del tipo FACTURAPROVEEDOR
            facturas = ArchivoFacturacion.objects.filter(Tipo="FACTURAPROVEEDOR")

            if fecha_inicio and fecha_fin:
                facturas = facturas.filter(
                    FechaCreacionArchivo__date__range=(fecha_inicio, fecha_fin)
                )

            if estado == "aprobada":
                facturas = facturas.filter(RevisionPrimera=True)
            elif estado == "rechazada":
                facturas = facturas.filter(RevisionPrimera=False, IdRevisor=0)
            elif estado == "pendiente":
                facturas = facturas.filter(RevisionPrimera=False).exclude(IdRevisor=0)

            if numero_admision:
                numero_admision_limpio = numero_admision.replace(".", "")
                facturas = facturas.filter(
                    Q(NumeroAdmision__icontains=numero_admision)
                    | Q(NumeroAdmision__icontains=numero_admision_limpio)
                )

            facturas = facturas.order_by("-FechaCreacionArchivo")
            print("Total facturas encontradas:", facturas.count())

            data_response = []
            for factura in facturas:
                observaciones = ObservacionesArchivos.objects.filter(
                    IdArchivo_id=factura.IdArchivo
                ).order_by("-FechaObservacion")

                data_response.append({
                    "factura": ArchivoFacturacionSerializer(factura).data,
                    "estado": self._get_estado(factura.RevisionPrimera),
                    "observaciones": ObservacionesArchivosSerializer(observaciones, many=True).data,
                })

            return Response({"success": True, "data": data_response}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"success": False, "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _get_estado(self, revision_primera):
        if revision_primera is True:
            return "aprobada"
        elif revision_primera is False:
            return "rechazada"
        return "pendiente"


@api_view(["PATCH"])
def actualizar_numero_egreso(request, id_archivo):
    try:
        numero_egreso = request.data.get("numero_documento")
        if not numero_egreso:
            return Response(
                {"success": False, "detail": "Debe enviar el número de egreso."},
                status=status.HTTP_400_BAD_REQUEST
            )

        archivo = ArchivoFacturacion.objects.get(IdArchivo=id_archivo)
        archivo.Admision_id = numero_egreso
        archivo.save()

        return Response(
            {"success": True, "detail": "Número de egreso actualizado correctamente."},
            status=status.HTTP_200_OK
        )

    except ArchivoFacturacion.DoesNotExist:
        return Response(
            {"success": False, "detail": "Factura no encontrada."},
            status=status.HTTP_404_NOT_FOUND
        )

    except Exception as e:
        return Response(
            {"success": False, "detail": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

class FacturaProveedorDeleteView(APIView):
    def delete(self, request, id_archivo):  # <- aquí debe estar el argumento
        try:
            archivo = ArchivoFacturacion.objects.filter(IdArchivo=id_archivo).first()

            if not archivo:
                return Response({"success": False, "detail": "Archivo no encontrado."}, status=status.HTTP_404_NOT_FOUND)

            ruta_fisica = os.path.join(settings.MEDIA_ROOT, archivo.RutaArchivo.name)

            if os.path.exists(ruta_fisica):
                os.remove(ruta_fisica)

            archivo.delete()

            return Response({"success": True, "detail": "Archivo eliminado correctamente."}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"success": False, "detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from rest_framework import serializers
from datetime import datetime, timedelta
from controlfacturacion.serializers import CodigoSoatSerializer, DetalleFacturaSerializer
from .models import DetalleFactura, CodigoSoat
from rest_framework import generics
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.views import APIView
from .models import DetalleFactura
from django.db import connections
from rest_framework import status
from collections import OrderedDict
from rest_framework.response import Response
from collections import defaultdict
class DateSerializerField(serializers.Field):
    
    def to_representation(self, value):
        return value.date()

class DetalleFacturaSerializer(serializers.ModelSerializer):
    FechaServicio = DateSerializerField(read_only=True)

    class Meta:
        model = DetalleFactura
        fields = '__all__'
from datetime import datetime


        






class FiltroDetalleFacturaPorFecha(APIView):       

    def get(self, request):
        fecha_servicio_str = request.query_params.get('FechaServicio', None)
        
        if fecha_servicio_str:
            try:
                fecha_servicio = datetime.strptime(fecha_servicio_str, '%Y-%m-%d')
                fecha_inicio = fecha_servicio.replace(hour=0, minute=0, second=0)
                fecha_fin = fecha_inicio + timedelta(days=1) - timedelta(seconds=1)

                # ZeusSalud: detallefactura → sis_deta
                detalles_factura = []
                with connections['zeussalud'].cursor() as cursor:
                    cursor.execute('''
                        SELECT sd.fuente_tips, sd.fecha_servicio, sd.cod_servicio, sd.cups,
                               sd.cantidad, sd.vlr_servicio, sd.num_servicio, sd.ccosto
                        FROM sis_deta sd
                        WHERE sd.fecha_servicio BETWEEN %s AND %s
                    ''', [fecha_inicio, fecha_fin])
                    detalles_factura = cursor.fetchall()

                # [0]=fuente_tips(AdmisionNo), [1]=fecha_servicio, [2]=cod_servicio, [3]=cups,
                # [4]=cantidad, [5]=vlr_servicio, [6]=num_servicio, [7]=ccosto
                data = [{
                    'AdmisionNo': detalle[0],
                    'FechaServicio': detalle[1].strftime('%Y-%m-%d') if detalle[1] is not None else None,
                    'IDServicio': detalle[2],
                    'CodigoCUPS': detalle[3],
                    'CodigoSOAT': None,
                    'CodigoISS': None,
                    'Cantidad': detalle[4],
                    'ValorUnitario': detalle[5],
                    'FacturaNo': None,
                    'RegistroGlosa': None,
                    'IdEspecialista': None,
                    'CreadoPor': None,
                    'ModificadoPor': None,
                    'FechaCreado': None,
                    'FechaModificado': None,
                    'VrUnitarioCompartido': None,
                    'VrPorCopago': None,
                    'VrPorCuota': None,
                    'OrdenNo': detalle[6],
                    'Ccosto': detalle[7]
                } for detalle in detalles_factura]

                return Response(data)
            except ValueError:
                return Response({'error': 'Formato de fecha incorrecto. Use el formato YYYY-MM-DD.'}, status=400)
        else:
            return Response({'error': 'Se necesita proporcionar una fecha de servicio.'}, status=400)


class FiltroFacturaPorFecha(APIView):       

    def get(self, request):
        fecha_servicio_str = request.query_params.get('Fecha', None)
        
        if fecha_servicio_str:
            try:
                fecha_servicio = datetime.strptime(fecha_servicio_str, '%Y-%m-%d')
                fecha_inicio = fecha_servicio.replace(hour=0, minute=0, second=0)
                fecha_fin = fecha_inicio + timedelta(days=1) - timedelta(seconds=1)

                # ZeusSalud: facturas → sis_maes (contiene nro_factura, TotalFactura, fecha_ing)
                factura = []
                with connections['zeussalud'].cursor() as cursor:
                    cursor.execute('''
                        SELECT con_estudio, fecha_ing, TotalFactura, nro_factura, fecha_ing
                        FROM sis_maes
                        WHERE CONVERT(date, fecha_ing) BETWEEN %s AND %s
                    ''', [fecha_inicio.date(), fecha_fin.date()])
                    factura = cursor.fetchall()

                data = [{
                    'AdmisionNo': detalle[0],
                    'Fecha': detalle[1].strftime('%Y-%m-%d') if detalle[1] is not None else None,
                    'TotalFactura': detalle[2],
                    'FacturaNo': detalle[3],
                    'TotalTerceros': None,
                    'FechaAdmision': detalle[4].strftime('%Y-%m-%d') if detalle[4] is not None else None,
                } for detalle in factura]

                return Response(data)
            except ValueError:
                return Response({'error': 'Formato de fecha incorrecto. Use el formato YYYY-MM-DD.'}, status=400)
        else:
            return Response({'error': 'Se necesita proporcionar una fecha de servicio.'}, status=400)
        



class CiPxApiView(APIView):
    def get(self, request, format=None):
        fecha_inicio_str = request.GET.get('fecha_inicio', None)
        fecha_fin_str = request.GET.get('fecha_fin', None)
        
        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({"error": "Formato de fecha inválido"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Se requieren las fechas de inicio y fin"}, status=status.HTTP_400_BAD_REQUEST)
        
        citas_por_rango = {}
        delta = timedelta(days=1)
        fecha_actual = fecha_inicio
        while fecha_actual <= fecha_fin:
            citas_por_rango[fecha_actual.strftime('%Y-%m-%d')] = self.get_citas_por_dia(fecha_actual)
            fecha_actual += delta

        consolidated_data = self.calculate_consolidated_data(citas_por_rango)

        response_data = {
            "success": True,
            "detail": "Las citas programadas por día en el rango de fechas son las siguientes",
            "data": citas_por_rango,
            "consolidated_data": consolidated_data
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def get_citas_por_dia(self, fecha):
        fecha_inicio = datetime.combine(fecha, datetime.min.time())
        fecha_fin = datetime.combine(fecha, datetime.max.time())
        with connections['zeussalud'].cursor() as cursor:
            # ZeusSalud: citas→citas_procedimientos→sis_proc
            # VrUnitario no existe directo; se retorna 0 (pendiente optimización con sis_proc.manual_XX)
            query = '''
                SELECT DISTINCT c.id, 0 AS VrUnitario, cp.Cantidad, sp.cups,
                       sp.codigo AS RegistroNo, cp.Servicio, sp.nombreve AS DescripcionCUPS
                FROM citas c
                JOIN citas_procedimientos cp ON c.id = cp.id_cita
                LEFT JOIN sis_proc sp ON cp.id_procedimiento = sp.codigo
                WHERE c.fecha BETWEEN %s AND %s AND c.estado != 'CA'
            '''
            cursor.execute(query, [fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

            citas_data = []
            for row in rows:
                cita = {
                    'IdCita': row[0],
                    'VrUnitario': row[1],
                    'Cantidad': row[2],
                    'CUPS': row[3],
                    'RegistroNo': row[4],
                    'Servicio': str(row[5]) if row[5] is not None else None,
                    'DescripcionCUPS': row[6],
                    'ValorTotal': row[1] * row[2]
                }
                citas_data.append(cita)

        citas_data_filtered = []
        processed_ids = set()
        for cita in citas_data:
            id_cita = cita['IdCita']
            cups = cita['CUPS']
            if id_cita not in processed_ids:
                citas_data_filtered.append(cita)
                processed_ids.add(id_cita)
            else:
                cups_set = set(cita['CUPS'] for cita in citas_data_filtered if cita['IdCita'] == id_cita)
                if cups not in cups_set:
                    citas_data_filtered.append(cita)

        return citas_data_filtered

    def calculate_consolidated_data(self, citas_por_rango):
        consolidated_data = defaultdict(int)
        servicio_categoria = {
            "1": "Radiografia",
            "2": "Ultrasonografia",
            "3": "Doppler",
            "4": "Electroencefalograma",
            "5": "Electromiografia",
            "6": "EstudioPoliSinOximetria",
            "7": "EstudioPoliConOximetria",
            "8": "Monitorizacion",
            "9": "Neuroconduccion",
            "10": "PolisonografiaTitulacion",
            "11": "Potenciales",
            "14": "Neurologia",
            "15": "ServicioRadiologia",
            "16": "InyeccionMiorelajante",
            "17": "Fisiatria",
            "18": "Infiltraciones",
            "19": "BloqueoFisiatria",
            "20": "Bloqueoneurologia",
            "21": "TomografiaContrastada",
            "22": "TomografiaSimple",
            "23": "InsumoTomografia",
            "24": "MedicamentoTac"
        }
        for categoria in servicio_categoria.values():
         consolidated_data[categoria] = 0
        

        for fecha, citas in citas_por_rango.items():
            for cita in citas:
                categoria = servicio_categoria.get(cita['Servicio'], 'Otros')
                consolidated_data[categoria] += cita['ValorTotal']

        return consolidated_data


class CitasSubcentroApiView(APIView):
    def get(self, request, format=None):
        fecha_inicio_str = request.GET.get('fecha_inicio', None)
        fecha_fin_str = request.GET.get('fecha_fin', None)
        
        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({"error": "Formato de fecha inválido"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Se requieren las fechas de inicio y fin"}, status=status.HTTP_400_BAD_REQUEST)
        
        citas_por_rango = {}
        delta = timedelta(days=1)
        fecha_actual = fecha_inicio
        while fecha_actual <= fecha_fin:
            citas_por_rango[fecha_actual.strftime('%Y-%m-%d')] = self.get_citas_por_dia(fecha_actual)
            fecha_actual += delta

        consolidated_data = self.calculate_consolidated_data(citas_por_rango)

        response_data = {
            "success": True,
            "detail": "Las citas programadas por día en el rango de fechas son las siguientes",
            "data": citas_por_rango,
            "consolidated_data": consolidated_data
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def get_citas_por_dia(self, fecha):
        fecha_inicio = datetime.combine(fecha, datetime.min.time())
        fecha_fin = datetime.combine(fecha, datetime.max.time())
        with connections['zeussalud'].cursor() as cursor:
            query = '''
                SELECT DISTINCT c.id, 0 AS VrUnitario, cp.Cantidad, sp.cups,
                       sp.codigo AS RegistroNo, cp.Servicio, sp.nombreve AS DescripcionCUPS
                FROM citas c
                JOIN citas_procedimientos cp ON c.id = cp.id_cita
                LEFT JOIN sis_proc sp ON cp.id_procedimiento = sp.codigo
                WHERE c.fecha BETWEEN %s AND %s AND c.estado != 'CA'
            '''
            cursor.execute(query, [fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

            citas_data = []
            for row in rows:
                cita = {
                    'IdCita': row[0],
                    'VrUnitario': row[1],
                    'Cantidad': row[2],
                    'CUPS': row[3],
                    'RegistroNo': row[4],
                    'Servicio': str(row[5]) if row[5] is not None else None,
                    'DescripcionCUPS': row[6],
                    'ValorTotal': row[1] * row[2]
                }
                citas_data.append(cita)

        citas_data_filtered = []
        processed_ids = set()
        for cita in citas_data:
            id_cita = cita['IdCita']
            cups = cita['CUPS']
            if id_cita not in processed_ids:
                citas_data_filtered.append(cita)
                processed_ids.add(id_cita)
            else:
                cups_set = set(cita['CUPS'] for cita in citas_data_filtered if cita['IdCita'] == id_cita)
                if cups not in cups_set:
                    citas_data_filtered.append(cita)

        return citas_data_filtered

    def calculate_consolidated_data(self, citas_por_rango):
        consolidated_data = defaultdict(int)
        servicio_categoria = {
            "1": "ImagenesBajaComplejidad",
            "2": "ImagenesBajaComplejidad",
            "3": "ImagenesBajaComplejidad",
            "4": "EstudioSueño",
            "6": "EstudioSueño",
            "7": "EstudioSueño",
            "8": "EstudioSueño",
            "10": "EstudioSueño",
            "14": "Neurologia",
            "16": "Neurologia",
            "20": "Neurologia",
            "21": "AltaComplejidad",
            "22": "AltaComplejidad",
            "23": "AltaComplejidad",
            "24": "AltaComplejidad",
            "5": "Fisiatria",
            "9": "Fisiatria",
            "11": "Fisiatria",
            "17": "Fisiatria",
            "18": "Fisiatria",
            "19": "Fisiatria"
        }

        for fecha, citas in citas_por_rango.items():
            for cita in citas:
                categoria = servicio_categoria.get(cita['Servicio'], 'Otros')
                consolidated_data[categoria] += cita['ValorTotal']

        return consolidated_data
    


##### SERVICIO PARA AGENDAS#######
class AgendasView(APIView):
    def get(self, request, format=None):
        fecha_inicio_str = request.GET.get('fecha_inicio', None)
        fecha_fin_str = request.GET.get('fecha_fin', None)
        
        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
            except ValueError:
                return Response({"error": "Formato de fecha inválido"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Se requieren las fechas de inicio y fin"}, status=status.HTTP_400_BAD_REQUEST)
        
        citas_por_rango = {}
        delta = timedelta(days=1)
        fecha_actual = fecha_inicio
        while fecha_actual <= fecha_fin:
            citas_por_rango[fecha_actual.strftime('%Y-%m-%d')] = self.get_citas_por_dia(fecha_actual)
            fecha_actual += delta

        consolidated_data = self.calculate_consolidated_data(citas_por_rango)

        response_data = {
            "success": True,
            "detail": "Las citas programadas por día en el rango de fechas son las siguientes",
            "data": citas_por_rango,
            "consolidated_data": consolidated_data
        }

        return Response(response_data, status=status.HTTP_200_OK)

    def get_citas_por_dia(self, fecha):
        fecha_inicio = datetime.combine(fecha, datetime.min.time())
        fecha_fin = datetime.combine(fecha, datetime.max.time())
        with connections['zeussalud'].cursor() as cursor:
            # ZeusSalud: cita.Agenda → c.asunto (tipo de consulta)
            query = '''
                SELECT DISTINCT c.id, c.asunto AS Agenda, 0 AS VrUnitario, cp.Cantidad, sp.cups,
                       sp.codigo AS RegistroNo, cp.Servicio, sp.nombreve AS DescripcionCUPS
                FROM citas c
                JOIN citas_procedimientos cp ON c.id = cp.id_cita
                LEFT JOIN sis_proc sp ON cp.id_procedimiento = sp.codigo
                WHERE c.fecha BETWEEN %s AND %s AND c.estado != 'CA'
            '''
            cursor.execute(query, [fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

            citas_data = []
            for row in rows:
                cita = {
                    'IdCita': row[0],
                    'VrUnitario': row[2],
                    'Cantidad': row[3],
                    'Agenda': row[1],
                    'CUPS': row[4],
                    'RegistroNo': row[5],
                    'Servicio': str(row[6]) if row[6] is not None else None,
                    'DescripcionCUPS': row[7],
                    'ValorTotal': row[2] * row[3]
                }
                citas_data.append(cita)
        
        # Procesamiento adicional para filtrar los CUPS duplicados por cada cita
        citas_data_filtered = []
        processed_ids = set()  # Para realizar seguimiento de los IdCita procesados

        for cita in citas_data:
            id_cita = cita['IdCita']
            cups = cita['CUPS']
            if id_cita not in processed_ids:
                citas_data_filtered.append(cita)
                processed_ids.add(id_cita)
            else:
                # Verificar si el CUPS ya ha sido registrado para esta cita
                cups_set = set(cita['CUPS'] for cita in citas_data_filtered if cita['IdCita'] == id_cita)
                if cups not in cups_set:
                    citas_data_filtered.append(cita)

        return citas_data_filtered
    
    def calculate_consolidated_data(self, citas_por_rango):
        consolidated_data = defaultdict(int)
        servicio_categoria = {
            "1": "ImagenesBajaComplejidad",
            "2": "ImagenesBajaComplejidad",
            "3": "ImagenesBajaComplejidad",
            "4": "EstudioSueño",
            "6": "EstudioSueño",
            "7": "EstudioSueño",
            "8": "EstudioSueño",
            "10": "EstudioSueño",
            "14": "Neurologia",
            "16": "Neurologia",
            "20": "Neurologia",
            "21": "AltaComplejidad",
            "22": "AltaComplejidad",
            "23": "AltaComplejidad",
            "24": "AltaComplejidad",
            "5": "Fisiatria",
            "9": "Fisiatria",
            "11": "Fisiatria",
            "17": "Fisiatria",
            "18": "Fisiatria",
            "19": "Fisiatria"
        }

        for fecha, citas in citas_por_rango.items():
            for cita in citas:
                categoria = servicio_categoria.get(cita['Servicio'], 'Otros')
                consolidated_data[categoria] += cita['ValorTotal']

        return consolidated_data
    



class CodigoSoatList(APIView):
    def get(self, request, tarifa_number, format=None):
        with connections['zeussalud'].cursor() as cursor:
            # ZeusSalud: codigossoat.TarifaN → sis_proc.manual_N
            # tarifa_number (1-40) corresponde al código del manual en sis_manual
            manual_col = f'manual_{tarifa_number}'
            query = f'''
                SELECT sp.cups, sp.nombreve, sp.[{manual_col}]
                FROM sis_proc sp
                WHERE sp.[{manual_col}] IS NOT NULL AND sp.[{manual_col}] <> 0
                  AND sp.cups IS NOT NULL AND sp.cups != %s
            '''
            cursor.execute(query, [''])
            rows = cursor.fetchall()
            data = [{'CodigoCUPS': row[0], 'DescripcionCUPS': row[1], 'Tarifa': row[2]} for row in rows]

        return Response(data, status=status.HTTP_200_OK)
    




class ContratoTarifaListView(APIView):
    def get(self, request, format=None):
        codigos = [
            { "id": 1, "label": "POLICIA ELECTRODIAGNOSTIC" },
            { "id": 2, "label": "EQUIVIDA" },
            { "id": 3, "label": "SANITAS2022" },
            { "id": 4, "label": "IPS SOLIMED" },
            { "id": 5, "label": "SANITAS 2024" },
            { "id": 6, "label": "POL09_FISATRIA" },
            { "id": 7, "label": "POL10_ELECTRO" },
            { "id": 8, "label": "POLIZAS SEGUROS BOLIVAR" },
            { "id": 9, "label": "SALUD TOTAL" },
            { "id": 10, "label": "INV META PARTICULAR" },
            { "id": 11, "label": "UNAAC" },
            { "id": 12, "label": "MEDICINA LABORAL" },
            { "id": 13, "label": "INV META COMPARTA" },
            { "id": 14, "label": "NV META SEGUROS DEL ESTA" },
            { "id": 15, "label": "AIR LIQUIDE COLOMBIA" },
            { "id": 16, "label": "ONCOLIFE IPS" },
            { "id": 17, "label": "POLICIA FISIATRIA 2" },
            { "id": 18, "label": "INV META SEGUROS BOLIVAR" },
            { "id": 19, "label": "INV META COLSANITAS" },
            { "id": 20, "label": "CENTRO DE CUIDADO CLINICO" },
            { "id": 21, "label": "COLMEDICA" },
            { "id": 22, "label": "NUEVA CLINICA BARZAL" },
            { "id": 23, "label": "CONSULTORIO IPS" },
            { "id": 24, "label": "POLIZA EVO SURA" },
            { "id": 25, "label": "POLIZA GENERAL SURA" },
            { "id": 26, "label": "MEDISANITAS" },
            { "id": 27, "label": "POLICIA TUTELA" },
            { "id": 28, "label": "CLINICA META" },
            { "id": 29, "label": "CHM MEDIMAS IMG DX" },
            { "id": 30, "label": "POL ELECTRO 2024" },
            { "id": 31, "label": "MEDIMAS" },
            { "id": 32, "label": "CAJACOPI" },
            { "id": 33, "label": "FOMAG" },
            { "id": 34, "label": "COMPENSAR" },
            { "id": 35, "label": "SANITAS" },
            { "id": 36, "label": "PARTICULAR" },
            { "id": 37, "label": "MULTISALUD SAS" },
            { "id": 38, "label": "CAPITAL SALUD" },
            { "id": 39, "label": "NUEVA CLINICA SOAT" },
            { "id": 40, "label": "COLSANITAS" },
        ]
        return Response(codigos, status=status.HTTP_200_OK)
    

####################### MRC ######################class CitasPxApiView(APIView):

class CitasPxApiView(APIView):
    

    def filter_duplicate_citas(self, citas):
        unique_citas = []
        duplicate_citas = []
        seen = set()
        for cita in citas:
            key = (cita['IdCita'], cita['CUPS'])
            if key not in seen:
                seen.add(key)
                unique_citas.append(cita)
            else:
                duplicate_citas.append(cita)
        return unique_citas, duplicate_citas

    def adjust_prices(self, citas):
        valor_unitario_base = 53560
        divisor_base = 19335160
        for i, cita in enumerate(citas):
            posicion = i + 1
            if posicion >= 325:
                cita['VrUnitario'] = round(divisor_base / posicion)
            else:
                cita['VrUnitario'] = valor_unitario_base
            cita['ValorTotal'] = round(cita['VrUnitario'] * cita['Cantidad'])
        return citas

    def get_citas_por_dia(self, fecha_inicio, fecha_fin):
        fecha_inicio = datetime.combine(fecha_inicio, datetime.min.time())
        fecha_fin = datetime.combine(fecha_fin, datetime.max.time())
        with connections['zeussalud'].cursor() as cursor:
            # ZeusSalud: cita.Entidad → c.empresa, pxcita.CUPS → sp.cups
            query = '''
                SELECT DISTINCT c.id, 0 AS VrUnitario, cp.Cantidad, sp.cups,
                       sp.codigo AS RegistroNo, cp.Servicio, sp.nombreve AS DescripcionCUPS,
                       c.empresa AS Entidad, c.fecha AS FeCita
                FROM citas c
                JOIN citas_procedimientos cp ON c.id = cp.id_cita
                LEFT JOIN sis_proc sp ON cp.id_procedimiento = sp.codigo
                WHERE c.fecha BETWEEN %s AND %s AND c.estado != 'CA'
                AND c.empresa = 'SAN02' AND sp.cups IN ('890274', '890374')
            '''
            cursor.execute(query, [fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

            citas_data = []
            for row in rows:
                cita = {
                    'IdCita': row[0],
                    'VrUnitario': row[1],
                    'Cantidad': row[2],
                    'CUPS': row[3],
                    'RegistroNo': row[4],
                    'Servicio': str(row[5]) if row[5] is not None else None,
                    'DescripcionCUPS': row[6],
                    'Entidad': row[7],
                    'FeCita': row[8],
                    'ValorTotal': row[1] * row[2]
                }
                citas_data.append(cita)

        print(f"Total citas fetched: {len(citas_data)}")

        citas_data, duplicate_citas = self.filter_duplicate_citas(citas_data)

     
        print(f"Total citas after filtering duplicates: {len(citas_data)}")
        print(f"Duplicated citas: {duplicate_citas}")

      
        citas_ajustadas = self.adjust_prices(citas_data)

       
        citas_por_dia = OrderedDict()
        for cita in citas_ajustadas:
            fecha_cita = cita['FeCita'].strftime('%Y-%m-%d') 
            if fecha_cita not in citas_por_dia:
                citas_por_dia[fecha_cita] = []
            citas_por_dia[fecha_cita].append(cita)

      
        citas_por_dia = OrderedDict(sorted(citas_por_dia.items()))

        
        total_citas = sum(cita['Cantidad'] for cita in citas_ajustadas)

        print(f"Total citas adjusted and counted: {total_citas}")

        return citas_por_dia, total_citas

    def get(self, request, *args, **kwargs):
        fecha_inicio_str = request.query_params.get('fecha_inicio')
        fecha_fin_str = request.query_params.get('fecha_fin')
        
        if not fecha_inicio_str or not fecha_fin_str:
            return Response(
                {"error": "Se requieren los parámetros 'fecha_inicio' y 'fecha_fin'."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {"error": "Las fechas deben tener el formato 'YYYY-MM-DD'."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        citas_por_dia, total_citas = self.get_citas_por_dia(fecha_inicio, fecha_fin)
        
        return Response(
            {"citas_por_dia": citas_por_dia, "total_citas": total_citas},
            status=status.HTTP_200_OK
        )
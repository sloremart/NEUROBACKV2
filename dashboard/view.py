from datetime import datetime
from django.db import connections
from rest_framework.views import APIView
from rest_framework.response import Response
from collections import defaultdict
import time
from datetime import date, datetime,  timedelta
import calendar
from rest_framework import status
from collections import defaultdict
from django.http import JsonResponse


class DashboardFacturacionEntidadView(APIView):
    def get(self, request):
        fecha = request.GET.get('fecha')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')

        if not fecha and not (fecha_inicio and fecha_fin):
            return Response({"error": "Debe enviar ?fecha o ?fecha_inicio y ?fecha_fin"}, status=400)

        params = [fecha] if fecha else [fecha_inicio, fecha_fin]
        filtro = "= %s" if fecha else "BETWEEN %s AND %s"

        start_total = time.time()

        # Paso 1: Obtener admisiones y entidades
        with connections['zeussalud'].cursor() as cursor:
            # ZeusSalud: admisiones+facturas → sis_maes, entidades → sis_empre
            # contabilidadndx.tabladenits no disponible → se usa nombre de sis_empre directamente
            cursor.execute(f'''
                SELECT sm.autoid, sm.con_estudio, sm.EPSPaciente, se.nombre AS NombreEntidad
                FROM sis_maes sm
                LEFT JOIN sis_empre se ON sm.EPSPaciente = se.codigo
                WHERE CONVERT(date, sm.fecha_ing) {filtro}
                  AND sm.Prefijo != %s
                  AND sm.EPSPaciente != %s
                  AND sm.contabilizado = 1
            ''', params + ['MGL', 'SAN02'])
            admisiones_info = cursor.fetchall()

        if not admisiones_info:
            return Response({"data": [], "total_facturado": 0})

        # autoid se usa para joins internos (sis_deta.fuente_tips); con_estudio es el consecutivo visible
        autoid_to_consecutivo = {row[0]: row[1] for row in admisiones_info}
        admision_to_entidad = {row[0]: row[3] for row in admisiones_info}
        admision_to_factura = {row[0]: None for row in admisiones_info}

        admisiones_ids = list(admision_to_entidad.keys())

        # Paso adicional: Obtener número de factura por admisión (sis_maes.nro_factura)
        with connections['zeussalud'].cursor() as cursor:
            placeholders = ','.join(['%s'] * len(admision_to_factura))
            cursor.execute(f'''
                SELECT autoid, nro_factura
                FROM sis_maes
                WHERE autoid IN ({placeholders})
            ''', list(admision_to_factura.keys()))
            for autoid, factura in cursor.fetchall():
                admision_to_factura[autoid] = factura

        # Paso 2: Obtener detallefactura
        detalles = []
        lotes = [admisiones_ids[i:i + 1000] for i in range(0, len(admisiones_ids), 1000)]
        with connections['zeussalud'].cursor() as cursor:
            for lote in lotes:
                placeholders = ','.join(['%s'] * len(lote))
                # ZeusSalud: detallefactura → sis_deta (fuente_tips = autoid de sis_maes)
                cursor.execute(f'''
                    SELECT sd.fuente_tips, sd.cups, sd.cantidad, sd.vlr_servicio,
                           NULL, NULL
                    FROM sis_deta sd
                    WHERE sd.fuente_tips IN ({placeholders})
                ''', lote)
                detalles.extend(cursor.fetchall())

        # Paso 3: Mapear CUPS a nombre de servicio (ZeusSalud: sis_proc)
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute('''
                SELECT sp.cups, sp.nombreve
                FROM sis_proc sp
                WHERE sp.cups IS NOT NULL AND sp.cups != ''
            ''')
            cups_map = dict(cursor.fetchall())

        # Paso 4: Agrupar por admisión
        detalles_por_admision = defaultdict(list)
        for admision, cups, cantidad, valor_unitario, vr_por_cuota, vr_por_copago in detalles:
            detalles_por_admision[admision].append(
                (cups, cantidad, valor_unitario, vr_por_cuota, vr_por_copago)
            )

        entidades_data = defaultdict(lambda: defaultdict(float))
        cups_desconocidos_info = []

        for admision, registros in detalles_por_admision.items():
            nombre_entidad = admision_to_entidad.get(admision, "Entidad desconocida")
            factura = admision_to_factura.get(admision)
            cups_encontrados = 0
            cups_desconocidos = []

            for cups, *_ in registros:
                if cups and str(cups) in cups_map:
                    cups_encontrados += 1
                elif cups:
                    cups_desconocidos.append(str(cups))

            # Registrar admisión si tiene CUPS desconocidos
            if cups_desconocidos:
                cups_desconocidos_info.append({
                    "entidad": nombre_entidad,
                    "admision": autoid_to_consecutivo.get(admision, admision),
                    "factura": factura,
                    "cups": cups_desconocidos
                })

            # Para agrupación por servicios (toma el primero conocido o "desconocido")
            nombre_servicio = "Servicio desconocido"
            for cups, *_ in registros:
                if cups and str(cups) in cups_map:
                    nombre_servicio = cups_map[str(cups)]
                    break

            for _, cantidad, valor_unitario, vr_por_cuota, vr_por_copago in registros:
                total = (
                    float(cantidad) * float(valor_unitario)
                    + float(vr_por_copago or 0)
                    + float(vr_por_cuota or 0)
                )
                entidades_data[nombre_entidad][nombre_servicio] += total

        # Armar respuesta agrupada
        response_data = [
            {
                "entidad": entidad,
                "servicios": [
                    {"nombre": nombre_servicio, "total": round(total, 2)}
                    for nombre_servicio, total in servicios.items()
                ]
            }
            for entidad, servicios in entidades_data.items()
        ]

        total_facturado = sum(
            total
            for servicios in entidades_data.values()
            for total in servicios.values()
        )

        return Response({
            "data": response_data,
            "total_facturado": round(total_facturado, 2),
            "cantidad_facturas": len(admision_to_entidad),
            "cups_desconocidos_info": cups_desconocidos_info
        })

class DashboardAgendadasView(APIView):
    def get(self, request):
        hoy = datetime.now().date()

        # Paso 1: Obtener citas del día con nombre de entidad (ZeusSalud: citas + sis_empre)
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute('''
                SELECT c.id, c.empresa, se.nombre AS NombreEntidad
                FROM citas c
                LEFT JOIN sis_empre se ON c.empresa = se.codigo
                WHERE CONVERT(date, c.fecha) = %s
                  AND c.estado != 'CA'
            ''', [hoy])
            citas_info = cursor.fetchall()

        if not citas_info:
            return Response({"servicios": [], "entidades": [], "usuarios": []})

        idcita_to_entidad = {}
        for idcita, empresa, nombre_entidad in citas_info:
            idcita_to_entidad[idcita] = nombre_entidad or empresa or 'Entidad desconocida'

        idcitas = list(idcita_to_entidad.keys())

        # Paso 2: Buscar procedimientos en citas_procedimientos con nombre de servicio desde sis_proc
        registros_validos = []
        if idcitas:
            lotes = [idcitas[i:i + 1000] for i in range(0, len(idcitas), 1000)]
            with connections['zeussalud'].cursor() as cursor:
                for lote in lotes:
                    formato = ','.join(['%s'] * len(lote))
                    cursor.execute(f'''
                        SELECT cp.id_cita,
                               COALESCE(sp.nombreve, 'Servicio desconocido') AS NombreServicio,
                               0 AS VrUnitario,
                               cp.Cantidad
                        FROM citas_procedimientos cp
                        LEFT JOIN sis_proc sp ON cp.id_procedimiento = sp.codigo
                        WHERE cp.id_cita IN ({formato})
                    ''', lote)
                    registros_validos.extend(cursor.fetchall())

        if not registros_validos:
            return Response({"servicios": [], "entidades": [], "usuarios": []})

        # Paso 3: Agrupar por servicio y entidad
        servicio_totales = defaultdict(float)
        entidad_citas = defaultdict(set)

        for idcita, nombre_servicio, vr_unitario, cantidad in registros_validos:
            total = float(vr_unitario) * float(cantidad)
            entidad = idcita_to_entidad.get(idcita, 'Entidad desconocida')

            servicio_totales[nombre_servicio] += total
            entidad_citas[entidad].add(idcita)

        # Construir JSON de salida
        servicios_data = [
            {"nombre": nombre, "total": round(valor, 2)}
            for nombre, valor in servicio_totales.items()
        ]

        entidades_data = [
            {"nombre": nombre, "citas": len(citas)}
            for nombre, citas in entidad_citas.items()
        ]

        return Response({
            "servicios": servicios_data,
            "entidades": entidades_data,
            "usuarios": []
        })



## DASHBOARD DE FACTURACION - ESTADO DE CARTERA




def chunked_list(data, chunk_size=500):
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]
""" 
class DocumentosPorNit(APIView):
    def get(self, request):
        try:
            nit = request.query_params.get('nit')
            periodo = request.query_params.get('periodo')
            sin_limite = request.query_params.get('sin_limite', '0') == '1'

            if not nit:
                return JsonResponse({'error': 'El parámetro NIT es requerido'}, status=400)

            try:
                nit = int(nit)
            except ValueError:
                return JsonResponse({'error': 'El NIT debe ser numérico'}, status=400)

            query_params = [nit]

            if periodo:
                query = '''
                    SELECT Factura, Prefijo, Periodo, AdmisionNo, Debito, Credito
                    FROM contabilidadndx.tmpauxiliar
                    WHERE NIT = %s AND Prefijo != 'MGL' AND TipoDoc = 2 AND Periodo = %s
                '''
                query_params.append(periodo)
            else:
                query = '''
                    SELECT Factura, Prefijo, Periodo, AdmisionNo, Debito, Credito
                    FROM contabilidadndx.tmpauxiliar
                    WHERE NIT = %s AND Prefijo != 'MGL' AND TipoDoc = 2 AND Periodo >= '202201'
                '''
                if not sin_limite:
                    query += ' LIMIT 1000'

            with connections['contabilidadndx'].cursor() as cursor:
                cursor.execute(query, query_params)
                facturas_rows = cursor.fetchall()

            admisiones_ids = [row[3] for row in facturas_rows if row[3] and row[3] != 0]
            admisiones_data = {}
            fechas_envio_data = {}

            if admisiones_ids:
                with connections['zeussalud'].cursor() as cursor:
                    for chunk in chunked_list(admisiones_ids, 500):
                        format_strings = ','.join(['%s'] * len(chunk))

                        cursor.execute(f'''
                            SELECT Consecutivo, COALESCE(IDPaciente, ''), COALESCE(NombreResponsable, '')
                            FROM zeussalud.admisiones
                            WHERE Consecutivo IN ({format_strings});
                        ''', tuple(chunk))
                        for row in cursor.fetchall():
                            admisiones_data[row[0]] = (row[1], row[2])

                        cursor.execute(f'''
                            SELECT AdmisionNo, FechaEnvio
                            FROM zeussalud.facturas
                            WHERE AdmisionNo IN ({format_strings});
                        ''', tuple(chunk))
                        for row in cursor.fetchall():
                            fechas_envio_data[row[0]] = row[1]

            # 🧠 Agrupamos por factura/prefijo para buscar notas de cartera
            factura_claves = {(row[0], row[1]) for row in facturas_rows}

            # Mapeo: (Factura, Prefijo) → NotaCartera (Credito del TipoDoc 32)
            notas_cartera = {}
            with connections['contabilidadndx'].cursor() as cursor:
                if factura_claves:
                    format_strings = ','.join(['(%s, %s)'] * len(factura_claves))
                    flat_params = []
                    for factura, prefijo in factura_claves:
                        flat_params.extend([factura, prefijo])

                    cursor.execute(f'''
                        SELECT Factura, Prefijo, Credito
                        FROM contabilidadndx.tmpauxiliar
                        WHERE TipoDoc = 32
                          AND (Factura, Prefijo) IN ({format_strings});
                    ''', tuple(flat_params))

                    for factura, prefijo, credito in cursor.fetchall():
                        notas_cartera[(factura, prefijo)] = float(credito or 0)

            resultado = []
            for row in facturas_rows:
                factura, prefijo, periodo, admision_no, debito, credito = row
                id_paciente, nombre_responsable = admisiones_data.get(admision_no, ('', ''))
                fecha_envio = fechas_envio_data.get(admision_no)
                notacartera = notas_cartera.get((factura, prefijo), 0)

                resultado.append({
                    'Factura': factura,
                    'Prefijo': prefijo,
                    'Periodo': periodo,
                    'AdmisionNo': admision_no,
                    'IDPaciente': id_paciente,
                    'NombreResponsable': nombre_responsable,
                    'FechaEnvio': fecha_envio.strftime('%Y-%m-%d') if fecha_envio else None,
                    'FueRadicada': fecha_envio is not None,
                    'Debito': float(debito or 0),
                    'Credito': float(credito or 0),
                    'NotaCartera': notacartera
                })

            return JsonResponse(resultado, safe=False)

        except Exception as e:
            print(f"Error en la API: {e}")
            return JsonResponse({'error': str(e)}, status=500)
 """
from datetime import date
from django.http import JsonResponse
from rest_framework.views import APIView
from django.db import connections
import calendar

class CarteraConsolidadaPorEntidadAPIView(APIView):

    def get(self, request):
        codigos = request.query_params.getlist('codigo_entidad')
        anio = request.query_params.get('anio')
        mes = request.query_params.get('mes')

        try:
            with connections['zeussalud'].cursor() as cursor:
                params = []

                # Filtrado por código entidad
                where_clause = ''
                if codigos:
                    placeholders = ','.join(['%s'] * len(codigos))
                    where_clause = f'WHERE e.IDEntidad IN ({placeholders})'
                    params.extend(codigos)

                # Rango de fechas desde 2022 hasta anio/mes recibido
                if anio and anio.isdigit():
                    anio = int(anio)
                    mes = int(mes) if mes and mes.isdigit() else datetime.now().month
                    ultimo_dia = calendar.monthrange(anio, mes)[1]
                    fecha_inicio = date(2022, 1, 1)
                    fecha_fin = date(anio, mes, ultimo_dia)
                else:
                    fecha_inicio = date(2022, 1, 1)
                    fecha_fin = date.today()

                # Fechas para todas las subconsultas
                params.extend([fecha_inicio, fecha_fin])  # adm
                params.extend([fecha_inicio, fecha_fin])  # fact_radicadas
                params.extend([fecha_inicio, fecha_fin])  # fact_no_radicadas

                query = f'''
                    WITH adm AS (
                        SELECT 
                            CodigoEntidad,
                            COUNT(DISTINCT Consecutivo) AS total_admisiones
                        FROM zeussalud.admisiones
                        WHERE FechaCreado BETWEEN %s AND %s
                        GROUP BY CodigoEntidad
                    ),
                    fact_radicadas AS (
                        SELECT 
                            a.CodigoEntidad,
                            COUNT(DISTINCT f.AdmisionNo) AS facturas_radicadas,
                            SUM(df.Cantidad * df.ValorUnitario) AS total_facturado_radicado
                        FROM zeussalud.admisiones a
                        JOIN zeussalud.facturas f ON f.AdmisionNo = a.Consecutivo
                            AND f.FechaEnvio IS NOT NULL AND f.Prefijo <> 'MGL'
                        JOIN zeussalud.detallefactura df ON df.AdmisionNo = f.AdmisionNo
                        WHERE a.FechaCreado BETWEEN %s AND %s
                        GROUP BY a.CodigoEntidad
                    ),
                    fact_no_radicadas AS (
                        SELECT 
                            a.CodigoEntidad,
                            SUM(df.Cantidad * df.ValorUnitario) AS total_facturado_no_radicado
                        FROM zeussalud.admisiones a
                        JOIN zeussalud.facturas f ON f.AdmisionNo = a.Consecutivo
                            AND f.FechaEnvio IS NULL AND f.Prefijo <> 'MGL'
                        JOIN zeussalud.detallefactura df ON df.AdmisionNo = f.AdmisionNo
                        WHERE a.FechaCreado BETWEEN %s AND %s
                        GROUP BY a.CodigoEntidad
                    )
                    SELECT 
                        e.IDEntidad,
                        e.NIT,
                        e.NombreEntidad,
                        COALESCE(adm.total_admisiones, 0),
                        COALESCE(fr.facturas_radicadas, 0),
                        COALESCE(fr.total_facturado_radicado, 0),
                        COALESCE(fnr.total_facturado_no_radicado, 0)
                    FROM zeussalud.entidades e
                    LEFT JOIN adm ON adm.CodigoEntidad = e.IDEntidad
                    LEFT JOIN fact_radicadas fr ON fr.CodigoEntidad = e.IDEntidad
                    LEFT JOIN fact_no_radicadas fnr ON fnr.CodigoEntidad = e.IDEntidad
                    {where_clause}
                    ORDER BY e.NombreEntidad
                '''

                cursor.execute(query, params)
                resultados = cursor.fetchall()

                data = []
                for row in resultados:
                    record = {
                        'codigo_entidad': row[0],
                        'nit': row[1],
                        'entidad': row[2],
                        'total_admisiones': row[3],
                        'facturas_radicadas': row[4],
                        'total_facturado_radicado': float(row[5]),
                        'total_facturado_no_radicado': float(row[6]),
                    }

                    if any([
                        record['total_admisiones'] > 0,
                        record['facturas_radicadas'] > 0,
                        record['total_facturado_radicado'] > 0,
                        record['total_facturado_no_radicado'] > 0,
                    ]):
                        data.append(record)

                return JsonResponse(data, safe=False)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)





NITS_DIAS_CREDITO = {
    "900470909": {"nombre": "NUEVA CLINICA EL BARZAL S.A.S", "dias": 60},
    "860066942": {"nombre": "CAJA DE COMPENSACION FAMILIAR COMPENSAR", "dias": 60},
    "900364721": {"nombre": "UNIDAD MEDICA ONCOLOGICA ONCOLIFE IPS SAS", "dias": 30},
    "860011153": {"nombre": "POSITIVA COMPAÑIA DE SEGUROS S. A.", "dias": 30},
    "900298372": {"nombre": "CAPITAL SALUD", "dias": 30}, 
    "900838988": {"nombre": "AIR LIQUIDE COLOMBIA S.A.S.", "dias": 30},  
    "900213617": {"nombre": "CORPORACION CLINICA PRIMAVERA", "dias": 30},
    "860002184": {"nombre": "AXA COLPATRIA SEGUROS S.A.", "dias": 30},
    "860002503": {"nombre": "COMPAÑÍA DE SEGUROS BOLIVAR S A", "dias": 30},
    "860037013": {"nombre": "COMPAÑÍA MUNDIAL DE SEGUROS S.A", "dias": 30},
    "900434629": {"nombre": "LA EQUIDAD SEGUROS GENERALES", "dias": 30},
    "860002400": {"nombre": "LA PREVISORA SA COMPAÑÍA DE SEGUROS", "dias": 30},
    "860002180": {"nombre": "SEGUROS COMERCIALES BOLIVAR SA", "dias": 30},
    "890903790": {"nombre": "SEGUROS DE VIDA SURAMERICANA S.A.", "dias": 30},
    "860009578": {"nombre": "SEGUROS DEL ESTADO S.A.", "dias": 30},
    "890903407": {"nombre": "SEGUROS GENERALES SURAMERICANA S.A.", "dias": 30},
    "830053105": {"nombre": "FIDEICOMISOS PATRIMONIOS AUTONOMOS FIDUCIARIA LA PREVISORA", "dias": 75},
    "900407224": {"nombre": "LABORATORIO CLINICO PROTEGER IPS PROFESIONALES EN SALUD OCUPACIONAL Y CALIDAD S.A.S", "dias": 45},
    "901324466": {"nombre": "UNIDAD AMBULATORIA DE ALTA COMPLEJIDAD S.A.S", "dias": 75},  
    "860078828": {"nombre": "COMPAÑÍA DE MEDICINA PREPAGADA COLSANITAS S.A.", "dias": 60},
    "860524654": {"nombre": "ASEGURADORA SOLIDARIA DE COLOMBIA ENTIDAD COOPERATIVA", "dias": 60},
    "860524654": {"nombre": "ASEGURADORA SOLIDARIA DE COLOMBIA ENTIDAD COOPERATIVA", "dias": 60},
    "800153424": {"nombre": "MEDISANITAS S.A. COMPAÑÍA DE MEDICINA PREPAGADA", "dias": 90},
    "900407224": {"nombre": "REGIONAL DE ASEGURAMIENTO EN SALUD NO.7 POLICIA NACIONAL", "dias": 90},
    "860009578": {"nombre": "SEGUROS DEL ESTADO S.A.", "dias": 90},
    "900502267": {"nombre": "CENTROS DE CONSULTAS SAS", "dias": 90},
    "900491982": {"nombre": "EQUIPO INTERDISCIPLINARIO PARA EL MEJORAMIENTO DE LA CALIDAD", "dias": 30},
    "892000401": {"nombre": "INVERSIONES CLINICA DEL META S.A.", "dias": 30},
    "900454855": {"nombre": "IPS CONSULTORIO MEDICO SALUD OCUPACIONAL S.A.S", "dias": 30},
    "900992393": {"nombre": "IPS SOLIMED JD SAS", "dias": 30},
    "830511298": {"nombre": "MULTISALUD SAS", "dias": 60},
    "901543211": {"nombre": "CAJA COPIEPS", "dias": 60},
    
}

class EstadoCarteraTodasEntidades(APIView):
    def get(self, request):
        codigos = request.query_params.getlist('codigo_entidad')
        anio = request.query_params.get('anio')
        mes = request.query_params.get('mes')
        
        # Si no se especifica año/mes, usar fecha actual
        if anio and mes:
            try:
                fecha_limite = date(int(anio), int(mes), 1)
            except ValueError:
                return JsonResponse({'error': 'Formato de año/mes inválido'}, status=400)
        else:
            fecha_limite = datetime.now().date()

        resultado = defaultdict(lambda: {
            "nombre": "",
            "dias_credito": 0,
            "1-30 días": 0,
            "31-60 días": 0,
            "61-90 días": 0,
            "91-120 días": 0,
            "121-150 días": 0,
            "151-180 días": 0,
            "181+ días": 0,
            "Glosas": 0,
        })

        try:
            # 1. Obtener pagos válidos y glosas VALIDADAS desde tmpauxiliar
            pagos_por_factura = {}    # Tipos 32 y 24
            glosas_por_factura = {}   # Tipo 23 VALIDADO como glosa real
            anulaciones_por_factura = {}  # Tipo 23 que son anulaciones

            # Primero obtener todos los documentos tipo 23 para validarlos
            documentos_tipo23 = {}
            with connections['contabilidadndx'].cursor() as cursor:
                cursor.execute('''
                    SELECT DISTINCT Documento
                    FROM contabilidadndx.tmpauxiliar
                    WHERE TipoDoc = 23
                ''')
                documentos_tipo23 = {row[0] for row in cursor.fetchall()}

            # Validar cada documento tipo 23 consultando la tabla documentos
            documentos_validados = {}
            if documentos_tipo23:
                placeholders = ','.join(['%s'] * len(documentos_tipo23))
                with connections['contabilidadndx'].cursor() as cursor:
                    cursor.execute(f'''
                        SELECT Documento, ConceptoContable
                        FROM documentos
                        WHERE Documento IN ({placeholders})
                    ''', list(documentos_tipo23))
                    
                    for documento, concepto in cursor.fetchall():
                        concepto_lower = (concepto or '').lower()
                        # Validar si es anulación o glosa real
                        if any(palabra in concepto_lower for palabra in ['anulación', 'anulacion', 'reversión', 'reversion', 'reverso']):
                            documentos_validados[documento] = 'anulacion'
                        elif any(palabra in concepto_lower for palabra in ['glosa', 'objecion', 'objeción']):
                            documentos_validados[documento] = 'glosa'
                        else:
                            # Por defecto, si no se puede determinar, se considera glosa
                            documentos_validados[documento] = 'glosa'

            # Ahora procesar los movimientos con la validación
            with connections['contabilidadndx'].cursor() as cursor:
                cursor.execute('''
                    SELECT Factura, Prefijo, TipoDoc, Documento, SUM(Credito - Debito)
                    FROM contabilidadndx.tmpauxiliar
                    WHERE TipoDoc IN (23, 24, 32)
                    GROUP BY Factura, Prefijo, TipoDoc, Documento
                ''')
                
                for factura, prefijo, tipodoc, documento, saldo in cursor.fetchall():
                    clave = (str(factura), prefijo)
                    saldo = float(saldo or 0)

                    if tipodoc in (32, 24):
                        pagos_por_factura[clave] = pagos_por_factura.get(clave, 0) + saldo
                    elif tipodoc == 23:
                        # Validar si es glosa real o anulación
                        tipo_documento = documentos_validados.get(documento, 'glosa')
                        if tipo_documento == 'glosa':
                            glosas_por_factura[clave] = glosas_por_factura.get(clave, 0) + saldo
                        elif tipo_documento == 'anulacion':
                            # Las anulaciones NO se cuentan como glosas, se tratan como ajuste de factura
                            anulaciones_por_factura[clave] = anulaciones_por_factura.get(clave, 0) + saldo

            # 2. Procesar facturas reales desde zeussalud
            with connections['zeussalud'].cursor() as cursor:
                cursor.execute('''
                    SELECT e.NIT, e.NombreEntidad, f.FechaEnvio,
                           df.Cantidad, df.ValorUnitario, df.VrPorCuota, df.VrPorCopago,
                           f.Prefijo, f.FacturaNo
                    FROM entidades e
                    JOIN admisiones a ON a.CodigoEntidad = e.IDEntidad
                    JOIN facturas f ON f.AdmisionNo = a.Consecutivo
                    JOIN detallefactura df ON df.AdmisionNo = f.AdmisionNo
                    WHERE f.FechaEnvio IS NOT NULL AND f.Prefijo <> 'MGL'
                ''')

                for (
                    nit, nombre, fecha_envio,
                    cantidad, valor_unitario, vr_cuota, vr_copago,
                    prefijo, documento
                ) in cursor.fetchall():

                    if not nit or not fecha_envio or not documento or not prefijo:
                        continue

                    nit_clean = nit.replace(".", "").replace("-", "").strip()
                    if nit_clean not in NITS_DIAS_CREDITO:
                        continue

                    dias_credito = NITS_DIAS_CREDITO[nit_clean]["dias"]
                    nombre_nit = NITS_DIAS_CREDITO[nit_clean]["nombre"]
                    clave_factura = (str(documento), prefijo)

                    # Calcular valor neto a cobrar (sin copago/cuota moderadora)
                    valor_bruto = (cantidad or 0) * (valor_unitario or 0)
                    valor_total = valor_bruto - (vr_cuota or 0) - (vr_copago or 0)

                    # Saldos aplicados por la entidad
                    total_pago = pagos_por_factura.get(clave_factura, 0)
                    total_glosa = glosas_por_factura.get(clave_factura, 0)
                    total_anulacion = anulaciones_por_factura.get(clave_factura, 0)

                    # Mostrar glosa en resultado, pero no como pago
                    resultado[nit_clean]["Glosas"] += total_glosa
                    resultado[nit_clean]["nombre"] = nombre_nit
                    resultado[nit_clean]["dias_credito"] = dias_credito

                    saldo_pendiente = valor_total - total_pago - total_anulacion

                    # Si el saldo pendiente es cero o menor, está saldada
                    if saldo_pendiente <= 0:
                        continue

                    # NUEVO SISTEMA: Clasificar según días transcurridos desde FechaEnvio
                    dias_transcurridos = (fecha_limite - fecha_envio).days

                    if dias_transcurridos <= 30:
                        estado = "1-30 días"
                    elif dias_transcurridos <= 60:
                        estado = "31-60 días"
                    elif dias_transcurridos <= 90:
                        estado = "61-90 días"
                    elif dias_transcurridos <= 120:
                        estado = "91-120 días"
                    elif dias_transcurridos <= 150:
                        estado = "121-150 días"
                    elif dias_transcurridos <= 180:
                        estado = "151-180 días"
                    else:
                        estado = "181+ días"

                    resultado[nit_clean][estado] += saldo_pendiente

            # 3. NUEVO: Calcular recaudo pendiente por aplicar por NIT
            recaudo_pendiente_por_nit = {}
            
            with connections['contabilidadndx'].cursor() as cursor:
                # Obtener DINERO QUE ENTRA a la cuenta 1306 (comprobantes de ingreso tipodoc 3)
                cursor.execute('''
                    SELECT NIT, SUM(Credito - Debito) as total_entrada_1306
                    FROM contabilidadndx.tmpauxiliar
                    WHERE TipoDoc = 3 
                    AND Prefijo != 'MGL'
                    AND Cuenta LIKE '1306%'
                    GROUP BY NIT
                ''')
                
                entrada_cuenta_1306_por_nit = {}
                for nit, total_entrada in cursor.fetchall():
                    if nit:
                        nit_clean = nit.replace(".", "").replace("-", "").strip()
                        entrada_cuenta_1306_por_nit[nit_clean] = float(total_entrada or 0)

                # Obtener DINERO QUE SALE de la cuenta 1306 (notas de cartera tipodoc 32)
                cursor.execute('''
                    SELECT NIT, SUM(Credito - Debito) as total_salida_1306
                    FROM contabilidadndx.tmpauxiliar
                    WHERE TipoDoc = 32 
                    AND Prefijo != 'MGL'
                    AND Cuenta LIKE '1306%'
                    GROUP BY NIT
                ''')
                
                salida_cuenta_1306_por_nit = {}
                for nit, total_salida in cursor.fetchall():
                    if nit:
                        nit_clean = nit.replace(".", "").replace("-", "").strip()
                        salida_cuenta_1306_por_nit[nit_clean] = float(total_salida or 0)

                # Calcular recaudo pendiente por NIT
                for nit_clean, total_entrada in entrada_cuenta_1306_por_nit.items():
                    total_salida = salida_cuenta_1306_por_nit.get(nit_clean, 0)
                    recaudo_pendiente = total_entrada - total_salida
                    
                    if recaudo_pendiente > 0:
                        recaudo_pendiente_por_nit[nit_clean] = recaudo_pendiente

            # 4. AGREGAR el campo a la respuesta existente (SIN CAMBIAR la lógica de cartera)
            for nit_clean, recaudo_pendiente in recaudo_pendiente_por_nit.items():
                if nit_clean in resultado:
                    resultado[nit_clean]["recaudo_pendiente_aplicar"] = recaudo_pendiente

            return JsonResponse(resultado, safe=False)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


class EstadoCarteraPorNit(APIView):
    def get(self, request):
        nit = request.query_params.get('nit')
        anio = request.query_params.get('anio')
        mes = request.query_params.get('mes')
        
        if not nit:
            return JsonResponse({'error': 'El parámetro NIT es requerido'}, status=400)
        
        # Limpiar NIT
        nit_clean = nit.replace(".", "").replace("-", "").strip()
        
        # Si no se especifica año/mes, usar fecha actual
        if anio and mes:
            try:
                fecha_limite = date(int(anio), int(mes), 1)
            except ValueError:
                return JsonResponse({'error': 'Formato de año/mes inválido'}, status=400)
        else:
            fecha_limite = datetime.now().date()
        
        try:
            # Obtener todos los movimientos de cartera para el NIT específico
            movimientos_cartera = {}
            
            # Primero obtener todos los documentos tipo 23 para validarlos
            documentos_tipo23 = set()
            with connections['contabilidadndx'].cursor() as cursor:
                cursor.execute('''
                    SELECT DISTINCT Documento
                    FROM contabilidadndx.tmpauxiliar
                    WHERE NIT = %s AND TipoDoc = 23
                ''', [nit])
                documentos_tipo23 = {row[0] for row in cursor.fetchall()}

            # Validar cada documento tipo 23 consultando la tabla documentos
            documentos_validados = {}
            if documentos_tipo23:
                placeholders = ','.join(['%s'] * len(documentos_tipo23))
                with connections['contabilidadndx'].cursor() as cursor:
                    cursor.execute(f'''
                        SELECT Documento, ConceptoContable
                        FROM documentos
                        WHERE Documento IN ({placeholders})
                    ''', list(documentos_tipo23))
                    
                    for documento, concepto in cursor.fetchall():
                        concepto_lower = (concepto or '').lower()
                        # Validar si es anulación o glosa real
                        if any(palabra in concepto_lower for palabra in ['anulación', 'anulacion', 'reversión', 'reversion', 'reverso']):
                            documentos_validados[documento] = 'anulacion'
                        elif any(palabra in concepto_lower for palabra in ['glosa', 'objecion', 'objeción']):
                            documentos_validados[documento] = 'glosa'
                        else:
                            # Por defecto, si no se puede determinar, se considera glosa
                            documentos_validados[documento] = 'glosa'
            
            with connections['contabilidadndx'].cursor() as cursor:
                cursor.execute('''
                    SELECT Factura, Prefijo, TipoDoc, Periodo, Debito, Credito, Cuenta, Documento
                    FROM contabilidadndx.tmpauxiliar
                    WHERE NIT = %s 
                    AND TipoDoc IN (2, 29, 32, 23, 24)
                    AND Prefijo != 'MGL'
                    ORDER BY Factura, Prefijo, Periodo, TipoDoc
                ''', [nit])
                
                for factura, prefijo, tipodoc, periodo, debito, credito, cuenta, documento in cursor.fetchall():
                    clave = (str(factura), prefijo)
                    if clave not in movimientos_cartera:
                        movimientos_cartera[clave] = {
                            'movimientos': [],
                            'valor_facturado': 0,      # Valor original de la factura
                            'valor_radicado': 0,        # Valor radicado (enviado a entidad)
                            'valor_abonado': 0,         # Valor realmente pagado
                            'glosas': 0,                # Glosas aplicadas (VALIDADAS)
                            'anulaciones': 0,           # Anulaciones (NO son glosas)
                            'saldo_pendiente': 0,       # Saldo real pendiente
                            'detalle_movimientos': []
                        }
                    
                    movimientos_cartera[clave]['movimientos'].append({
                        'tipodoc': tipodoc,
                        'periodo': periodo,
                        'debito': float(debito or 0),
                        'credito': float(credito or 0),
                        'cuenta': cuenta,
                        'documento': documento
                    })
                    
                    # Agregar detalle para mostrar en el resultado
                    descripcion = self._get_descripcion_tipodoc(tipodoc)
                    if tipodoc == 23:
                        # Validar si es glosa real o anulación
                        tipo_documento = documentos_validados.get(documento, 'glosa')
                        if tipo_documento == 'anulacion':
                            descripcion = "Anulación de Factura"
                        elif tipo_documento == 'glosa':
                            descripcion = "Glosa"
                    
                    movimientos_cartera[clave]['detalle_movimientos'].append({
                        'tipodoc': tipodoc,
                        'periodo': periodo,
                        'debito': float(debito or 0),
                        'credito': float(credito or 0),
                        'cuenta': cuenta,
                        'descripcion': descripcion,
                        'documento': documento
                    })
            
            # Procesar cada factura para calcular saldos correctamente
            facturas_procesadas = []
            
            for clave, datos in movimientos_cartera.items():
                factura, prefijo = clave
                
                # Ordenar movimientos por periodo para seguir el flujo cronológico
                movimientos_ordenados = sorted(datos['movimientos'], key=lambda x: x['periodo'])
                
                # Inicializar valores
                valor_factura = 0
                valor_radicado = 0
                valor_abonado = 0
                valor_glosas = 0
                
                # Consolidar movimientos por tipo para evitar duplicados
                movimientos_consolidados = {}
                
                for mov in movimientos_ordenados:
                    tipodoc = mov['tipodoc']
                    if tipodoc not in movimientos_consolidados:
                        movimientos_consolidados[tipodoc] = {
                            'tipodoc': tipodoc,
                            'periodo': mov['periodo'],
                            'debito': 0,
                            'credito': 0,
                            'cuenta': mov['cuenta'],
                            'documento': mov['documento'],
                            'descripcion': self._get_descripcion_tipodoc(tipodoc)
                        }
                    
                    # Acumular débitos y créditos
                    movimientos_consolidados[tipodoc]['debito'] += mov['debito']
                    movimientos_consolidados[tipodoc]['credito'] += mov['credito']
                
                # Ahora procesar los movimientos consolidados
                for tipodoc, mov in movimientos_consolidados.items():
                    if tipodoc == 2:  # Factura - Valor original
                        # Para tipodoc 2, el valor facturado es el débito
                        valor_factura = mov['debito']
                        
                    elif tipodoc == 29:  # Radicación - NO es pago, solo cambio de cuenta
                        # La radicación mueve el valor de una cuenta a otra, pero no es pago
                        # Solo registramos que fue radicada
                        valor_radicado = valor_factura
                        
                    elif tipodoc == 32:  # Abono Cartera - PAGO REAL
                        # Este SÍ es un pago real
                        abono = mov['credito'] - mov['debito']
                        if abono > 0:
                            valor_abonado += abono
                            
                    elif tipodoc == 23:  # Glosa o Anulación - VALIDAR
                        # Validar si es glosa real o anulación
                        documento = mov.get('documento', '')
                        tipo_documento = documentos_validados.get(documento, 'glosa')
                        
                        if tipo_documento == 'anulacion':
                            # Es una anulación, se trata como ajuste de factura
                            anulacion = mov['credito'] - mov['debito']
                            if anulacion > 0:
                                datos['anulaciones'] += anulacion
                                # Las anulaciones reducen el valor facturado
                                valor_factura -= anulacion
                                # Actualizar descripción para mostrar que es anulación
                                mov['descripcion'] = "Anulación de Factura"
                        else:
                            # Es una glosa real
                            glosa = mov['credito'] - mov['debito']
                            if glosa > 0:
                                valor_glosas += glosa
                            
                    elif tipodoc == 24:  # Otros Abonos
                        abono = mov['credito'] - mov['debito']
                        if abono > 0:
                            valor_abonado += abono
                
                # Calcular saldo pendiente real
                # Saldo = Facturado - Abonado - Glosas (las anulaciones ya se restaron del facturado)
                saldo_pendiente = valor_factura - valor_abonado - valor_glosas
                
                # Asegurarse de que el valor_factura nunca sea negativo
                valor_factura = max(0, valor_factura)
                
                # Solo incluir facturas que tengan movimientos o saldo pendiente
                # Cambiar la condición para incluir facturas que tengan tipodoc 2 (factura creada)
                if valor_factura > 0 or any(mov['tipodoc'] == 2 for mov in datos['movimientos']):
                    # Obtener información adicional de la factura
                    valor_factura_original = 0
                    with connections['zeussalud'].cursor() as cursor:
                        cursor.execute('''
                            SELECT f.FechaEnvio, f.AdmisionNo, a.IDPaciente, a.NombreResponsable, f.TotalFactura
                            FROM zeussalud.facturas f
                            JOIN zeussalud.admisiones a ON f.AdmisionNo = a.Consecutivo
                            WHERE f.FacturaNo = %s AND f.Prefijo = %s
                        ''', [factura, prefijo])
                        
                        factura_info = cursor.fetchone()
                        if factura_info:
                            fecha_envio = factura_info[0]
                            admision_no = factura_info[1]
                            id_paciente = factura_info[2]
                            nombre_responsable = factura_info[3]
                            valor_factura_original = float(factura_info[4]) if factura_info[4] else 0
                        else:
                            fecha_envio = None
                            admision_no = None
                            id_paciente = None
                            nombre_responsable = None
                            valor_factura_original = 0
                    
                    # Si no se pudo obtener el valor original, usar el calculado desde los movimientos contables
                    if valor_factura_original <= 0:
                        valor_factura_original = valor_factura
                    
                    # Verificar si la factura tiene glosas pendientes (sin tipodoc 32)
                    tiene_glosas_pendientes = False
                    if valor_glosas > 0:
                        # Buscar si existe tipodoc 32 (nota de cartera) para esta factura
                        with connections['contabilidadndx'].cursor() as cursor_glosas:
                            cursor_glosas.execute('''
                                SELECT COUNT(*) 
                                FROM tmpauxiliar 
                                WHERE Factura = %s AND Prefijo = %s AND TipoDoc = 32
                            ''', [factura, prefijo])
                            tiene_nota_cartera = cursor_glosas.fetchone()[0] > 0
                            tiene_glosas_pendientes = not tiene_nota_cartera
                    
                    # Crear lista de movimientos consolidados para mostrar
                    movimientos_mostrar = []
                    for tipodoc, mov_consolidado in movimientos_consolidados.items():
                        # Solo incluir movimientos con valores significativos
                        if mov_consolidado['debito'] > 0 or mov_consolidado['credito'] > 0:
                            movimientos_mostrar.append({
                                'tipodoc': tipodoc,
                                'periodo': mov_consolidado['periodo'],
                                'debito': mov_consolidado['debito'],
                                'credito': mov_consolidado['credito'],
                                'cuenta': mov_consolidado['cuenta'],
                                'descripcion': mov_consolidado['descripcion'],
                                'documento': mov_consolidado['documento']
                            })
                    
                    facturas_procesadas.append({
                        'factura': factura,
                        'prefijo': prefijo,
                        'admision_no': admision_no,
                        'id_paciente': id_paciente,
                        'nombre_responsable': nombre_responsable,
                        'fecha_envio': fecha_envio.strftime('%Y-%m-%d') if fecha_envio else None,
                        'valor_facturado': round(valor_factura_original, 2), # Use the fetched value
                        'valor_radicado': round(valor_radicado, 2),
                        'valor_abonado': round(valor_abonado, 2),
                        'glosas': round(valor_glosas, 2),
                        'anulaciones': round(datos['anulaciones'], 2),
                        'saldo_actual': round(max(0, saldo_pendiente), 2),
                        'tiene_glosas_pendientes': tiene_glosas_pendientes,
                        'movimientos': movimientos_mostrar
                    })
            
            # Ordenar por saldo pendiente (mayor a menor)
            facturas_procesadas.sort(key=lambda x: x['saldo_actual'], reverse=True)
            
            # Calcular totales
            total_facturado = sum(f['valor_facturado'] for f in facturas_procesadas)
            total_radicado = sum(f['valor_radicado'] for f in facturas_procesadas)
            total_abonado = sum(f['valor_abonado'] for f in facturas_procesadas)
            total_glosas = sum(f['glosas'] for f in facturas_procesadas)
            total_anulaciones = sum(f['anulaciones'] for f in facturas_procesadas)
            total_saldo_pendiente = sum(f['saldo_actual'] for f in facturas_procesadas)
            
            # Calcular glosas pendientes vs resueltas
            total_glosas_pendientes = sum(f['glosas'] for f in facturas_procesadas if f['tiene_glosas_pendientes'])
            total_glosas_resueltas = total_glosas - total_glosas_pendientes
            
            # Obtener nombre de la entidad
            nombre_entidad = ""
            if facturas_procesadas:
                with connections['zeussalud'].cursor() as cursor:
                    cursor.execute('''
                        SELECT e.NombreEntidad
                        FROM zeussalud.entidades e
                        WHERE e.NIT = %s
                    ''', [nit])
                    resultado = cursor.fetchone()
                    if resultado:
                        nombre_entidad = resultado[0]
                    
                    # Si no se encuentra en entidades, buscar en tabladenits
                    if not nombre_entidad:
                        with connections['contabilidadndx'].cursor() as cursor_cont:
                            cursor_cont.execute('''
                                SELECT Nombre
                                FROM contabilidadndx.tabladenits
                                WHERE IDTercero = %s
                            ''', [nit])
                            resultado_cont = cursor_cont.fetchone()
                            if resultado_cont:
                                nombre_entidad = resultado_cont[0]
            
            # Si aún no se encuentra, usar un valor por defecto
            if not nombre_entidad:
                nombre_entidad = f"Entidad NIT {nit_clean}"
            
            return JsonResponse({
                'nit': nit_clean,
                'nombre_entidad': nombre_entidad,
                'total_facturado': round(total_facturado, 2),
                'total_radicado': round(total_radicado, 2),
                'total_abonado': round(total_abonado, 2),
                'total_glosas': round(total_glosas, 2),
                'total_anulaciones': round(total_anulaciones, 2),
                'total_saldo_pendiente': round(total_saldo_pendiente, 2),
                'total_glosas_pendientes': round(total_glosas_pendientes, 2),
                'total_glosas_resueltas': round(total_glosas_resueltas, 2),
                'cantidad_facturas': len(facturas_procesadas),
                'facturas': facturas_procesadas
            }, safe=False)
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    def _get_descripcion_tipodoc(self, tipodoc):
        descripciones = {
            2: "Factura",
            29: "Radicación de Cuenta",
            32: "Abono Cartera",
            23: "Glosa",
            24: "Otros Abonos"
        }
        return descripciones.get(tipodoc, f"Tipo {tipodoc}")


class ComprobantesIngresoView(APIView):
    def get(self, request):
        try:
            # Parámetro del periodo: formato '202504'
            periodo = request.query_params.get('periodo')
            if not periodo:
                return Response({'error': 'Debe enviar el parámetro "periodo".'}, status=status.HTTP_400_BAD_REQUEST)

            with connections['contabilidadndx'].cursor() as cursor:
                query = '''
                    SELECT d.Periodo, d.NIT, d.Detalle, d.Debito, d.Credito, n.Nombre
                    FROM detalledocumentos d
                    LEFT JOIN tabladenits n ON d.NIT = n.IDTercero
                    WHERE d.TipoDoc = 3 AND d.Periodo = %s
                '''
                cursor.execute(query, [periodo])
                rows = cursor.fetchall()

                columnas = [col[0] for col in cursor.description]
                resultados = [dict(zip(columnas, row)) for row in rows]

            return Response({'data': resultados}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class RecaudoPeriodoView(APIView):
    def get(self, request):
        periodo = request.query_params.get('periodo')
        if not periodo:
            return Response({"error": "Parámetro 'periodo' es obligatorio"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # ================= CONTABILIDAD =================
            with connections['contabilidadndx'].cursor() as cursor:
                query = '''
                    SELECT d.Documento, d.Detalle, d.NIT, d.Cuenta, d.Debito, d.Credito,
                           n.Nombre AS NombreTercero,
                           c.Nombre AS NombreCuenta,
                           d.FechaCreado
                    FROM detalledocumentos d
                    LEFT JOIN tabladenits n ON d.NIT = n.IDTercero
                    LEFT JOIN cuentas c ON d.Cuenta = c.Cuenta
                    WHERE d.TipoDoc = 3 AND d.Periodo = %s
                    ORDER BY d.Documento
                '''
                cursor.execute(query, [periodo])
                rows = cursor.fetchall()
                columnas = [col[0] for col in cursor.description]
                datos = [dict(zip(columnas, row)) for row in rows]

            documentos = {}
            for row in datos:
                doc = row['Documento']
                if doc not in documentos:
                    fecha_creado = row['FechaCreado']
                    fecha_formateada = fecha_creado.strftime('%d/%m/%Y') if fecha_creado else ''
                    documentos[doc] = {
                        'Documento': doc,
                        'Detalle': row['Detalle'],
                        'FechaCreado': fecha_formateada,
                        'CuentaBanco': '',
                        'NombreBanco': '',
                        'ValorTotal': 0.0,
                        'MovimientosMap': defaultdict(lambda: {
                            'NIT': '',
                            'NombreTercero': '',
                            'Cuenta': '',
                            'NombreCuenta': '',
                            'Credito': 0.0
                        })
                    }

                debito = float(row['Debito'] or 0)
                credito = float(row['Credito'] or 0)

                if debito > 0:
                    documentos[doc]['ValorTotal'] += debito
                    if not documentos[doc]['CuentaBanco']:
                        documentos[doc]['CuentaBanco'] = row['Cuenta']
                        documentos[doc]['NombreBanco'] = row['NombreCuenta']
                elif credito > 0:
                    key = f"{row['NIT']}_{row['Cuenta']}"
                    movimientos_map = documentos[doc]['MovimientosMap'][key]
                    movimientos_map['NIT'] = row['NIT']
                    movimientos_map['NombreTercero'] = row['NombreTercero']
                    movimientos_map['Cuenta'] = row['Cuenta']
                    movimientos_map['NombreCuenta'] = row['NombreCuenta']
                    movimientos_map['Credito'] += credito

            for doc_data in documentos.values():
                doc_data['Movimientos'] = list(doc_data.pop('MovimientosMap').values())

            # ============ DATOSIPSNDX: Cuotas moderadoras y copagos ============
            with connections['zeussalud'].cursor() as cursor:
                cursor.execute('''
                    SELECT df.VrPorCuota, df.VrPorCopago
                    FROM zeussalud.detallefactura df
                    INNER JOIN zeussalud.facturas f ON f.AdmisionNo = df.AdmisionNo
                    INNER JOIN zeussalud.admisiones a ON df.AdmisionNo = a.Consecutivo
                    WHERE f.CUV IS NOT NULL
                      AND f.FacturaAnulada != -1
                      AND a.CodigoEntidad != 'SAN02'
                ''')
                moderadoras = cursor.fetchall()

            total_moderadora = sum((float(cuota or 0) + float(copago or 0)) for cuota, copago in moderadoras)

            # ====== SUMAR AL TOTAL GENERAL ======
            total_recaudo_contable = sum(doc['ValorTotal'] for doc in documentos.values())
            total_general = total_recaudo_contable + total_moderadora

            return Response({
                'recaudos': list(documentos.values()),
                'cuotas_moderadoras': round(total_moderadora, 2),
                'total_recaudo_contable': round(total_recaudo_contable, 2),
                'total_general': round(total_general, 2)
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

GRUPOS = {
    "CONSULTA DE NEUROLOGIA": {
        "cups": {"890274", "890374"},
        "tarifa": 53560,
        "min": 325,
        "ref": 361,
        "max": 397,
        "valor_mes": 19335160
    },
    "CONSULTA DE ELECTROENCEFALOGRAMA": {
        "cups": {"891402", "891901", "891402-1", "891402PED", "891901-1", "891901PED", "891401", "891401PED"},
        "tarifa": 198465,
        "min": 140,
        "ref": 156,
        "max": 172,
        "valor_mes": 30960555
    },
    "CONSULTA DE BLOQUEOS": {
        "cups": {"053106", "053105", "053111"},
        "tarifa": 111303,
        "min": 55,
        "ref": 61,
        "max": 67,
        "valor_mes": 6789478
    },
    "CONSULTA DE APLICACIÓN DE SUSTANCIA": {
        "cups": {"861411", "48201"},
        "tarifa": 270831,
        "min": 16,
        "ref": 18,
        "max": 20,
        "valor_mes": 4874958
    },
    "CONSULTA DE POLISOMNOGRAFIA": {
        "cups": {"891704", "891703", "891704-1", "891704PED", "891703-1", "891703PED"},
        "tarifa": 612433,
        "min": 42,
        "ref": 47,
        "max": 52,
        "valor_mes": 35070488
    },
    "CONSULTA DE  OTROS PROCEDIMIENTOS": {
        "cups": {"891515", "891514", "930820", "891511", "891509", "930860", "891530", "952303", "954626", "952302", "930103", "930821", "954624", "954625"},
        "tarifa": 37613,
        "min": 972,
        "ref": 1080,
        "max": 1188,
        "valor_mes": 35070488
    },
}

class DashboardRiesgoCompartidoView(APIView):
    def get(self, request):
        fecha_inicio_str = request.GET.get("fecha_inicio")
        fecha_fin_str = request.GET.get("fecha_fin")

        if not fecha_inicio_str or not fecha_fin_str:
            return Response({"error": "Debe enviar ?fecha_inicio=dd/mm/yyyy y ?fecha_fin=dd/mm/yyyy"}, status=400)

        fecha_inicio = datetime.strptime(fecha_inicio_str, "%d/%m/%Y")
        fecha_fin = datetime.strptime(fecha_fin_str, "%d/%m/%Y")

        with connections['zeussalud'].cursor() as cursor:
            cursor.execute('''
                SELECT df.AdmisionNo, df.CodigoCUPS, df.FechaServicio, f.Prefijo, df.Cantidad
                FROM zeussalud.detallefactura df
                INNER JOIN zeussalud.admisiones a ON df.AdmisionNo = a.Consecutivo
                INNER JOIN zeussalud.facturas f ON f.AdmisionNo = df.AdmisionNo
                WHERE a.CodigoEntidad = 'SAN02'
                  AND f.Prefijo != 'MGL'
                  AND (f.FacturaAnulada IS NULL OR f.FacturaAnulada = 0)
                  AND df.FechaServicio BETWEEN %s AND %s
                ORDER BY df.FechaServicio ASC
            ''', [fecha_inicio, fecha_fin])
            detalles = cursor.fetchall()

        # Resumen por admisión y filtro de cantidad máxima
        admisiones_count = defaultdict(int)
        for admision, _, _, _, cantidad in detalles:
            admisiones_count[admision] += cantidad

        # Mostrar en consola
        print("===== 🔎 Resumen por admisión (ordenado por cantidad) =====")
        for admision, total in sorted(admisiones_count.items(), key=lambda x: x[1], reverse=True):
            print(f"Admision {admision}: {total}")

        # Filtrar admisiones con más de 40 unidades
        detalles_filtrados = [
            row for row in detalles if admisiones_count[row[0]] <= 40
        ]

        grupo_cups_facturas = defaultdict(lambda: defaultdict(list))
        for admision, cups, fecha_servicio, _, cantidad in detalles_filtrados:
            for nombre_grupo, config in GRUPOS.items():
                if str(cups) in config['cups']:
                    grupo_cups_facturas[nombre_grupo][str(cups)].append((admision, fecha_servicio, cantidad))
                    break

        resultado = []

        for grupo, cups_dict in grupo_cups_facturas.items():
            config = GRUPOS[grupo]
            total_facturas = sum(cantidad for fact_list in cups_dict.values() for _, _, cantidad in fact_list)
            grupo_total = 0

            if total_facturas < config['min']:
                costo_unitario_estimado = config['tarifa']
                estado = "Por debajo del mínimo"
            elif config['min'] <= total_facturas <= config['max']:
                costo_unitario_estimado = config['valor_mes'] / total_facturas
                estado = "Dentro del rango"
            else:
                exceso = total_facturas - config['max']
                costo_unitario_estimado = (config['valor_mes'] + (exceso * (config['tarifa'] / 2))) / total_facturas
                estado = "Por encima del máximo"

            cups_result = []
            contador = 0
            for cups, facturas in cups_dict.items():
                cup_total = 0
                cup_cantidad = 0
                admisiones_detalle = []
                
                for admision, fecha_servicio, cantidad in facturas:
                    admision_valor_total = 0
                    for _ in range(cantidad):
                        contador += 1
                        if total_facturas < config['min']:
                            valor = config['tarifa']
                        elif config['min'] <= total_facturas <= config['max']:
                            valor = config['valor_mes'] / total_facturas
                        else:
                            if contador <= config['max']:
                                valor = config['valor_mes'] / config['max']
                            else:
                                valor = config['tarifa'] / 2
                        cup_total += valor
                        admision_valor_total += valor
                    cup_cantidad += cantidad
                    
                    # Agregar detalle de cada admisión
                    admisiones_detalle.append({
                        "admision_no": admision,
                        "fecha_servicio": fecha_servicio.strftime("%d/%m/%Y") if fecha_servicio else None,
                        "cantidad": cantidad,
                        "valor_total": round(admision_valor_total, 2)
                    })
                
                grupo_total += cup_total
                cups_result.append({
                    "cups": cups,
                    "cantidad": cup_cantidad,
                    "valor_total": round(cup_total, 2),
                    "admisiones": admisiones_detalle
                })

            resumen_cups = {
                cups: sum(cantidad for _, _, cantidad in facturas)
                for cups, facturas in cups_dict.items()
            }

            resultado.append({
                "grupo": grupo,
                "valor_total_pagado": round(grupo_total, 2),
                "cantidad_total_facturas": total_facturas,
                "costo_unitario_estimado": round(costo_unitario_estimado, 2),
                "estado": estado,
                "cups": cups_result,
                "resumen_cups": resumen_cups,
                "cantidad_minima": config["min"],
                "cantidad_referencia": config["ref"],
                "cantidad_maxima": config["max"],
                "valor_mes": config["valor_mes"]
            })

        return Response({
            "fecha_inicio": fecha_inicio.strftime("%d/%m/%Y"),
            "fecha_fin": fecha_fin.strftime("%d/%m/%Y"),
            "grupos": resultado
        })
        
        
        
        
        
        
        


class MedicosView(APIView):
    def get(self, request):
        """
        Obtiene todos los médicos activos desde la tabla usuarios en contabilidadndx
        EsMedico = -1 indica que SÍ es médico
        Activo = 0 indica que el usuario está activo
        Cedula es el identificador único real de cada médico
        """
        try:
            with connections['contabilidadndx'].cursor() as cursor:
                # Consulta para obtener médicos activos con su cédula real
                cursor.execute('''
                    SELECT IdUsuario, NombreReal, NombreUsuario, Cedula, EsMedico, Activo
                    FROM contabilidadndx.usuarios
                    WHERE EsMedico = '-1'
                    AND Activo = 0
                    AND NombreUsuario IS NOT NULL
                    AND NombreUsuario != ''
                    AND Cedula IS NOT NULL
                    AND Cedula != ''
                    ORDER BY NombreReal
                ''')
                medicos = cursor.fetchall()
                print(f"✅ Médicos activos encontrados: {len(medicos)}")

            resultado = []
            for id_usuario, nombre_real, nombre_usuario, cedula, es_medico, activo in medicos:
                resultado.append({
                    'id_usuario': id_usuario,
                    'nombre_real': nombre_real,
                    'nombre_usuario': nombre_usuario,
                    'cedula_medico': cedula  # Cédula real del médico
                })
            
            print(f"✅ Médicos procesados: {len(resultado)}")

            return Response({
                'medicos': resultado,
                'total': len(resultado)
            })

        except Exception as e:
            print(f"❌ Error en MedicosView: {str(e)}")
            return Response({'error': str(e)}, status=500)


class CitasMedicoView(APIView):
    def get(self, request):
        """
        Consulta citas por médico y fecha con filtros de estado
        Parámetros:
        - cedula_medico: Cédula del médico (obligatorio)
        - fecha: Fecha específica (formato YYYY-MM-DD) (opcional)
        - fecha_inicio: Fecha inicio del rango (formato YYYY-MM-DD) (opcional)
        - fecha_fin: Fecha fin del rango (formato YYYY-MM-DD) (opcional)
        - estado: 'confirmadas', 'canceladas', 'todas' (opcional, por defecto 'todas')
        """
        try:
            cedula_medico = request.GET.get('cedula_medico')
            fecha = request.GET.get('fecha')
            fecha_inicio = request.GET.get('fecha_inicio')
            fecha_fin = request.GET.get('fecha_fin')
            estado = request.GET.get('estado', 'todas')

            if not cedula_medico:
                return Response({"error": "El parámetro 'cedula_medico' es obligatorio"}, status=400)

            # Validar parámetros de fecha
            if not fecha and not (fecha_inicio and fecha_fin):
                return Response({"error": "Debe enviar 'fecha' o 'fecha_inicio' y 'fecha_fin'"}, status=400)

            # Construir filtros de fecha
            if fecha:
                filtro_fecha = "DATE(c.FeCita) = %s"
                params_fecha = [fecha]
            else:
                filtro_fecha = "DATE(c.FeCita) BETWEEN %s AND %s"
                params_fecha = [fecha_inicio, fecha_fin]

            # Construir filtro de estado
            filtro_estado = ""
            if estado == 'confirmadas':
                filtro_estado = "AND c.Confirmada = -1 AND (c.Cancelada IS NULL OR c.Cancelada = 0)"
            elif estado == 'canceladas':
                filtro_estado = "AND c.Cancelada = -1"
            elif estado == 'pendientes':
                filtro_estado = "AND (c.Confirmada IS NULL OR c.Confirmada = 0) AND (c.Cancelada IS NULL OR c.Cancelada = 0)"

            with connections['zeussalud'].cursor() as cursor:
                query = f'''
                    SELECT 
                        c.IdCita,
                        c.FeCita,
                        c.FechaSolicitud,
                        c.IdMedico,
                        c.NumeroPaciente,
                        c.Observaciones,
                        c.Procedimiento,
                        c.Confirmada,
                        c.FechaConfirmacion,
                        c.Cancelada,
                        c.FechaCancelacion,
                        c.MotivoCancela,
                        c.CreadoPor,
                        e.NombreEntidad,
                        u.NombreReal as NombreMedico,
                        p.IDPaciente,
                        p.Nombre1,
                        p.Nombre2,
                        p.Apellido1,
                        p.Apellido2,
                        p.Telefono,
                        p.EntidadPaciente as CodigoEntidad
                    FROM zeussalud.citas c
                    LEFT JOIN zeussalud.entidades e ON c.Entidad = e.IDEntidad
                    LEFT JOIN contabilidadndx.usuarios u ON c.IdMedico = u.Cedula
                    LEFT JOIN zeussalud.pacientes p ON c.NumeroPaciente = p.NumeroPaciente
                    WHERE c.IdMedico = %s 
                    AND {filtro_fecha}
                    {filtro_estado}
                    ORDER BY c.FeCita ASC, c.IdCita ASC
                '''
                
                params = [cedula_medico] + params_fecha
                cursor.execute(query, params)
                citas = cursor.fetchall()

            resultado = []
            for cita in citas:
                (id_cita, fe_cita, fecha_solicitud, medico, numero_paciente, 
                 observaciones, procedimiento, confirmada, fecha_confirmacion, 
                 cancelada, fecha_cancelacion, motivo_cancela, creado_por, 
                 nombre_entidad, nombre_medico, id_paciente, nombre1, nombre2, 
                 apellido1, apellido2, telefono, codigo_entidad) = cita

                # Formatear fechas
                fe_cita_str = fe_cita.strftime('%Y-%m-%d') if fe_cita else None  # Solo fecha, sin hora
                fecha_solicitud_str = fecha_solicitud.strftime('%Y-%m-%d %H:%M:%S') if fecha_solicitud else None
                fecha_confirmacion_str = fecha_confirmacion.strftime('%Y-%m-%d %H:%M:%S') if fecha_confirmacion else None
                fecha_cancelacion_str = fecha_cancelacion.strftime('%Y-%m-%d %H:%M:%S') if fecha_cancelacion else None

                # Construir nombre completo del paciente
                nombres = []
                if nombre1:
                    nombres.append(nombre1.strip())
                if nombre2:
                    nombres.append(nombre2.strip())
                if apellido1:
                    nombres.append(apellido1.strip())
                if apellido2:
                    nombres.append(apellido2.strip())
                nombre_completo_paciente = ' '.join(nombres) if nombres else 'Sin nombre'

                # Determinar estado de la cita
                if cancelada == -1:
                    estado_cita = 'Cancelada'
                elif confirmada == -1:
                    estado_cita = 'Confirmada'
                else:
                    estado_cita = 'Pendiente'

                # Agregar fechas según el estado
                cita_data = {
                    'id_cita': id_cita,
                    'fecha_cita': fe_cita_str,
                    'fecha_solicitud': fecha_solicitud_str,
                    'cedula_medico': medico,
                    'nombre_medico': nombre_medico,
                    'numero_documento_paciente': id_paciente,  # Cédula real del paciente
                    'nombre_paciente': nombre_completo_paciente,
                    'numero_paciente_interno': numero_paciente,  # Identificador interno
                    'telefono_paciente': telefono,
                    'codigo_entidad_paciente': codigo_entidad,
                    'entidad': nombre_entidad,
                    'observaciones': observaciones,
                    'procedimiento': procedimiento,
                    'estado': estado_cita,
                    'confirmada': confirmada == -1,
                    'cancelada': cancelada == -1,
                    'creado_por': creado_por
                }

                # Agregar fechas según el estado final de la cita
                if cancelada == -1:
                    # Si está cancelada, solo agregar fecha de cancelación
                    if fecha_cancelacion_str:
                        cita_data['fecha_cancelacion'] = fecha_cancelacion_str
                    if motivo_cancela:
                        cita_data['motivo_cancelacion'] = motivo_cancela
                elif confirmada == -1:
                    # Si está confirmada (y no cancelada), solo agregar fecha de confirmación
                    if fecha_confirmacion_str:
                        cita_data['fecha_confirmacion'] = fecha_confirmacion_str

                resultado.append(cita_data)

            # Estadísticas
            total_citas = len(resultado)
            confirmadas = len([c for c in resultado if c['estado'] == 'Confirmada'])
            canceladas = len([c for c in resultado if c['estado'] == 'Cancelada'])
            pendientes = len([c for c in resultado if c['estado'] == 'Pendiente'])

            return Response({
                'citas': resultado,
                'estadisticas': {
                    'total': total_citas,
                    'confirmadas': confirmadas,
                    'canceladas': canceladas,
                    'pendientes': pendientes
                },
                'filtros_aplicados': {
                    'cedula_medico': cedula_medico,
                    'fecha': fecha,
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'estado': estado
                }
            })

        except Exception as e:
            return Response({'error': str(e)}, status=500)
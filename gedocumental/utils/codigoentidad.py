from django.db import connections
from rest_framework.decorators import api_view
from django.http import JsonResponse

TIPOS_DOCUMENTOS_ESTANDAR = [
    'FACTURA', 'COMPROBANTE', 'AUTORIZACION', 'ORDEN',
    'ADICIONALES', 'RESULTADO', 'HCNEURO', 'HCLINICA',
]

def obtener_entidades_activas():
    try:
        with connections['zeussalud'].cursor() as cursor:
            cursor.execute('''
                SELECT LTRIM(RTRIM(codigo))
                FROM sis_empre
                WHERE codigo IS NOT NULL AND codigo != ''
            ''')
            return {row[0].upper() for row in cursor.fetchall()}
    except Exception:
        return set()

def obtener_tipos_documentos_por_entidad(codigo_entidad):
    if not codigo_entidad:
        return []

    codigo_upper = codigo_entidad.strip().upper()
    entidades_activas = obtener_entidades_activas()

    if codigo_upper not in entidades_activas:
        return []

    return list(TIPOS_DOCUMENTOS_ESTANDAR)

@api_view(['GET'])
def obtener_hallazgos(request):
    try:
        opciones = [
            {"id": 1, "descripcion": "AUTORIZACIÓN VENCIDA"},
            {"id": 2, "descripcion": "AUTORIZACIÓN CON TACHONES"},
            {"id": 3, "descripcion": "DOCUMENTOS ILEGIBLES O MAL ESCANEADOS"},
            {"id": 4, "descripcion": "ERROR DE COPAGO/ERROR DE CUOTA MODERADORA"},            
            {"id": 5, "descripcion": "ERROR DE TARIFA"},
            {"id": 6, "descripcion": "ERROR EN CANTIDADES"},
            {"id": 7, "descripcion": "ERROR DE CONTRATO"},
            {"id": 8, "descripcion": "ERROR DE CUPS"},
            {"id": 9, "descripcion": "ERROR NÚMERO DE AUTORIZACIÓN"},
            {"id": 10, "descripcion": "ERROR TIPO DOCUMENTO PTE"},
            {"id": 11, "descripcion": "ERROR NÚMERO DOCUMENTO DEL PTE"},
            {"id": 12, "descripcion": "ERROR NOMBRE DEL PTE"},
            {"id": 13, "descripcion": "ERROR EN NOMBRE/CANTIDAD DEL MEDIO DE CONTRASTE"},
            {"id": 14, "descripcion": "ERROR EN LATERALIDAD(NO ES ERROR CANTIDAD)"},
            {"id": 15, "descripcion": "ERROR EDAD"},
            {"id": 16, "descripcion": "ERROR EN INSUMOS/INSUMOS INCOMPLETOS"},
            {"id": 17, "descripcion": "ERROR EN RESULTADO/INCOHERENCIA EN RESULTADO"},
            {"id": 18, "descripcion": "FALTA FIRMA DEL COMPROBANTE"},      
            {"id": 19, "descripcion": "FALTA SOPORTE/REPORTE/HC/RESULTADO/ORDEN MEDICA"},
            {"id": 20, "descripcion": "FALTA SOPORTE VALE"},
            {"id": 21, "descripcion": "NO COBRO DE COPAGO/NO COBRO CUOTA MODERADORA"},  
            {"id": 22, "descripcion": "ORDEN MÉDICA VENCIDA"},       
            {"id": 23, "descripcion": "SOPORTE/REPORTE/HC/ RESULTADO/ ADJUNTO NO CORRESPONDE"},
          
            
        ]
        response_data = {
            "success": True,
            "detail": "Hallazgos obtenidos exitosamente",
            "data": opciones
        }
        return JsonResponse(response_data, status=200)
    except Exception as e:
        response_data = {
            "success": False,
            "detail": "Error interno del servidor",
            "error_details": str(e)
        }
        return JsonResponse(response_data, status=500)




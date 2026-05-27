from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.db.models import Q, Count, Avg
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.conf import settings


from .models import (
    Evaluacion, AsignacionEvaluacion, PreguntaComponente360, 
    CategoriaPregunta, PlantillaEvaluacion, RespuestaEvaluacion,
    EvaluacionActividad, AsignacionActividad, Actividad, Area, Componente, TipoComponente,
    LiderActividad, ContratoUsuario, HorarioLaboral
)
from django.contrib.auth import get_user_model
from .serializer import (
    EvaluacionSerializer, EvaluacionDashboardSerializer, AsignacionEvaluacionSerializer, 
    PreguntaComponente360Serializer, CategoriaPreguntaSerializer,
    PlantillaEvaluacionSerializer, RespuestaEvaluacionSerializer,
    EvaluacionActividadSerializer, AsignacionActividadSerializer, AreaSerializer, ComponenteSerializer,
    TipoComponenteSerializer, ActividadSerializer, UserConPerfilSerializer,
    LiderActividadSerializer, ContratoUsuarioSerializer, HorarioLaboralSerializer
)
from .services import (
    obtener_actividades_para_evaluar_lider, obtener_evaluaciones_360_para_lider,
    obtener_evaluaciones_360_para_companero, evaluar_actividad_laboral,
    evaluar_360_completa, obtener_resumen_evaluaciones_lider,
    obtener_preguntas_evaluacion_360, obtener_evaluaciones_pendientes_usuario,
    obtener_preguntas_por_categoria, calcular_promedio_evaluacion, obtener_estadisticas_evaluacion,
    asignar_evaluacion_lider, validar_contrato_para_evaluacion, obtener_dashboard_contratos_urgentes
)

# Servicios para el horario laboral
import pandas as pd
from datetime import datetime, timedelta
from django.core.exceptions import ValidationError
from .models import HorarioLaboral, ContratoUsuario

def procesar_excel_horario_laboral(request):
    """Procesar archivo Excel de horario laboral - NUEVA IMPLEMENTACIÓN"""
    try:
        if 'archivo' not in request.FILES:
            return {
                'success': False,
                'error': 'No se proporcionó archivo'
            }
        
        archivo = request.FILES['archivo']
        nombre_archivo = archivo.name
        
        # Verificar extensión
        if not nombre_archivo.endswith(('.xlsx', '.xls')):
            return {
                'success': False,
                'error': 'El archivo debe ser un Excel (.xlsx o .xls)'
            }
        
        # Leer archivo Excel
        try:
            df = pd.read_excel(archivo)
            print(f"📊 Archivo leído: {nombre_archivo}")
            print(f"📊 Columnas encontradas: {list(df.columns)}")
            print(f"📊 Filas encontradas: {len(df)}")
        except Exception as e:
            return {
                'success': False,
                'error': f'Error leyendo archivo Excel: {str(e)}'
            }
        
        # Verificar columnas requeridas
        columnas_requeridas = ['Apellidos', 'Nombre', 'Identificador', 'Grupo', 'Fecha']
        columnas_faltantes = [col for col in columnas_requeridas if col not in df.columns]
        
        if columnas_faltantes:
            return {
                'success': False,
                'error': f'Columnas faltantes: {columnas_faltantes}'
            }
        
        # Función para convertir tiempo de Excel a minutos
        def parsear_atraso_excel(valor):
            if pd.isna(valor) or valor == 'NaT' or valor == '' or valor is None:
                return 0
            
            try:
                # Si es string, limpiar espacios
                if isinstance(valor, str):
                    valor = valor.strip()
                    if not valor:
                        return 0
                    
                    # Si es string, intentar convertir HH:MM a minutos
                    if ':' in valor:
                        partes = valor.split(':')
                        if len(partes) == 2:
                            try:
                                horas = int(partes[0])
                                minutos = int(partes[1])
                                total_minutos = horas * 60 + minutos
                                print(f"    🔍 Parseado HH:MM '{valor}' → {horas}h {minutos}m = {total_minutos} min")
                                return total_minutos
                            except ValueError:
                                print(f"    ⚠️ Error parseando HH:MM '{valor}'")
                                return 0
                    
                    # Si no es HH:MM, intentar convertir a número
                    if valor.replace('.', '').replace('-', '').replace(',', '').isdigit():
                        numero = float(valor)
                        print(f"    🔍 Parseado número '{valor}' → {numero} min")
                        return numero
                    else:
                        print(f"    ⚠️ Valor no numérico ni HH:MM: '{valor}'")
                        return 0
                
                elif isinstance(valor, (int, float)):
                    print(f"    🔍 Parseado valor numérico {valor} → {valor} min")
                    return float(valor)
                
                # NUEVO: Manejar Timedelta de pandas
                elif hasattr(valor, 'total_seconds'):
                    # Es un Timedelta (pandas o datetime)
                    total_segundos = valor.total_seconds()
                    total_minutos = int(total_segundos / 60)
                    print(f"    🔍 Parseado Timedelta '{valor}' → {total_segundos}s = {total_minutos} min")
                    return total_minutos
                
                else:
                    print(f"    ⚠️ Tipo de valor no reconocido: {type(valor)} = '{valor}'")
                    return 0
                    
            except Exception as e:
                print(f"    ❌ Error parseando valor '{valor}': {e}")
                return 0
        
        # Función para calcular porcentaje según tabla de porcentajes
        def calcular_porcentaje_atraso(total_minutos_atraso):
            if total_minutos_atraso == 0:
                return 100
            elif total_minutos_atraso <= 120:  # 0-120 minutos
                return 80
            elif total_minutos_atraso <= 240:  # 121-240 minutos
                return 60
            elif total_minutos_atraso <= 360:  # 241-360 minutos
                return 40
            elif total_minutos_atraso <= 480:  # 361-480 minutos
                return 20
            else:  # Más de 480 minutos
                return 0
        
        # Función para determinar estado según porcentaje
        def determinar_estado(porcentaje):
            if porcentaje == 100:
                return "PERFECTO"
            elif porcentaje >= 80:
                return "BUENO"
            elif porcentaje >= 60:
                return "REGULAR"
            elif porcentaje >= 40:
                return "DEFICIENTE"
            elif porcentaje >= 20:
                return "MALO"
            else:
                return "CRÍTICO"
        
        registros_procesados = []
        total_registros = len(df)
        total_validos = 0
        total_advertencias = 0
        total_errores = 0
        
        # Diccionario para consolidar datos por usuario
        consolidado_usuarios = {}
        
        # Mostrar todas las columnas disponibles en la primera fila
        print(f"🔍 TODAS LAS COLUMNAS DISPONIBLES: {list(df.columns)}")
        
        # Mostrar todas las columnas del Excel con sus primeros valores
        print(f"🔍 ANÁLISIS COMPLETO DE COLUMNAS:")
        for i, col in enumerate(df.columns):
            primeros_valores = df[col].head(3).tolist()
            print(f"  Col {i}: '{col}' = {primeros_valores}")
        
        # Usar directamente las columnas conocidas del Excel
        # Según la imagen: K=10 (Atraso), Q=16 (Atraso), U=20 (Adelanto)
        columnas_atraso = {
            'atraso_entrada': 10,  # Columna K - Primera columna de Atraso
            'atraso_almuerzo': 16,  # Columna Q - Segunda columna de Atraso
            'adelanto': 20          # Columna U - Columna de Adelanto
        }
        
        print(f"🔍 COLUMNAS DE ATRASO CONOCIDAS: {columnas_atraso}")
        print(f"🔍 Columna K (índice 10): '{df.columns[10] if len(df.columns) > 10 else 'NO EXISTE'}'")
        print(f"🔍 Columna Q (índice 16): '{df.columns[16] if len(df.columns) > 16 else 'NO EXISTE'}'")
        print(f"🔍 Columna U (índice 20): '{df.columns[20] if len(df.columns) > 20 else 'NO EXISTE'}'")
        
        # Verificar que las columnas existan
        if len(df.columns) <= 20:
            print(f"❌ ERROR: El Excel solo tiene {len(df.columns)} columnas, pero necesitamos al menos 21 columnas")
            return {
                'success': False,
                'error': f'El Excel debe tener al menos 21 columnas, pero solo tiene {len(df.columns)}'
            }
        
        # Mostrar los primeros valores de las columnas de atraso para debug
        print(f"\n🔍 VALORES DE LAS COLUMNAS DE ATRASO (primeras 5 filas):")
        print(f"Columna K (índice 10): {df.iloc[:5, 10].tolist()}")
        print(f"Columna Q (índice 16): {df.iloc[:5, 16].tolist()}")
        print(f"Columna U (índice 20): {df.iloc[:5, 20].tolist()}")
        print()
        
        for index, row in df.iterrows():
            try:
                # Extraer datos básicos
                apellidos = str(row.get('Apellidos', '')).strip()
                nombre = str(row.get('Nombre', '')).strip()
                nombre_completo = f"{apellidos} {nombre}".strip()
                identificador = str(row.get('Identificador', '')).strip()
                grupo = str(row.get('Grupo', '')).strip()
                
                # Convertir formato de fecha de "Mar 17-06-2025" a objeto datetime
                fecha_excel = row.get('Fecha', '')
                fecha = None
                try:
                    if pd.notna(fecha_excel) and str(fecha_excel).strip():
                        # Si es string, convertir formato
                        if isinstance(fecha_excel, str):
                            # Formato: "Mar 17-06-2025" -> datetime object
                            import re
                            from datetime import datetime
                            match = re.search(r'(\w+)\s+(\d+)-(\d+)-(\d+)', fecha_excel)
                            if match:
                                dia_semana, dia, mes, anio = match.groups()
                                fecha_str = f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}"
                                fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
                                print(f"🔍 Fila {index + 1} - Fecha convertida: '{fecha_excel}' → '{fecha}'")
                            else:
                                # Intentar parsear directamente si no coincide el patrón
                                fecha = pd.to_datetime(fecha_excel).date()
                                print(f"⚠️ Fila {index + 1} - Fecha parseada con pandas: '{fecha_excel}' → '{fecha}'")
                        else:
                            # Si es datetime de pandas, convertir a date
                            fecha = fecha_excel.date()
                            print(f"🔍 Fila {index + 1} - Fecha datetime convertida: '{fecha_excel}' → '{fecha}'")
                except Exception as e:
                    print(f"Error convirtiendo fecha '{fecha_excel}': {e}")
                    # Fecha por defecto como objeto date
                    from datetime import date
                    fecha = date(2025, 1, 1)
                
                turno = str(row.get('Turno', '')).strip()
                
                # Extraer y convertir campos de tiempo
                entro = row.get('Entró', '')
                salio_manana = row.get('Salió', '')
                entro_tarde = row.get('Entró (after lunch)', '')
                salio_tarde = row.get('Salió (after lunch)', '')
                
                # DEBUG: Mostrar todas las columnas del Excel para identificar las correctas
                if index < 3:  # Solo para las primeras 3 filas
                    print(f"🔍 Fila {index + 1} - COLUMNAS DISPONIBLES:")
                    for i, col in enumerate(df.columns):
                        print(f"    Col {i}: '{col}' = {row[col]}")
                
                # LÓGICA CORREGIDA: Usar las columnas encontradas automáticamente
                atraso_entrada = 0
                atraso_almuerzo = 0
                adelanto = 0
                
                # Función auxiliar para leer valor de columna (índice)
                def leer_valor_columna(indice_columna):
                    valor = row.iloc[indice_columna]
                    nombre_columna = df.columns[indice_columna] if indice_columna < len(df.columns) else f"Columna {indice_columna}"
                    print(f"🔍 Fila {index + 1} - Columna {indice_columna} ('{nombre_columna}'): {valor} (tipo: {type(valor)})")
                    return valor
                
                # Leer atraso entrada usando la columna encontrada
                if columnas_atraso['atraso_entrada']:
                    valor_atraso_entrada = leer_valor_columna(columnas_atraso['atraso_entrada'])
                    atraso_entrada = parsear_atraso_excel(valor_atraso_entrada)
                    print(f"🔍 Fila {index + 1} - Atraso entrada parseado: {valor_atraso_entrada} → {atraso_entrada} minutos")
                
                # Leer atraso almuerzo usando la columna encontrada
                if columnas_atraso['atraso_almuerzo']:
                    valor_atraso_almuerzo = leer_valor_columna(columnas_atraso['atraso_almuerzo'])
                    atraso_almuerzo = parsear_atraso_excel(valor_atraso_almuerzo)
                    print(f"🔍 Fila {index + 1} - Atraso almuerzo parseado: {valor_atraso_almuerzo} → {atraso_almuerzo} minutos")
                
                # Leer adelanto usando la columna encontrada
                if columnas_atraso['adelanto']:
                    valor_adelanto = leer_valor_columna(columnas_atraso['adelanto'])
                    adelanto = parsear_atraso_excel(valor_adelanto)
                    print(f"🔍 Fila {index + 1} - Adelanto parseado: {valor_adelanto} → {adelanto} minutos")
                
                # Calcular total de minutos de atraso (solo atrasos, no adelantos)
                total_minutos_atraso = atraso_entrada + atraso_almuerzo
                
                # Debug: imprimir valores de atraso
                print(f"🔍 Fila {index + 1} - Atrasos: entrada={atraso_entrada}, almuerzo={atraso_almuerzo}, adelanto={adelanto}")
                print(f"🔍 Fila {index + 1} - Total minutos atraso calculado: {total_minutos_atraso}")
                
                # Calcular porcentaje según tabla
                porcentaje = calcular_porcentaje_atraso(total_minutos_atraso)
                print(f"🔍 Fila {index + 1} - Porcentaje calculado: {porcentaje}%")
                
                # Determinar estado
                estado = determinar_estado(porcentaje)
                print(f"🔍 Fila {index + 1} - Estado determinado: {estado}")
                
                # Determinar mensaje
                if total_minutos_atraso == 0:
                    mensaje = "A tiempo"
                else:
                    mensaje = f"Total atraso: {total_minutos_atraso} min"
                
                # Buscar usuario en la base de datos usando usuario_id
                usuario_encontrado = False
                area = "Usuario no encontrado"
                cargo = ""
                usuario_id = None
                
                if identificador and identificador != 'NaT':
                    try:
                        # Buscar en ContratoUsuario usando identificacion
                        contrato = ContratoUsuario.objects.filter(identificacion=identificador, activo=True).first()
                        if contrato and contrato.usuario_id:
                            # Ahora buscar en login_customuser usando usuario_id
                            from django.contrib.auth import get_user_model
                            User = get_user_model()
                            usuario = User.objects.filter(id=contrato.usuario_id).first()
                            
                            if usuario:
                                usuario_encontrado = True
                                usuario_id = contrato.usuario_id
                                area = contrato.area_nombre if contrato.area_nombre else "Sin área"
                                cargo = contrato.cargo_nombre if contrato.cargo_nombre else "Sin cargo"
                                print(f"🔍 Fila {index + 1} - Usuario encontrado: {usuario.username} (ID: {usuario.id})")
                                print(f"🔍 Fila {index + 1} - Área: {area}, Cargo: {cargo}")
                            else:
                                print(f"⚠️ Fila {index + 1} - Usuario no encontrado en login_customuser con ID: {contrato.usuario_id}")
                        else:
                            print(f"⚠️ Fila {index + 1} - Contrato no encontrado con identificación: {identificador}")
                    except Exception as e:
                        print(f"Error buscando usuario {identificador}: {e}")
                
                # Crear registro procesado con todos los campos necesarios para el frontend
                registro = {
                    'fila': index + 1,
                    'identificador': identificador,
                    'nombre_completo': nombre_completo,
                    'grupo': grupo,
                    'fecha': fecha,
                    'turno': turno,
                    'entro': entro,
                    'salio_manana': salio_manana,
                    'entro_tarde': entro_tarde,
                    'salio_tarde': salio_tarde,
                    'minutos_atraso': total_minutos_atraso,
                    'atraso_manana': atraso_entrada,
                    'atraso_almuerzo': atraso_almuerzo,
                    'atraso_salida': 0,  # Por ahora no se procesa
                    'total_minutos_atraso': total_minutos_atraso,
                    'adelanto': adelanto,
                    'hea': 0,  # Horas extras autorizadas
                    'hec': 0,  # Horas extras compensadas
                    'hnt': 0,  # Horas nocturnas trabajadas
                    'ht': 0,   # Horas totales
                    'porcentaje': porcentaje,
                    'estado': estado,
                    'mensaje': mensaje,
                    'usuario_encontrado': usuario_encontrado,
                    'area': area,
                    'cargo': cargo,
                    'usuario_id': usuario_id
                }
                
                registros_procesados.append(registro)
                total_validos += 1
                
                # Consolidar datos por usuario
                if identificador and identificador != 'NaT':
                    if identificador not in consolidado_usuarios:
                        consolidado_usuarios[identificador] = {
                            'identificador': identificador,
                            'nombre_completo': nombre_completo,
                            'grupo': grupo,
                            'area': area,
                            'cargo': cargo,
                            'usuario_id': usuario_id,
                            'total_dias': 0,
                            'total_atraso_entrada': 0,
                            'total_atraso_almuerzo': 0,
                            'total_atraso_salida': 0,
                            'total_adelanto': 0,
                            'total_minutos_atraso': 0,
                            'dias_con_atraso': 0,
                            'dias_sin_atraso': 0,
                            'porcentaje_promedio': 0,
                            'estado_general': 'PERFECTO'
                        }
                    
                    # Sumar datos del día
                    consolidado_usuarios[identificador]['total_dias'] += 1
                    consolidado_usuarios[identificador]['total_atraso_entrada'] += atraso_entrada
                    consolidado_usuarios[identificador]['total_atraso_almuerzo'] += atraso_almuerzo
                    consolidado_usuarios[identificador]['total_adelanto'] += adelanto
                    consolidado_usuarios[identificador]['total_minutos_atraso'] += total_minutos_atraso
                    
                    # Contar días con/sin atraso
                    if total_minutos_atraso > 0:
                        consolidado_usuarios[identificador]['dias_con_atraso'] += 1
                    else:
                        consolidado_usuarios[identificador]['dias_sin_atraso'] += 1
                
            except Exception as e:
                print(f"Error procesando fila {index + 1}: {e}")
                total_errores += 1
                continue
        
        # Calcular porcentajes y estados generales para cada usuario
        for usuario_data in consolidado_usuarios.values():
            if usuario_data['total_dias'] > 0:
                # Calcular porcentaje promedio
                usuario_data['porcentaje_promedio'] = calcular_porcentaje_atraso(usuario_data['total_minutos_atraso'])
                # Determinar estado general
                usuario_data['estado_general'] = determinar_estado(usuario_data['porcentaje_promedio'])
                
                # Convertir minutos a horas para mejor visualización
                usuario_data['total_horas_atraso'] = round(usuario_data['total_minutos_atraso'] / 60, 2)
                usuario_data['total_horas_adelanto'] = round(usuario_data['total_adelanto'] / 60, 2)
        
        # NO guardar automáticamente - solo procesar y mostrar resultados
        print(f"✅ Procesamiento completado. {len(registros_procesados)} registros procesados.")
        print(f"✅ Archivo: {nombre_archivo}")
        print(f"✅ Total registros: {total_registros}")
        print(f"✅ Registros válidos: {total_validos}")
        print(f"✅ Registros con errores: {total_errores}")
        print(f"✅ Usuarios consolidados: {len(consolidado_usuarios)}")
        
        # Mostrar resumen de los primeros 5 registros para validación
        print("\n📋 RESUMEN DE LOS PRIMEROS 5 REGISTROS:")
        for i, registro in enumerate(registros_procesados[:5]):
            print(f"  Fila {registro['fila']}: {registro['nombre_completo']}")
            print(f"    - Atraso entrada: {registro['atraso_manana']} min")
            print(f"    - Atraso almuerzo: {registro['atraso_almuerzo']} min")
            print(f"    - Total atraso: {registro['total_minutos_atraso']} min")
            print(f"    - Porcentaje: {registro['porcentaje']}%")
            print(f"    - Estado: {registro['estado']}")
            print(f"    - Usuario encontrado: {registro['usuario_encontrado']}")
            print(f"    - Área: {registro['area']}")
            print(f"    - Cargo: {registro['cargo']}")
            print()
        
        # Mostrar resumen consolidado por usuario
        print("\n📊 RESUMEN CONSOLIDADO POR USUARIO:")
        for identificador, usuario_data in consolidado_usuarios.items():
            print(f"  Usuario: {usuario_data['nombre_completo']} (ID: {identificador})")
            print(f"    - Total días: {usuario_data['total_dias']}")
            print(f"    - Total atraso entrada: {usuario_data['total_atraso_entrada']} min ({usuario_data['total_horas_atraso']} horas)")
            print(f"    - Total atraso almuerzo: {usuario_data['total_atraso_almuerzo']} min")
            print(f"    - Total adelanto: {usuario_data['total_adelanto']} min ({usuario_data['total_horas_adelanto']} horas)")
            print(f"    - Total minutos atraso: {usuario_data['total_minutos_atraso']} min")
            print(f"    - Días con atraso: {usuario_data['dias_con_atraso']}")
            print(f"    - Porcentaje promedio: {usuario_data['porcentaje_promedio']}%")
            print(f"    - Estado general: {usuario_data['estado_general']}")
            print()
        
        return {
            'success': True,
            'mensaje': 'Archivo procesado correctamente. Revisa los resultados antes de guardar.',
            'modo': 'validacion',  # Indica que es solo validación, no guardado
            'total_registros': total_registros,
            'total_validos': total_validos,
            'total_advertencias': total_advertencias,
            'total_errores': total_errores,
            'resultados': registros_procesados,
            'consolidado_usuarios': list(consolidado_usuarios.values()),  # Agregar consolidado
            'archivo': nombre_archivo,
            'accion_requerida': 'Revisar cálculos y confirmar guardado'
        }
        
    except Exception as e:
        print(f"Error general procesando archivo: {e}")
        return {
            'success': False,
            'error': f'Error procesando archivo: {str(e)}'
        }

User = get_user_model()

# ViewSets básicos que funcionaban antes
class AreaViewSet(viewsets.ModelViewSet):
    queryset = Area.objects.all()
    serializer_class = AreaSerializer

class TipoComponenteViewSet(viewsets.ModelViewSet):
    queryset = TipoComponente.objects.all()
    serializer_class = TipoComponenteSerializer

class ComponenteViewSet(viewsets.ModelViewSet):
    queryset = Componente.objects.all()
    serializer_class = ComponenteSerializer
    
    @action(detail=False, methods=['get'])
    def porcentajes_cumplimiento(self, request):
        """Calcular porcentajes de cumplimiento por área y cargo"""
        try:
            area_id = request.query_params.get('area_id')
            cargo_filtro = request.query_params.get('cargo', '')
            
            print(f"🔍 Buscando porcentajes para área: {area_id}, cargo: {cargo_filtro}")
            
            # Obtener todos los usuarios del área a través de contratos
            from .models import ContratoUsuario
            
            # Si area_id es 0, mostrar todas las áreas
            if area_id == '0' or area_id == 0:
                contratos_query = ContratoUsuario.objects.filter(activo=True)
                area_id = 'todas'
            else:
                if not area_id:
                    return Response({'error': 'area_id requerido'}, status=status.HTTP_400_BAD_REQUEST)
                contratos_query = ContratoUsuario.objects.filter(
                    area=area_id,
                    activo=True
                )
            
            # Filtrar por cargo si se especifica
            if cargo_filtro:
                contratos_query = contratos_query.filter(cargo__icontains=cargo_filtro)
            
            contratos = list(contratos_query)
            print(f"📋 Contratos encontrados: {len(contratos)}")
            
            if not contratos:
                return Response({
                    'area_id': area_id,
                    'cargo_filtro': cargo_filtro,
                    'usuarios': [],
                    'resumen_area': {
                        'total_usuarios': 0,
                        'total_porcentaje': 0,
                        'promedio_cumplimiento': 0
                    },
                    'mensaje': 'No se encontraron usuarios con contratos vigentes en esta área'
                })
            
            # Obtener componentes del área
            if area_id == 'todas':
                componentes = Componente.objects.all().select_related('tipo')
            else:
                componentes = Componente.objects.filter(area_id=area_id).select_related('tipo')
            print(f"🎯 Componentes del área: {len(componentes)}")
            
            resultados_por_usuario = []
            
            for contrato in contratos:
                usuario_id = contrato.usuario_id
                print(f"👤 Procesando usuario: {usuario_id}")
                
                # Obtener información del usuario
                try:
                    user = User.objects.get(id=usuario_id)
                    nombre_usuario = user.get_full_name() or f"Usuario {usuario_id}"
                except User.DoesNotExist:
                    nombre_usuario = f"Usuario {usuario_id}"
                
                cargo_usuario = contrato.cargo or "Sin cargo"
                
                # Procesar cada componente para este usuario
                resultados_componentes = []
                
                for componente in componentes:
                    # Porcentaje 360° (simplificado)
                    porcentaje_360 = 0
                    # Convertir Decimal a float para operaciones matemáticas
                    porcentaje_objetivo = float(componente.tipo.porcentaje_total)
                    
                    if componente.es_360:
                        # Contar evaluaciones 360° completadas
                        count_360 = Evaluacion.objects.filter(
                            componente=componente,
                            usuario_evaluado_id=usuario_id
                        ).count()
                        if count_360 > 0:
                            porcentaje_360 = porcentaje_objetivo * 0.8  # 80% como ejemplo
                    
                    # Porcentaje actividades (simplificado)
                    porcentaje_actividades = 0
                    count_actividades = AsignacionActividad.objects.filter(
                        actividad__componente=componente,
                        usuario_asignado_id=usuario_id
                    ).count()
                    if count_actividades > 0:
                        porcentaje_actividades = porcentaje_objetivo * 0.7  # 70% como ejemplo
                    
                    # Total del componente
                    porcentaje_total = porcentaje_360 + porcentaje_actividades
                    
                    resultados_componentes.append({
                        'componente_id': componente.id,
                        'componente_nombre': componente.nombre,
                        'tipo_nombre': componente.tipo.nombre,
                        'es_360': componente.es_360,
                        'porcentaje_objetivo': porcentaje_objetivo,
                        'porcentaje_360': round(porcentaje_360, 2),
                        'porcentaje_actividades': round(porcentaje_actividades, 2),
                        'porcentaje_total': round(porcentaje_total, 2),
                        'porcentaje_cumplimiento': round((porcentaje_total / porcentaje_objetivo) * 100, 2) if porcentaje_objetivo > 0 else 0
                    })
                
                # Totales del usuario
                total_porcentaje_usuario = sum(r['porcentaje_total'] for r in resultados_componentes)
                promedio_cumplimiento_usuario = sum(r['porcentaje_cumplimiento'] for r in resultados_componentes) / len(resultados_componentes) if resultados_componentes else 0
                
                resultados_por_usuario.append({
                    'usuario_id': usuario_id,
                    'nombre_usuario': nombre_usuario,
                    'cargo': cargo_usuario,
                    'componentes': resultados_componentes,
                    'total_porcentaje': round(total_porcentaje_usuario, 2),
                    'promedio_cumplimiento': round(promedio_cumplimiento_usuario, 2)
                })
            
            # Resumen del área
            total_porcentaje_area = sum(u['total_porcentaje'] for u in resultados_por_usuario)
            promedio_cumplimiento_area = sum(u['promedio_cumplimiento'] for u in resultados_por_usuario) / len(resultados_por_usuario) if resultados_por_usuario else 0
            
            print(f"✅ Procesados {len(resultados_por_usuario)} usuarios")
            
            return Response({
                'area_id': area_id,
                'cargo_filtro': cargo_filtro,
                'usuarios': resultados_por_usuario,
                'resumen_area': {
                    'total_usuarios': len(resultados_por_usuario),
                    'total_porcentaje': round(total_porcentaje_area, 2),
                    'promedio_cumplimiento': round(promedio_cumplimiento_area, 2)
                }
            })
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            traceback_msg = traceback.format_exc()
            print(f"❌ ERROR en porcentajes_cumplimiento: {error_msg}")
            print(f"Traceback: {traceback_msg}")
            
            return Response({
                'error': error_msg,
                'debug_info': {
                    'area_id': area_id,
                    'cargo_filtro': cargo_filtro if 'cargo_filtro' in locals() else '',
                    'traceback': traceback_msg
                }
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _calcular_porcentaje_360(self, componente, usuario_id):
        """
        Calcula el porcentaje de evaluación 360° basado en respuestas reales y pesos de preguntas
        """
        try:
            from .models import PreguntaComponente360, RespuestaEvaluacion, AsignacionEvaluacion
            
            # Obtener preguntas 360° del componente
            preguntas_360 = PreguntaComponente360.objects.filter(
                componente=componente,
                activo=True
            )
            
            if not preguntas_360.exists():
                print(f"⚠️ No hay preguntas 360° activas para componente {componente.id}")
                return 0
            
            # Obtener evaluaciones 360° del usuario
            evaluaciones_360 = AsignacionEvaluacion.objects.filter(
                evaluacion__componente=componente,
                evaluacion__usuario_evaluado_id=usuario_id,
                evaluacion__tipo='360',
                completada=True
            )
            
            if not evaluaciones_360.exists():
                print(f"⚠️ No hay evaluaciones 360° completadas para usuario {usuario_id} en componente {componente.id}")
                return 0
            
            # Calcular promedio ponderado por peso de pregunta
            total_peso = 0
            total_respuestas_ponderadas = 0
            preguntas_respondidas = 0
            
            for pregunta in preguntas_360:
                peso_pregunta = float(pregunta.peso)
                total_peso += peso_pregunta
                
                # Obtener respuestas para esta pregunta
                respuestas = RespuestaEvaluacion.objects.filter(
                    pregunta=pregunta,
                    asignacion__in=evaluaciones_360
                )
                
                if respuestas.exists():
                    preguntas_respondidas += 1
                    
                    # Calcular valor promedio de las respuestas
                    if pregunta.tipo == 'LIKERT':
                        valores = [r.escala_seleccionada.valor for r in respuestas if r.escala_seleccionada]
                        if valores:
                            promedio_respuesta = sum(valores) / len(valores)
                            # Normalizar a porcentaje (asumiendo escala 1-5 o 1-6)
                            max_escala = max(valores) if valores else 5
                            porcentaje_respuesta = (promedio_respuesta / max_escala) * 100
                            total_respuestas_ponderadas += porcentaje_respuesta * peso_pregunta
                    
                    elif pregunta.tipo == 'NUMERICA':
                        valores = [r.respuesta_numerica for r in respuestas if r.respuesta_numerica is not None]
                        if valores:
                            promedio_respuesta = sum(valores) / len(valores)
                            # Normalizar a porcentaje (asumiendo escala 0-10)
                            porcentaje_respuesta = (promedio_respuesta / 10) * 100
                            total_respuestas_ponderadas += porcentaje_respuesta * peso_pregunta
                    
                    elif pregunta.tipo == 'BOOLEANA':
                        valores = [1 if r.respuesta_booleana else 0 for r in respuestas if r.respuesta_booleana is not None]
                        if valores:
                            promedio_respuesta = sum(valores) / len(valores)
                            porcentaje_respuesta = promedio_respuesta * 100
                            total_respuestas_ponderadas += porcentaje_respuesta * peso_pregunta
            
            # Calcular porcentaje final ponderado
            if total_peso > 0 and preguntas_respondidas > 0:
                porcentaje_360 = (total_respuestas_ponderadas / total_peso) * 0.2  # 20% del total del componente
                print(f"✅ Porcentaje 360° calculado: {porcentaje_360:.2f}% para usuario {usuario_id}")
                return round(porcentaje_360, 2)
            else:
                print(f"⚠️ No se pudo calcular porcentaje 360° para usuario {usuario_id}")
                return 0
                
        except Exception as e:
            print(f"❌ Error calculando porcentaje 360°: {str(e)}")
            return 0

    def _calcular_porcentaje_actividades(self, componente, usuario_id):
        """
        Calcula el porcentaje de actividades basado en evaluaciones reales y pesos de actividades
        """
        try:
            from .models import Actividad, AsignacionActividad, EvaluacionActividad
            
            # Obtener actividades del componente
            actividades = Actividad.objects.filter(
                componente=componente
            )
            
            if not actividades.exists():
                print(f"⚠️ No hay actividades para componente {componente.id}")
                return 0
            
            # Obtener asignaciones de actividades del usuario
            asignaciones = AsignacionActividad.objects.filter(
                actividad__componente=componente,
                usuario_asignado_id=usuario_id
            )
            
            if not asignaciones.exists():
                print(f"⚠️ No hay asignaciones de actividades para usuario {usuario_id} en componente {componente.id}")
                return 0
            
            # Calcular promedio ponderado por peso de actividad
            total_peso = 0
            total_actividades_ponderadas = 0
            actividades_evaluadas = 0
            
            for asignacion in asignaciones:
                actividad = asignacion.actividad
                peso_actividad = float(actividad.porcentaje)
                total_peso += peso_actividad
                
                # Verificar si la actividad está completada y evaluada
                if asignacion.completada:
                    evaluacion = EvaluacionActividad.objects.filter(asignacion=asignacion).first()
                    
                    if evaluacion:
                        actividades_evaluadas += 1
                        # Convertir calificación 0-10 a porcentaje
                        calificacion = float(evaluacion.calificacion)
                        porcentaje_actividad = (calificacion / 10.0) * 100
                        total_actividades_ponderadas += porcentaje_actividad * peso_actividad
                        print(f"📊 Actividad {actividad.id}: calificación {calificacion}/10 = {porcentaje_actividad:.2f}% (peso: {peso_actividad})")
                    else:
                        print(f"⚠️ Actividad {actividad.id} completada pero sin evaluación")
                else:
                    print(f"⚠️ Actividad {actividad.id} no completada")
            
            # Calcular porcentaje final ponderado
            if total_peso > 0 and actividades_evaluadas > 0:
                porcentaje_actividades = (total_actividades_ponderadas / total_peso) * 0.6  # 60% del total del componente
                print(f"✅ Porcentaje actividades calculado: {porcentaje_actividades:.2f}% para usuario {usuario_id}")
                return round(porcentaje_actividades, 2)
            else:
                print(f"⚠️ No se pudo calcular porcentaje actividades para usuario {usuario_id}")
                return 0
                
        except Exception as e:
            print(f"❌ Error calculando porcentaje actividades: {str(e)}")
            return 0

    def _calcular_porcentaje_talento_humano(self, componente, usuario_id):
        """
        Calcula el porcentaje de talento humano (20% del total del componente)
        Por ahora retorna un valor base, pero se puede expandir con más criterios
        """
        try:
            # Por ahora retornamos un valor base del 20%
            # En el futuro se puede expandir con criterios como:
            # - Asistencia y puntualidad
            # - Cumplimiento de políticas
            # - Desarrollo profesional
            # - Trabajo en equipo
            
            porcentaje_talento_humano = 20.0 * 0.2  # 20% del total del componente
            print(f"✅ Porcentaje talento humano: {porcentaje_talento_humano:.2f}% para usuario {usuario_id}")
            return round(porcentaje_talento_humano, 2)
            
        except Exception as e:
            print(f"❌ Error calculando porcentaje talento humano: {str(e)}")
            return 0

    @action(detail=False, methods=['get'])
    def dashboard_general(self, request):
        """Dashboard general con toda la información de todas las áreas"""
        try:
            print("🚀 Iniciando dashboard general...")
            
            # Obtener todas las áreas
            from .models import Area, ContratoUsuario, Componente, Evaluacion, AsignacionActividad
            from .services import calcular_desempeno_usuario
            areas = Area.objects.all()
            
            resultados_por_area = []
            total_usuarios_sistema = 0
            total_porcentaje_sistema = 0
            
            for area in areas:
                print(f"🏢 Procesando área: {area.nombre}")
                
                # Usuarios de esta área
                usuarios_area = ContratoUsuario.objects.filter(
                    area=area.id,
                    activo=True
                )
                
                if not usuarios_area.exists():
                    continue
                
                # Componentes de esta área
                componentes_area = Componente.objects.filter(area=area.id).select_related('tipo')
                
                usuarios_con_resultados = []
                total_porcentaje_area = 0
                
                for contrato in usuarios_area:
                    usuario_id = contrato.usuario_id
                    
                    # Obtener nombre del usuario
                    try:
                        user = User.objects.get(id=usuario_id)
                        nombre_usuario = user.get_full_name() or f"Usuario {usuario_id}"
                    except User.DoesNotExist:
                        nombre_usuario = f"Usuario {usuario_id}"
                    
                    # ✅ NUEVO: Calcular desempeño usando la función del dashboard de líderes
                    # Buscar líder del área para calcular desempeño
                    lider_area = None
                    try:
                        from .models import LiderActividad
                        lider_area = LiderActividad.objects.filter(
                            area=area.id,
                            tipo_actividad='FUNCIONES_CONTRATO',
                            activo=True
                        ).first()
                    except:
                        pass
                    
                    # Calcular desempeño si hay líder
                    desempeno_usuario = None
                    if lider_area:
                        try:
                            desempeno_usuario = calcular_desempeno_usuario(
                                usuario_id, 
                                lider_area.lider.id, 
                                area.id
                            )
                            print(f"✅ Desempeño calculado para usuario {usuario_id}: {desempeno_usuario}")
                        except Exception as e:
                            print(f"⚠️ Error calculando desempeño para usuario {usuario_id}: {str(e)}")
                    
                    # Calcular porcentajes por componente (mantener lógica original para compatibilidad)
                    resultados_componentes = []
                    
                    for componente in componentes_area:
                        print(f"🔍 Calculando porcentajes para componente {componente.id} - Usuario {usuario_id}")
                        
                        # Calcular porcentajes usando los métodos auxiliares
                        porcentaje_360 = self._calcular_porcentaje_360(componente, usuario_id)
                        porcentaje_actividades = self._calcular_porcentaje_actividades(componente, usuario_id)
                        porcentaje_talento_humano = self._calcular_porcentaje_talento_humano(componente, usuario_id)
                        
                        # Sumar los 3 componentes
                        porcentaje_total = porcentaje_360 + porcentaje_actividades + porcentaje_talento_humano
                        
                        # Validar que no exceda el 100% del componente
                        porcentaje_objetivo = float(componente.tipo.porcentaje_total)
                        porcentaje_total = min(porcentaje_total, porcentaje_objetivo)
                        
                        # Calcular porcentaje de cumplimiento
                        porcentaje_cumplimiento = (porcentaje_total / porcentaje_objetivo) * 100 if porcentaje_objetivo > 0 else 0
                        
                        # Validar que los porcentajes sumen correctamente
                        suma_porcentajes = porcentaje_360 + porcentaje_actividades + porcentaje_talento_humano
                        if suma_porcentajes > porcentaje_objetivo:
                            print(f"⚠️ ADVERTENCIA: Porcentajes suman {suma_porcentajes:.2f}% pero objetivo es {porcentaje_objetivo}%")
                        
                        resultados_componentes.append({
                            'componente_id': componente.id,
                            'componente_nombre': componente.nombre,
                            'tipo_nombre': componente.tipo.nombre,
                            'es_360': componente.es_360,
                            'porcentaje_objetivo': porcentaje_objetivo,
                            'porcentaje_360': round(porcentaje_360, 2),
                            'porcentaje_actividades': round(porcentaje_actividades, 2),
                            'porcentaje_talento_humano': round(porcentaje_talento_humano, 2),
                            'porcentaje_total': round(porcentaje_total, 2),
                            'porcentaje_cumplimiento': round(porcentaje_cumplimiento, 2),
                            'validacion_porcentajes': {
                                'suma_componentes': round(suma_porcentajes, 2),
                                'objetivo': round(porcentaje_objetivo, 2),
                                'diferencia': round(suma_porcentajes - porcentaje_objetivo, 2),
                                'es_valido': suma_porcentajes <= porcentaje_objetivo
                            }
                        })
                    
                    # Totales del usuario
                    total_usuario = sum(r['porcentaje_total'] for r in resultados_componentes)
                    promedio_usuario = sum(r['porcentaje_cumplimiento'] for r in resultados_componentes) / len(resultados_componentes) if resultados_componentes else 0
                    
                    usuarios_con_resultados.append({
                        'usuario_id': usuario_id,
                        'nombre_usuario': nombre_usuario,
                        'cargo': contrato.cargo or "Sin cargo",
                        'componentes': resultados_componentes,
                        'total_porcentaje': round(total_usuario, 2),
                        'promedio_cumplimiento': round(promedio_usuario, 2),
                        # ✅ NUEVO: Agregar datos de desempeño
                        'desempeno': desempeno_usuario
                    })
                    
                    total_porcentaje_area += total_usuario
                
                # Resumen del área
                promedio_area = total_porcentaje_area / len(usuarios_con_resultados) if usuarios_con_resultados else 0
                
                resultados_por_area.append({
                    'area_id': area.id,
                    'area_nombre': area.nombre,
                    'usuarios': usuarios_con_resultados,
                    'total_usuarios': len(usuarios_con_resultados),
                    'total_porcentaje': round(total_porcentaje_area, 2),
                    'promedio_cumplimiento': round(promedio_area, 2)
                })
                
                total_usuarios_sistema += len(usuarios_con_resultados)
                total_porcentaje_sistema += total_porcentaje_area
            
            # Resumen general del sistema
            promedio_sistema = total_porcentaje_sistema / total_usuarios_sistema if total_usuarios_sistema > 0 else 0
            
            print(f"✅ Dashboard general completado. Áreas: {len(resultados_por_area)}, Usuarios: {total_usuarios_sistema}")
            
            return Response({
                'resumen_sistema': {
                    'total_areas': len(resultados_por_area),
                    'total_usuarios': total_usuarios_sistema,
                    'total_porcentaje': round(total_porcentaje_sistema, 2),
                    'promedio_cumplimiento': round(promedio_sistema, 2)
                },
                'areas': resultados_por_area
            })
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            traceback_msg = traceback.format_exc()
            print(f"❌ ERROR en dashboard_general: {error_msg}")
            print(f"Traceback: {traceback_msg}")
            
            return Response({
                'error': error_msg,
                'debug_info': {
                    'traceback': traceback_msg
                }
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ActividadViewSet(viewsets.ModelViewSet):
    queryset = Actividad.objects.all()
    serializer_class = ActividadSerializer

class EvaluacionViewSet(viewsets.ModelViewSet):
    queryset = Evaluacion.objects.all()
    serializer_class = EvaluacionSerializer

    @action(detail=False, methods=['get'])
    def preguntas_por_categoria(self, request):
        componente_id = request.query_params.get('componente_id')
        if componente_id:
            preguntas = obtener_preguntas_por_categoria(componente_id)
            return Response(preguntas)
        return Response({'error': 'componente_id requerido'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def estadisticas(self, request):
        evaluacion_id = request.query_params.get('evaluacion_id')
        if evaluacion_id:
            estadisticas = obtener_estadisticas_evaluacion(evaluacion_id)
            return Response(estadisticas)
        return Response({'error': 'evaluacion_id requerido'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def asignar_masiva(self, request):
        """Asignar evaluaciones masivamente por área"""
        try:
            data = request.data
            area_id = data.get('area_id')
            componente_id = data.get('componente_id')
            tipo = data.get('tipo')
            
            # Validaciones básicas
            if not all([area_id, componente_id, tipo]):
                return Response({'error': 'Faltan campos requeridos: area_id, componente_id, tipo'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Validar que el tipo sea válido
            if tipo not in ['90', '180', '360']:
                return Response({'error': 'Tipo de evaluación inválido. Debe ser: 90, 180 o 360'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Verificar si ya existen evaluaciones para esta combinación área/componente/tipo
            evaluaciones_existentes = Evaluacion.objects.filter(
                area_grupo_id=area_id,
                componente_id=componente_id,
                tipo=tipo
            ).count()
            
            if evaluaciones_existentes > 0:
                return Response({
                    'error': f'Ya existen {evaluaciones_existentes} evaluaciones {tipo} del componente {componente_id} para el área {area_id}',
                    'evaluaciones_existentes': evaluaciones_existentes
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Usar el servicio existente
            from .services import asignar_evaluacion_por_area
            resultado = asignar_evaluacion_por_area(area_id, componente_id, tipo)
            
            return Response({
                'mensaje': 'Evaluaciones asignadas exitosamente',
                'detalles': {
                    'area_id': area_id,
                    'componente_id': componente_id,
                    'tipo': tipo
                },
                'resultado': resultado
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            print(f"❌ ERROR en asignar_masiva: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def asignar_lider(self, request):
        """Asignar evaluación específica para un líder"""
        try:
            data = request.data
            lider_id = data.get('lider_id')
            componente_id = data.get('componente_id')
            tipo = data.get('tipo', '360')
            
            if not all([lider_id, componente_id]):
                return Response({'error': 'Faltan campos requeridos: lider_id, componente_id'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Validar que el tipo sea válido
            if tipo not in ['90', '180', '360']:
                return Response({'error': 'Tipo de evaluación inválido. Debe ser: 90, 180 o 360'}, status=status.HTTP_400_BAD_REQUEST)
            

            
            # Usar el servicio específico para líderes
            resultado = asignar_evaluacion_lider(lider_id, componente_id, tipo)
            
            # Verificar si el resultado es serializable
            if hasattr(resultado, 'id'):  # Si es un objeto del modelo
                from .serializer import AsignacionEvaluacionSerializer
                serializer = AsignacionEvaluacionSerializer(resultado)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                # Si ya es un diccionario o lista
                return Response(resultado, status=status.HTTP_201_CREATED)
            
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(f"❌ ERROR en asignar_lider: {str(e)}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """Endpoint específico para el dashboard que incluye información de asignaciones y actividades"""
        try:
            print("🚀 Iniciando endpoint dashboard...")
            
            # ✅ NUEVO: Obtener evaluaciones con puntajes reales
            from .models import AsignacionEvaluacion, RespuestaEvaluacion, EvaluacionActividad
            
            evaluaciones = Evaluacion.objects.select_related('componente', 'usuario_evaluado', 'evaluador', 'area_grupo').prefetch_related('asignaciones').all()
            print(f"✅ Evaluaciones encontradas: {evaluaciones.count()}")
            
            # ✅ NUEVO: Calcular puntajes reales para cada evaluación
            evaluaciones_con_puntajes = []
            for evaluacion in evaluaciones:
                # Buscar asignaciones completadas para esta evaluación
                asignaciones_completadas = AsignacionEvaluacion.objects.filter(
                    evaluacion=evaluacion,
                    completada=True
                ).select_related('evaluador', 'usuario_evaluado')
                
                evaluacion_data = EvaluacionDashboardSerializer(evaluacion).data
                
                # ✅ NUEVO: Calcular puntaje real para cada asignación
                asignaciones_con_puntajes = []
                for asignacion in asignaciones_completadas:
                    asignacion_data = {
                        'id': asignacion.id,
                        'evaluador_nombre': asignacion.evaluador.get_full_name(),
                        'usuario_evaluado_nombre': asignacion.usuario_evaluado.get_full_name(),
                        'completada': asignacion.completada
                    }
                    
                    # ✅ NUEVO: Calcular puntaje real de la evaluación 360/180
                    if evaluacion.tipo in ['360', '180']:
                        print(f"🔍 Calculando puntaje para evaluación {evaluacion.id} tipo {evaluacion.tipo}")
                        
                        # Buscar respuestas de esta asignación
                        respuestas = RespuestaEvaluacion.objects.filter(
                            asignacion=asignacion
                        ).select_related('pregunta')
                        
                        print(f"   📝 Respuestas encontradas: {respuestas.count()}")
                        
                        if respuestas.exists():
                            # Calcular puntaje ponderado
                            total_peso = 0
                            total_puntaje = 0
                            
                            for respuesta in respuestas:
                                try:
                                    peso = float(respuesta.pregunta.peso) if respuesta.pregunta.peso else 1
                                    puntaje = 1 if respuesta.respuesta_booleana else 0
                                    
                                    print(f"     💡 Pregunta: {respuesta.pregunta.texto[:50]}...")
                                    print(f"        Peso: {peso}, Respuesta: {respuesta.respuesta_booleana}, Puntaje: {puntaje}")
                                    
                                    total_peso += peso
                                    total_puntaje += puntaje * peso
                                except Exception as e:
                                    print(f"     ❌ Error procesando respuesta {respuesta.id}: {str(e)}")
                                    continue
                            
                            print(f"   📊 Total peso: {total_peso}, Total puntaje: {total_puntaje}")
                            
                            # Calcular porcentaje final
                            if total_peso > 0:
                                porcentaje_final = (total_puntaje / total_peso) * 100
                                asignacion_data['puntaje_360_180'] = {
                                    'porcentaje': round(porcentaje_final, 2),
                                    'total_preguntas': respuestas.count(),
                                    'preguntas_respondidas': respuestas.filter(respuesta_booleana=True).count()
                                }
                                print(f"   ✅ Porcentaje calculado: {porcentaje_final:.2f}%")
                            else:
                                asignacion_data['puntaje_360_180'] = {
                                    'porcentaje': 0,
                                    'total_preguntas': 0,
                                    'preguntas_respondidas': 0
                                }
                                print(f"   ⚠️ No hay peso total para calcular porcentaje")
                        else:
                            asignacion_data['puntaje_360_180'] = {
                                'porcentaje': 0,
                                'total_preguntas': 0,
                                'preguntas_respondidas': 0
                            }
                            print(f"   ⚠️ No hay respuestas para esta asignación")
                    
                    asignaciones_con_puntajes.append(asignacion_data)
                
                evaluacion_data['asignaciones_con_puntajes'] = asignaciones_con_puntajes
                evaluaciones_con_puntajes.append(evaluacion_data)
            
            # ✅ NUEVO: Obtener asignaciones de actividades con puntajes reales
            asignaciones_actividades = AsignacionActividad.objects.all().select_related(
                'actividad', 'usuario_asignado', 'evaluador', 
                'actividad__componente', 'actividad__area_grupo'
            )
            print(f"✅ Asignaciones de actividades encontradas: {asignaciones_actividades.count()}")
            
            # ✅ NUEVO: Calcular puntajes reales para actividades
            asignaciones_con_puntajes_actividades = []
            for asignacion in asignaciones_actividades:
                asignacion_data = AsignacionActividadSerializer(asignacion).data
                
                # ✅ NUEVO: Buscar evaluación de esta actividad
                if asignacion.completada:
                    try:
                        evaluacion_actividad = EvaluacionActividad.objects.get(asignacion=asignacion)
                        print(f"   📊 Actividad {asignacion.actividad.nombre}: Calificación {evaluacion_actividad.calificacion}/10")
                        asignacion_data['puntaje_actividad'] = {
                            'calificacion': float(evaluacion_actividad.calificacion),
                            'porcentaje': (float(evaluacion_actividad.calificacion) / 10) * 100,
                            'comentarios': evaluacion_actividad.comentarios,
                            'fecha_evaluacion': evaluacion_actividad.fecha_evaluacion
                        }
                    except EvaluacionActividad.DoesNotExist:
                        print(f"   ⚠️ Actividad {asignacion.actividad.nombre}: No tiene evaluación")
                        asignacion_data['puntaje_actividad'] = None
                else:
                    print(f"   ⏳ Actividad {asignacion.actividad.nombre}: Pendiente de completar")
                    asignacion_data['puntaje_actividad'] = None
                
                asignaciones_con_puntajes_actividades.append(asignacion_data)
            
            # Debug: Mostrar algunas asignaciones
            if asignaciones_con_puntajes_actividades:
                for i, asignacion in enumerate(asignaciones_con_puntajes_actividades[:3]):
                    print(f"   - Asignación {i+1}: ID={asignacion['id']}, Actividad={asignacion['actividad_nombre']}, Usuario={asignacion['usuario_asignado_nombre']}, Evaluador={asignacion['evaluador_nombre']}, Completada={asignacion['completada']}")
                    if asignacion['puntaje_actividad']:
                        print(f"     Puntaje: {asignacion['puntaje_actividad']['calificacion']}/10 ({asignacion['puntaje_actividad']['porcentaje']:.1f}%)")
            else:
                print("⚠️ No hay asignaciones de actividades en la base de datos")
            
            # Combinar ambos tipos de datos
            dashboard_data = {
                'evaluaciones': evaluaciones_con_puntajes,
                'asignaciones_actividades': asignaciones_con_puntajes_actividades
            }
            
            print(f"✅ Dashboard data preparado: {len(dashboard_data['evaluaciones'])} evaluaciones, {len(dashboard_data['asignaciones_actividades'])} asignaciones")
            return Response(dashboard_data)
        except Exception as e:
            print(f"❌ ERROR en dashboard: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class AsignacionEvaluacionViewSet(viewsets.ModelViewSet):
    queryset = AsignacionEvaluacion.objects.all()
    serializer_class = AsignacionEvaluacionSerializer

class PreguntaComponente360ViewSet(viewsets.ModelViewSet):
    queryset = PreguntaComponente360.objects.all()
    serializer_class = PreguntaComponente360Serializer

class CategoriaPreguntaViewSet(viewsets.ModelViewSet):
    queryset = CategoriaPregunta.objects.all()
    serializer_class = CategoriaPreguntaSerializer

class PlantillaEvaluacionViewSet(viewsets.ModelViewSet):
    queryset = PlantillaEvaluacion.objects.all()
    serializer_class = PlantillaEvaluacionSerializer

class RespuestaEvaluacionViewSet(viewsets.ModelViewSet):
    queryset = RespuestaEvaluacion.objects.all()
    serializer_class = RespuestaEvaluacionSerializer

class AsignacionActividadViewSet(viewsets.ModelViewSet):
    queryset = AsignacionActividad.objects.all()
    serializer_class = AsignacionActividadSerializer
    
    @action(detail=False, methods=['get'])
    def para_lider(self, request):
        """Obtener asignaciones de actividades que un líder debe evaluar"""
        try:
            lider_id = request.GET.get('lider_id')
            area_id = request.GET.get('area_id')
            
            print(f"🔍 DEBUG para_lider: lider_id={lider_id}, area_id={area_id}")
            
            if not lider_id:
                return Response({'error': 'lider_id requerido'}, status=status.HTTP_400_BAD_REQUEST)
            
            # ✅ LÓGICA CORRECTA: Para actividades laborales, filtrar por evaluador_id (líder)
            # Las actividades se asignan específicamente al líder como evaluador
            # ✅ CAMBIAR: Traer TODAS las asignaciones (pendientes Y completadas)
            queryset = AsignacionActividad.objects.filter(
                evaluador_id=lider_id  # ← Filtrar por líder como evaluador
                # ✅ REMOVIDO: completada=False para traer todas
            ).select_related(
                'actividad', 'usuario_asignado', 'evaluador', 
                'actividad__componente', 'actividad__area_grupo'
            )
            
            # ✅ FILTRO ADICIONAL: Por área si se especifica
            if area_id:
                queryset = queryset.filter(actividad__area_grupo_id=area_id)
            
            print(f"🔍 DEBUG para_lider: lider_id={lider_id}, area_id={area_id}")
            print(f"🔍 DEBUG para_lider: Query SQL: {queryset.query}")
            print(f"🔍 DEBUG para_lider: Cantidad de asignaciones encontradas: {queryset.count()}")
            
            # ✅ DEBUG ADICIONAL: Verificar asignaciones completadas vs pendientes
            asignaciones_completadas = queryset.filter(completada=True).count()
            asignaciones_pendientes = queryset.filter(completada=False).count()
            print(f"🔍 DEBUG para_lider: Completadas: {asignaciones_completadas}, Pendientes: {asignaciones_pendientes}")
            
            # ✅ DEBUG ADICIONAL: Mostrar algunas asignaciones como ejemplo
            if queryset.count() > 0:
                primera_asignacion = queryset.first()
                print(f"🔍 DEBUG para_lider: Primera asignación - ID: {primera_asignacion.id}, Completada: {primera_asignacion.completada}, Usuario: {primera_asignacion.usuario_asignado_id}")
            
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'])
    def asignar_masiva(self, request):
        """Asignar actividades masivamente a usuarios con evaluadores"""
        try:
            print(f"🔍 DEBUG asignar_masiva: Iniciando asignación masiva...")
            data = request.data
            print(f"🔍 DEBUG asignar_masiva: Datos recibidos: {data}")
            
            actividad_id = data.get('actividad_id')
            usuarios_ids = data.get('usuarios_ids', [])
            evaluador_id = data.get('evaluador_id')
            fecha_limite = data.get('fecha_limite')
            
            print(f"🔍 DEBUG asignar_masiva: actividad_id={actividad_id}, usuarios_ids={usuarios_ids}, evaluador_id={evaluador_id}, fecha_limite={fecha_limite}")
            
            if not all([actividad_id, usuarios_ids, evaluador_id]):
                print(f"❌ ERROR asignar_masiva: Faltan campos requeridos")
                return Response({'error': 'Faltan campos requeridos'}, status=status.HTTP_400_BAD_REQUEST)
            
            # CORREGIDO: Verificar que el evaluador sea líder de actividades (no de 360°)
            from .models import LiderActividad
            from django.utils import timezone
            from django.db.models import Q
            
            # Filtrar por campos reales de la base de datos
            hoy = timezone.now().date()
            print(f"🔍 DEBUG asignar_masiva: Verificando liderazgo para evaluador_id={evaluador_id}, fecha={hoy}")
            
            try:
                lider_actividad = LiderActividad.objects.get(
                    Q(lider_id=evaluador_id) &
                    Q(activo=True) &
                    Q(fecha_inicio__lte=hoy) &  # Liderazgo iniciado
                    (Q(fecha_fin__isnull=True) | Q(fecha_fin__gte=hoy))  # Para liderazgo vigente: fecha_fin es NULL o fecha_fin >= hoy
                )
                print(f"✅ DEBUG asignar_masiva: Líder encontrado: {lider_actividad}")
            except LiderActividad.DoesNotExist:
                print(f"❌ ERROR asignar_masiva: No se encontró líder para evaluador_id={evaluador_id}")
                return Response({
                    'error': 'El evaluador debe ser un líder de actividades activo y vigente'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            print(f"🔍 DEBUG asignar_masiva: Creando {len(usuarios_ids)} asignaciones...")
            asignaciones_creadas = []
            
            for i, usuario_id in enumerate(usuarios_ids):
                print(f"🔍 DEBUG asignar_masiva: Creando asignación {i+1}/{len(usuarios_ids)} para usuario_id={usuario_id}")
                
                try:
                    # Crear asignación para cada usuario
                    asignacion = AsignacionActividad.objects.create(
                        actividad_id=actividad_id,
                        usuario_asignado_id=usuario_id,
                        evaluador_id=evaluador_id,
                        fecha_limite=fecha_limite
                    )
                    print(f"✅ DEBUG asignar_masiva: Asignación creada exitosamente: ID={asignacion.id}")
                    asignaciones_creadas.append(asignacion.id)
                except Exception as e:
                    print(f"❌ ERROR asignar_masiva: Error creando asignación para usuario_id={usuario_id}: {str(e)}")
                    raise e
            
            print(f"✅ DEBUG asignar_masiva: Se crearon {len(asignaciones_creadas)} asignaciones exitosamente")
            
            return Response({
                'mensaje': f'Se crearon {len(asignaciones_creadas)} asignaciones',
                'asignaciones_creadas': asignaciones_creadas
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            print(f"❌ ERROR asignar_masiva: Excepción general: {str(e)}")
            import traceback
            print(f"❌ ERROR asignar_masiva: Traceback: {traceback.format_exc()}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class EvaluacionActividadViewSet(viewsets.ModelViewSet):
    queryset = EvaluacionActividad.objects.all()
    serializer_class = EvaluacionActividadSerializer
    
    @action(detail=False, methods=['post'])
    def evaluar_actividad(self, request):
        """Evaluar una actividad asignada"""
        try:
            data = request.data
            asignacion_id = data.get('asignacion_id')
            calificacion = data.get('calificacion')
            comentarios = data.get('comentarios', '')
            
            if not all([asignacion_id, calificacion is not None]):
                return Response({'error': 'Faltan campos requeridos'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Verificar que la asignación existe y no esté completada
            asignacion = AsignacionActividad.objects.get(id=asignacion_id)
            if asignacion.completada:
                return Response({'error': 'Esta actividad ya fue evaluada'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Crear la evaluación
            evaluacion = EvaluacionActividad.objects.create(
                asignacion=asignacion,
                calificacion=calificacion,
                comentarios=comentarios
            )
            
            return Response({
                'mensaje': 'Actividad evaluada exitosamente',
                'evaluacion_id': evaluacion.id
            }, status=status.HTTP_201_CREATED)
            
        except AsignacionActividad.DoesNotExist:
            return Response({'error': 'Asignación no encontrada'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ✅ ELIMINADO: Método duplicado que interfería con el método principal
    # El método principal está en la línea 1132 y funciona correctamente

    @action(detail=False, methods=['post'], url_path='evaluar_actividad')
    def evaluar_actividad(self, request):
        """Evaluar una actividad laboral"""
        try:
            data = request.data
            asignacion_id = data.get('asignacion_id')
            calificacion = data.get('calificacion')
            comentarios = data.get('comentarios', '')

            if not all([asignacion_id, calificacion is not None]):
                return Response({'error': 'Faltan campos requeridos: asignacion_id y calificacion'}, status=status.HTTP_400_BAD_REQUEST)

            # Validar calificación
            try:
                calificacion = float(calificacion)
                if calificacion < 0 or calificacion > 10:
                    return Response({'error': 'La calificación debe estar entre 0.0 y 10.0'}, status=status.HTTP_400_BAD_REQUEST)
            except (ValueError, TypeError):
                return Response({'error': 'Calificación inválida'}, status=status.HTTP_400_BAD_REQUEST)

            # Obtener la asignación para extraer actividad_id y evaluador_id
            try:
                from .models import AsignacionActividad
                asignacion = AsignacionActividad.objects.get(id=asignacion_id)
                actividad_id = asignacion.actividad.id
                evaluador_id = asignacion.evaluador.id
            except AsignacionActividad.DoesNotExist:
                return Response({'error': f'Asignación {asignacion_id} no encontrada'}, status=status.HTTP_404_NOT_FOUND)

            resultado = evaluar_actividad_laboral(actividad_id, evaluador_id, calificacion, comentarios)
            
            # Serializar el resultado antes de devolverlo
            serializer = self.get_serializer(resultado)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class Evaluacion360ViewSet(viewsets.ModelViewSet):
    queryset = Evaluacion.objects.filter(tipo='360')
    serializer_class = EvaluacionSerializer

    @action(detail=False, methods=['get'], url_path='para_lider/(?P<lider_id>[^/.]+)')
    def para_lider(self, request, lider_id=None):
        """Obtener evaluaciones 360 que un líder puede evaluar"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_360_para_lider(lider_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='para_companero/(?P<usuario_id>[^/.]+)')
    def para_companero(self, request, usuario_id=None):
        """Obtener evaluaciones 360 que un compañero puede evaluar"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_360_para_companero(usuario_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='evaluar_360')
    def evaluar_360(self, request):
        """Completar una evaluación 360"""
        try:
            data = request.data
            asignacion_id = data.get('asignacion_id')
            evaluador_id = data.get('evaluador_id')
            respuestas_data = data.get('respuestas', [])

            if not all([asignacion_id, evaluador_id, respuestas_data]):
                return Response({'error': 'Faltan campos requeridos'}, status=status.HTTP_400_BAD_REQUEST)

            resultado = evaluar_360_completa(asignacion_id, {
                'evaluador_id': evaluador_id,
                'respuestas': respuestas_data
            })
            
            # Verificar si el resultado es serializable
            if hasattr(resultado, 'id'):  # Si es un objeto del modelo
                from .serializer import AsignacionEvaluacionSerializer
                serializer = AsignacionEvaluacionSerializer(resultado)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                # Si ya es un diccionario o lista
                return Response(resultado, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='preguntas/(?P<asignacion_id>[^/.]+)')
    def preguntas(self, request, asignacion_id=None):
        """Obtener preguntas de una evaluación 360 específica"""
        try:
            preguntas = obtener_preguntas_evaluacion_360(asignacion_id)
            return Response(preguntas)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='pendientes_usuario/(?P<usuario_id>[^/.]+)')
    def pendientes_usuario(self, request, usuario_id=None):
        """Obtener todas las evaluaciones pendientes de un usuario"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_pendientes_usuario(usuario_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ViewSets del dashboard restaurados
class DashboardLiderViewSet(viewsets.ViewSet):
    """ViewSet para dashboard de líderes"""

    @action(detail=False, methods=['get'], url_path='resumen/(?P<lider_id>[^/.]+)')
    def resumen(self, request, lider_id=None):
        """Obtener resumen general para dashboard de líder"""
        try:
            area_id = request.GET.get('area_id')
            resumen = obtener_resumen_evaluaciones_lider(lider_id, area_id)
            return Response(resumen)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='usuarios_a_cargo/(?P<lider_id>[^/.]+)')
    def usuarios_a_cargo(self, request, lider_id=None):
        """Obtener usuarios a cargo del líder"""
        try:
            # Por ahora retornar lista básica de usuarios
            usuarios = User.objects.filter(is_active=True).values('id', 'first_name', 'last_name', 'username', 'email')
            return Response(list(usuarios))
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class DashboardUsuarioViewSet(viewsets.ViewSet):
    """ViewSet para dashboard personal de usuario"""

    @action(detail=False, methods=['get'], url_path='mi_dashboard/(?P<usuario_id>[^/.]+)')
    def mi_dashboard(self, request, usuario_id=None):
        """Obtener dashboard personal del usuario"""
        try:
            # Obtener información básica del usuario
            usuario = get_object_or_404(User, id=usuario_id)
            
            # Obtener evaluaciones pendientes
            evaluaciones_pendientes = obtener_evaluaciones_pendientes_usuario(usuario_id)
            
            dashboard_data = {
                'usuario': {
                    'id': usuario.id,
                    'nombre': f"{usuario.first_name} {usuario.last_name}" if usuario.first_name else usuario.username,
                    'username': usuario.username,
                    'email': usuario.email
                },
                'evaluaciones_pendientes': evaluaciones_pendientes,
                'resumen': {
                    'total_evaluaciones_pendientes': len(evaluaciones_pendientes)
                }
            }
            
            return Response(dashboard_data)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='progreso_evaluacion/(?P<asignacion_id>[^/.]+)')
    def progreso_evaluacion(self, request, asignacion_id=None):
        """Obtener progreso detallado de una evaluación específica"""
        try:
            # Obtener la asignación
            asignacion = get_object_or_404(AsignacionEvaluacion, id=asignacion_id)
            
            # Obtener preguntas y respuestas
            preguntas_data = obtener_preguntas_evaluacion_360(asignacion_id)
            
            # Calcular progreso básico
            total_preguntas = preguntas_data.get('total_preguntas', 0)
            preguntas_respondidas = preguntas_data.get('preguntas_respondidas', 0)
            porcentaje = (preguntas_respondidas / total_preguntas * 100) if total_preguntas > 0 else 0
            
            progreso_data = {
                'asignacion_id': asignacion_id,
                'evaluacion_id': asignacion.evaluacion.id,
                'usuario_evaluado': {
                    'id': asignacion.usuario_evaluado.id,
                    'nombre': f"{asignacion.usuario_evaluado.first_name} {asignacion.usuario_evaluado.last_name}" if asignacion.usuario_evaluado.first_name else asignacion.usuario_evaluado.username
                },
                'componente': {
                    'id': asignacion.evaluacion.componente.id,
                    'nombre': asignacion.evaluacion.componente.nombre
                },
                'progreso_general': {
                    'total_preguntas': total_preguntas,
                    'preguntas_respondidas': preguntas_respondidas,
                    'porcentaje': round(porcentaje, 2),
                    'estado': 'completada' if asignacion.completada else 'en_proceso'
                }
            }
            
            return Response(progreso_data)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ViewSets que funcionaban antes y que eliminé por error
class UsuariosConPerfilViewSet(viewsets.ModelViewSet):
    """ViewSet para obtener usuarios con su perfil"""
    queryset = User.objects.filter(is_active=True).select_related('perfil', 'perfil__area')
    serializer_class = UserConPerfilSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        area_id = self.request.query_params.get('area_id')
        if area_id:
            queryset = queryset.filter(perfil__area_id=area_id)
        return queryset

# NUEVOS VIEWSETS PARA SERVICIOS DE EVALUACIÓN
class ServiciosEvaluacionViewSet(viewsets.ViewSet):
    """ViewSet para servicios de evaluación de actividades laborales y 360"""

    @action(detail=False, methods=['get'], url_path='actividades_laborales_lider/(?P<lider_id>[^/.]+)')
    def actividades_laborales_lider(self, request, lider_id=None):
        """Obtener actividades laborales que un líder puede evaluar"""
        try:
            area_id = request.GET.get('area_id')
            actividades = obtener_actividades_para_evaluar_lider(lider_id, area_id)
            return Response(actividades)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='evaluar_actividad_laboral')
    def evaluar_actividad_laboral(self, request):
        """Permitir que un líder evalúe una actividad laboral"""
        try:
            data = request.data
            asignacion_id = data.get('asignacion_id')
            calificacion = data.get('calificacion')
            comentarios = data.get('comentarios', '')

            if not all([asignacion_id, calificacion is not None]):
                return Response({'error': 'Faltan campos requeridos: asignacion_id y calificacion'}, status=status.HTTP_400_BAD_REQUEST)

            # Validar calificación
            try:
                calificacion = float(calificacion)
                if calificacion < 0 or calificacion > 10:
                    return Response({'error': 'La calificación debe estar entre 0.0 y 10.0'}, status=status.HTTP_400_BAD_REQUEST)
            except (ValueError, TypeError):
                return Response({'error': 'Calificación inválida'}, status=status.HTTP_400_BAD_REQUEST)

            # Obtener la asignación para extraer actividad_id y evaluador_id
            try:
                from .models import AsignacionActividad
                asignacion = AsignacionActividad.objects.get(id=asignacion_id)
                actividad_id = asignacion.actividad.id
                evaluador_id = asignacion.evaluador.id
            except AsignacionActividad.DoesNotExist:
                return Response({'error': f'Asignación {asignacion_id} no encontrada'}, status=status.HTTP_404_NOT_FOUND)

            resultado = evaluar_actividad_laboral(actividad_id, evaluador_id, calificacion, comentarios)
            
            # Serializar el resultado antes de devolverlo
            from .serializer import EvaluacionActividadSerializer
            serializer = EvaluacionActividadSerializer(resultado)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='evaluaciones_360_lider/(?P<lider_id>[^/.]+)')
    def evaluaciones_360_lider(self, request, lider_id=None):
        """Obtener evaluaciones 360 que un líder puede evaluar"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_360_para_lider(lider_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='evaluaciones_360_companero/(?P<usuario_id>[^/.]+)')
    def evaluaciones_360_companero(self, request, usuario_id=None):
        """Obtener evaluaciones 360 que un compañero puede evaluar"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_360_para_companero(usuario_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='evaluar_360_completa')
    def evaluar_360_completa(self, request):
        """Permitir que líderes y compañeros completen una evaluación 360"""
        try:
            data = request.data
            asignacion_id = data.get('asignacion_id')
            evaluador_id = data.get('evaluador_id')
            respuestas_data = data.get('respuestas', [])

            if not all([asignacion_id, evaluador_id, respuestas_data]):
                return Response({'error': 'Faltan campos requeridos'}, status=status.HTTP_400_BAD_REQUEST)

            # Debug: Verificar si la asignación existe
            try:
                from .models import AsignacionEvaluacion
                asignacion = AsignacionEvaluacion.objects.get(id=asignacion_id)
                print(f"DEBUG: Asignación encontrada - ID: {asignacion.id}, Evaluador: {asignacion.evaluador_id}, Completada: {asignacion.completada}")
            except AsignacionEvaluacion.DoesNotExist:
                print(f"DEBUG: Asignación {asignacion_id} no encontrada")
                # Verificar qué asignaciones existen
                todas_asignaciones = AsignacionEvaluacion.objects.all()[:10]
                print(f"DEBUG: Primeras 10 asignaciones: {[{'id': a.id, 'evaluador': a.evaluador_id, 'completada': a.completada} for a in todas_asignaciones]}")
                return Response({
                    'error': f'Asignación {asignacion_id} no encontrada',
                    'debug_info': {
                        'asignacion_id_buscada': asignacion_id,
                        'asignaciones_disponibles': [{'id': a.id, 'evaluador': a.evaluador_id, 'completada': a.completada} for a in todas_asignaciones]
                    }
                }, status=status.HTTP_404_NOT_FOUND)

            resultado = evaluar_360_completa(asignacion_id, {
                'evaluador_id': evaluador_id,
                'respuestas': respuestas_data
            })
            
            # Verificar si el resultado es serializable
            if hasattr(resultado, 'id'):  # Si es un objeto del modelo
                from .serializer import AsignacionEvaluacionSerializer
                serializer = AsignacionEvaluacionSerializer(resultado)
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                # Si ya es un diccionario o lista
                return Response(resultado, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='preguntas_evaluacion_360/(?P<asignacion_id>[^/.]+)')
    def preguntas_evaluacion_360(self, request, asignacion_id=None):
        """Obtener preguntas de una evaluación 360 específica para responder"""
        try:
            preguntas = obtener_preguntas_evaluacion_360(asignacion_id)
            return Response(preguntas)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='evaluaciones_pendientes_usuario/(?P<usuario_id>[^/.]+)')
    def evaluaciones_pendientes_usuario(self, request, usuario_id=None):
        """Obtener todas las evaluaciones pendientes de un usuario (como evaluador)"""
        try:
            area_id = request.GET.get('area_id')
            evaluaciones = obtener_evaluaciones_pendientes_usuario(usuario_id, area_id)
            return Response(evaluaciones)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='debug_asignaciones')
    def debug_asignaciones(self, request):
        """Endpoint de debug para ver todas las asignaciones disponibles"""
        try:
            from .models import AsignacionEvaluacion
            from .serializer import AsignacionEvaluacionSerializer
            
            # Obtener todas las asignaciones con información relacionada
            asignaciones = AsignacionEvaluacion.objects.select_related(
                'evaluacion', 'evaluador', 'usuario_evaluado'
            ).all()[:20]  # Limitar a 20 para no sobrecargar
            
            serializer = AsignacionEvaluacionSerializer(asignaciones, many=True)
            
            return Response({
                'total_asignaciones': AsignacionEvaluacion.objects.count(),
                'asignaciones_muestra': serializer.data,
                'debug_info': {
                    'mensaje': 'Endpoint de debug para verificar asignaciones',
                    'filtros_disponibles': 'Puedes usar ?evaluador_id=X o ?usuario_evaluado_id=Y'
                }
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# NUEVAS VISTAS PARA ACTIVIDADES DE DESEMPEÑO

class LiderActividadViewSet(viewsets.ModelViewSet):
    """ViewSet para gestionar líderes de actividades de desempeño"""
    queryset = LiderActividad.objects.all()
    serializer_class = LiderActividadSerializer
    
    def get_queryset(self):
        """Filtrar por área si se especifica"""
        queryset = super().get_queryset()
        area_id = self.request.query_params.get('area_id')
        if area_id:
            queryset = queryset.filter(area_id=area_id)
        return queryset.select_related('area')
    
    @action(detail=False, methods=['get'])
    def lideres_vigentes(self, request):
        """Obtener líderes vigentes por área"""
        try:
            area_id = request.query_params.get('area_id')
            tipo_actividad = request.query_params.get('tipo_actividad', 'FUNCIONES_CONTRATO')
            
            queryset = LiderActividad.objects.filter(
                activo=True,
                tipo_actividad=tipo_actividad
            )
            
            if area_id:
                queryset = queryset.filter(area_id=area_id)
            
            # Filtrar solo líderes vigentes
            lideres_vigentes = []
            for lider in queryset:
                if lider.es_vigente:
                    # Obtener el nombre del usuario desde el modelo User
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    try:
                        user = User.objects.get(id=lider.lider_id)
                        lider_nombre = user.get_full_name() or f"Usuario {lider.lider_id}"
                    except User.DoesNotExist:
                        lider_nombre = f"Usuario {lider.lider_id}"
                    
                    lideres_vigentes.append({
                        'id': lider.id,
                        'lider_id': lider.lider_id,
                        'lider_nombre': lider_nombre,
                        'area_id': lider.area.id,
                        'area_nombre': lider.area.nombre,
                        'tipo_actividad': lider.tipo_actividad,
                        'fecha_inicio': lider.fecha_inicio,
                        'fecha_fin': lider.fecha_fin,
                        'es_vigente': True
                    })
            
            return Response(lideres_vigentes)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'])
    def asignar_lider_automatico(self, request):
        """Asignar automáticamente un líder para un área y tipo de actividad"""
        try:
            area_id = request.data.get('area_id')
            tipo_actividad = request.data.get('tipo_actividad', 'FUNCIONES_CONTRATO')
            
            if not area_id:
                return Response({'error': 'Se requiere area_id'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Buscar líder vigente
            lider_vigente = LiderActividad.objects.filter(
                area_id=area_id,
                tipo_actividad=tipo_actividad,
                activo=True,
                fecha_inicio__lte=timezone.now().date(),
                fecha_fin__isnull=True
            ).first()
            
            if lider_vigente:
                # Obtener el nombre del usuario desde el modelo User
                from django.contrib.auth import get_user_model
                User = get_user_model()
                try:
                    user = User.objects.get(id=lider_vigente.lider_id)
                    lider_nombre = user.get_full_name() or f"Usuario {lider_vigente.lider_id}"
                except User.DoesNotExist:
                    lider_nombre = f"Usuario {lider_vigente.lider_id}"
                
                return Response({
                    'lider_id': lider_vigente.lider_id,
                    'lider_nombre': lider_nombre,
                    'area_id': lider_vigente.area.id,
                    'area_nombre': lider_vigente.area.nombre,
                    'tipo_actividad': lider_vigente.tipo_actividad,
                    'es_vigente': True
                })
            else:
                return Response({'error': 'No hay líder vigente para el área especificada'}, 
                             status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ContratoUsuarioViewSet(viewsets.ModelViewSet):
    """ViewSet para gestionar contratos de usuarios"""
    queryset = ContratoUsuario.objects.all()
    serializer_class = ContratoUsuarioSerializer
    
    def get_queryset(self):
        """Filtrar por área y usuario si se especifica"""
        queryset = super().get_queryset()
        area_id = self.request.query_params.get('area_id')
        usuario_id = self.request.query_params.get('usuario_id')
        
        if area_id:
            queryset = queryset.filter(area_id=area_id)
        if usuario_id:
            queryset = queryset.filter(usuario_id=usuario_id)
            
        return queryset.select_related('area')
    
    def update(self, request, *args, **kwargs):
        """Override update method to add debugging logs"""
        # Solo mostrar logs en desarrollo
        if settings.DEBUG:
            print(f"🔄 BACKEND: Actualizando contrato {kwargs.get('pk')}")
            print(f"🔄 BACKEND: Datos recibidos: {request.data}")
            
            # Log específico de fechas
            if 'fecha_inicio' in request.data:
                print(f"🔄 BACKEND: Fecha inicio recibida: {request.data['fecha_inicio']}")
            if 'fecha_fin' in request.data:
                print(f"🔄 BACKEND: Fecha fin recibida: {request.data['fecha_fin']}")
        
        # Llamar al método padre
        response = super().update(request, *args, **kwargs)
        
        # Log de la respuesta (solo en desarrollo)
        if settings.DEBUG:
            print(f"🔄 BACKEND: Respuesta enviada: {response.data}")
            if hasattr(response, 'data') and response.data:
                if 'fecha_inicio' in response.data:
                    print(f"🔄 BACKEND: Fecha inicio en respuesta: {response.data['fecha_inicio']}")
                if 'fecha_fin' in response.data:
                    print(f"🔄 BACKEND: Fecha fin en respuesta: {response.data['fecha_fin']}")
        
        return response
    
    def list(self, request, *args, **kwargs):
        """Override list method to add debugging logs"""
        # Solo mostrar logs en desarrollo
        if settings.DEBUG:
            print(f"🔄 BACKEND: Listando contratos")
        
        response = super().list(request, *args, **kwargs)
        
        # Log de los datos que se envían (solo en desarrollo)
        if settings.DEBUG and hasattr(response, 'data') and response.data:
            print(f"🔄 BACKEND: Enviando {len(response.data)} contratos")
            for i, contrato in enumerate(response.data[:3]):  # Solo los primeros 3 para no saturar
                print(f"🔄 BACKEND: Contrato {i+1}: fecha_inicio={contrato.get('fecha_inicio')}, fecha_fin={contrato.get('fecha_fin')}")
        
        return response
    
    @action(detail=False, methods=['get'])
    def contratos_vigentes(self, request):
        """Obtener contratos vigentes por área"""
        try:
            area_id = request.query_params.get('area_id')
            usuario_id = request.query_params.get('usuario_id')
            
            queryset = ContratoUsuario.objects.filter(activo=True)
            
            if area_id:
                queryset = queryset.filter(area_id=area_id)
            if usuario_id:
                queryset = queryset.filter(usuario_id=usuario_id)
            
            # Filtrar solo contratos vigentes
            contratos_vigentes = []
            for contrato in queryset:
                if contrato.es_vigente:
                    # Obtener el nombre del usuario desde el modelo User
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    try:
                        user = User.objects.get(id=contrato.user_id)
                        usuario_nombre = user.get_full_name() or f"Usuario {contrato.user_id}"
                    except User.DoesNotExist:
                        usuario_nombre = f"Usuario {contrato.user_id}"
                    
                    contratos_vigentes.append({
                        'id': contrato.id,
                        'usuario_id': contrato.user_id,
                        'usuario_nombre': usuario_nombre,
                        'area_id': contrato.area.id,
                        'area_nombre': contrato.area.nombre,
                        'tipo_contrato': contrato.tipo_contrato,
                        'fecha_inicio': contrato.fecha_inicio,
                        'fecha_fin': contrato.fecha_fin,
                        'cargo': contrato.cargo,
                        'es_vigente': True,
                        'dias_restantes': contrato.dias_restantes
                    })
            
            return Response(contratos_vigentes)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'])
    def crear_contrato_masivo(self, request):
        """Crear contratos para múltiples usuarios en un área"""
        try:
            data = request.data
            area_id = data.get('area_id')
            usuarios_data = data.get('usuarios', [])
            
            if not area_id or not usuarios_data:
                return Response({'error': 'Se requiere area_id y lista de usuarios'}, 
                             status=status.HTTP_400_BAD_REQUEST)
            
            contratos_creados = []
            errores = []
            
            for usuario_data in usuarios_data:
                try:
                    contrato_data = {
                        'usuario_id': usuario_data['usuario_id'],
                        'area_id': area_id,
                        'tipo_contrato': usuario_data.get('tipo_contrato', 'TERMINO_FIJO'),
                        'fecha_inicio': usuario_data['fecha_inicio'],
                        'fecha_fin': usuario_data.get('fecha_fin'),
                        'cargo': usuario_data.get('cargo', ''),
                        'salario': usuario_data.get('salario')
                    }
                    
                    serializer = self.get_serializer(data=contrato_data)
                    if serializer.is_valid():
                        contrato = serializer.save()
                        # Obtener el nombre del usuario desde el modelo User
                        from django.contrib.auth import get_user_model
                        User = get_user_model()
                        try:
                            user = User.objects.get(id=contrato.user_id)
                            usuario_nombre = user.get_full_name() or f"Usuario {contrato.user_id}"
                        except User.DoesNotExist:
                            usuario_nombre = f"Usuario {contrato.user_id}"
                        
                        contratos_creados.append({
                            'id': contrato.id,
                            'usuario_nombre': usuario_nombre,
                            'tipo_contrato': contrato.tipo_contrato
                        })
                    else:
                        errores.append({
                            'usuario_id': usuario_data['usuario_id'],
                            'errores': serializer.errors
                        })
                except Exception as e:
                    errores.append({
                        'usuario_id': usuario_data.get('usuario_id'),
                        'errores': str(e)
                    })
            
            return Response({
                'contratos_creados': contratos_creados,
                'errores': errores,
                'total_creados': len(contratos_creados),
                'total_errores': len(errores)
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def contratos_por_vencer(self, request):
        """Obtener contratos ordenados por urgencia de vencimiento"""
        try:
            # Obtener todos los contratos activos
            contratos = ContratoUsuario.objects.filter(activo=True)
            
            # Calcular días restantes y urgencia
            contratos_con_urgencia = []
            hoy = timezone.now().date()
            
            for contrato in contratos:
                if contrato.fecha_fin:
                    dias_restantes = (contrato.fecha_fin - hoy).days
                    
                    # Determinar urgencia
                    if dias_restantes <= 0:
                        urgencia = 'critica'
                    elif dias_restantes <= 30:
                        urgencia = 'alta'
                    elif dias_restantes <= 60:
                        urgencia = 'media'
                    elif dias_restantes <= 90:
                        urgencia = 'baja'
                    else:
                        urgencia = 'vigente'
                    
                    # Solo incluir contratos que no sean vigentes (para mostrar urgencia)
                    if urgencia != 'vigente':
                        contratos_con_urgencia.append({
                            'id': contrato.id,
                            'usuario_id': contrato.usuario_id,
                            'identificacion': contrato.identificacion,
                            'tipo_contrato': contrato.tipo_contrato,
                            'fecha_inicio': contrato.fecha_inicio,
                            'fecha_fin': contrato.fecha_fin,
                            'cargo': contrato.cargo,
                            'area': contrato.area.nombre if contrato.area else 'Sin área',
                            'dias_restantes': max(0, dias_restantes),
                            'urgencia': urgencia
                        })
            
            # Ordenar por urgencia y días restantes
            urgencia_order = {'critica': 0, 'alta': 1, 'media': 2, 'baja': 3}
            contratos_con_urgencia.sort(key=lambda x: (urgencia_order[x['urgencia']], x['dias_restantes']))
            
            return Response({
                'success': True,
                'contratos': contratos_con_urgencia,
                'total': len(contratos_con_urgencia)
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)

    @action(detail=False, methods=['get'])
    def validar_contrato_evaluacion(self, request):
        """Validar si un usuario tiene contrato vigente para poder ser evaluado"""
        try:
            from .services import validar_contrato_para_evaluacion
            
            usuario_id = request.query_params.get('usuario_id')
            area_id = request.query_params.get('area_id')
            
            if not usuario_id or not area_id:
                return Response({
                    'success': False,
                    'error': 'Se requiere usuario_id y area_id'
                }, status=400)
            
            validacion = validar_contrato_para_evaluacion(int(usuario_id), int(area_id))
            
            return Response({
                'success': True,
                'validacion': validacion
            })
            
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)

    @action(detail=False, methods=['get'])
    def dashboard_contratos_urgentes(self, request):
        """Endpoint optimizado para el dashboard de contratos urgentes"""
        try:
            from .services import obtener_dashboard_contratos_urgentes
            
            # Parámetros opcionales
            usuario_id = request.query_params.get('usuario_id')
            area_id = request.query_params.get('area_id')
            limit = request.query_params.get('limit', 10)
            
            # Convertir parámetros
            if usuario_id:
                usuario_id = int(usuario_id)
            if area_id:
                area_id = int(area_id)
            if limit:
                limit = int(limit)
            
            # Obtener datos del dashboard
            resultado = obtener_dashboard_contratos_urgentes(
                usuario_id=usuario_id,
                area_id=area_id,
                limit=limit
            )
            
            if resultado['success']:
                return Response(resultado)
            else:
                return Response(resultado, status=500)
                
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)

from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

@api_view(['POST'])
@parser_classes([MultiPartParser])
def procesar_horario_laboral(request):
    """
    Procesa un archivo Excel con registros de horario laboral
    """
    try:
        if 'archivo' not in request.FILES:
            return Response({
                'success': False,
                'error': 'No se proporcionó ningún archivo'
            }, status=400)
        
        archivo = request.FILES['archivo']
        
        # Validar tipo de archivo
        if not archivo.name.endswith(('.xlsx', '.xls')):
            return Response({
                'success': False,
                'error': 'El archivo debe ser un Excel (.xlsx o .xls)'
            }, status=400)
        
        # Procesar el archivo usando la función local
        resultado = procesar_excel_horario_laboral(request)
        
        if resultado['success']:
            return Response(resultado, status=200)
        else:
            return Response(resultado, status=400)
            
    except Exception as e:
        return Response({
            'success': False,
            'error': f'Error interno: {str(e)}'
        }, status=500)

class HorarioLaboralViewSet(viewsets.ModelViewSet):
    """ViewSet para gestionar registros de horario laboral"""
    serializer_class = HorarioLaboralSerializer
    
    def get_queryset(self):
        """Filtrar por fecha, identificacion y grupo_area si se especifica"""
        try:
            # Verificar si la tabla existe antes de hacer la consulta (compatible con MySQL y SQLite)
            from django.db import connection
            with connection.cursor() as cursor:
                if connection.vendor == 'mysql':
                    cursor.execute("""
                        SELECT TABLE_NAME FROM information_schema.TABLES 
                        WHERE TABLE_SCHEMA = DATABASE() 
                        AND TABLE_NAME = 'evaluaciondesempeno_horariolaboral'
                    """)
                else:
                    cursor.execute("""
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name='evaluaciondesempeno_horariolaboral'
                    """)
                tabla = cursor.fetchone()
                
            if not tabla:
                print("⚠️ Tabla HorarioLaboral no existe en la base de datos")
                return HorarioLaboral.objects.none()
            
            queryset = HorarioLaboral.objects.all()
            fecha = self.request.query_params.get('fecha')
            identificacion = self.request.query_params.get('identificacion')
            grupo_area = self.request.query_params.get('grupo_area')
            
            if fecha:
                queryset = queryset.filter(fecha=fecha)
            if identificacion:
                queryset = queryset.filter(identificacion=identificacion)
            if grupo_area:
                queryset = queryset.filter(grupo_area=grupo_area)
                
            return queryset.order_by('-fecha', 'identificacion')
        except Exception as e:
            # Si hay un error (por ejemplo, tabla no existe), retornar queryset vacío
            print(f"⚠️ Error en HorarioLaboralViewSet.get_queryset: {e}")
            return HorarioLaboral.objects.none()
    
    def list(self, request, *args, **kwargs):
        """Manejar la lista de horarios laborales con mejor manejo de errores"""
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            print(f"⚠️ Error en HorarioLaboralViewSet.list: {e}")
            return Response({
                'error': 'Error interno del servidor',
                'details': str(e),
                'message': 'No se pudieron cargar los registros de horario laboral'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def test_connection(self, request):
        """Endpoint de prueba para verificar la conexión y estado del modelo"""
        try:
            # Verificar si la tabla existe (compatible con MySQL y SQLite)
            from django.db import connection
            with connection.cursor() as cursor:
                if connection.vendor == 'mysql':
                    cursor.execute("""
                        SELECT TABLE_NAME FROM information_schema.TABLES 
                        WHERE TABLE_SCHEMA = DATABASE() 
                        AND TABLE_NAME = 'evaluaciondesempeno_horariolaboral'
                    """)
                else:
                    cursor.execute("""
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name='evaluaciondesempeno_horariolaboral'
                    """)
                tabla = cursor.fetchone()
            
            if not tabla:
                return Response({
                    'status': 'error',
                    'message': 'Tabla HorarioLaboral no existe en la base de datos',
                    'solution': 'Ejecutar migraciones: python manage.py makemigrations && python manage.py migrate'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Verificar si hay registros
            total_registros = HorarioLaboral.objects.count()
            
            return Response({
                'status': 'success',
                'message': 'Conexión exitosa',
                'tabla_existe': True,
                'total_registros': total_registros,
                'modelo': 'HorarioLaboral',
                'database_vendor': connection.vendor
            })
            
        except Exception as e:
            print(f"⚠️ Error en test_connection: {e}")
            return Response({
                'status': 'error',
                'message': 'Error interno del servidor',
                'details': str(e),
                'error_type': type(e).__name__
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def resumen_por_fecha(self, request):
        """Obtener resumen de horarios por fecha"""
        try:
            fecha = request.query_params.get('fecha')
            if not fecha:
                return Response({'error': 'Se requiere la fecha'}, 
                             status=status.HTTP_400_BAD_REQUEST)
            
            horarios = self.get_queryset().filter(fecha=fecha)
            
            # Calcular estadísticas
            total_registros = horarios.count()
            total_atrasos = horarios.filter(
                Q(atraso_manana__gt=0) | Q(atraso_almuerzo__gt=0) | Q(atraso_salida__gt=0)
            ).count()
            
            # Agrupar por grupo_area
            resumen_por_grupo = {}
            for horario in horarios:
                grupo_area = horario.grupo_area
                if grupo_area not in resumen_por_grupo:
                    resumen_por_grupo[grupo_area] = {
                        'total_usuarios': 0,
                        'atrasos': 0
                    }
                
                resumen_por_grupo[grupo_area]['total_usuarios'] += 1
                if (horario.atraso_manana and horario.atraso_manana > 0 or
                    horario.atraso_almuerzo and horario.atraso_almuerzo > 0 or
                    horario.atraso_salida and horario.atraso_salida > 0):
                    resumen_por_grupo[grupo_area]['atrasos'] += 1
            
            return Response({
                'fecha': fecha,
                'total_registros': total_registros,
                'total_atrasos': total_atrasos,
                'porcentaje_atrasos': (total_atrasos / total_registros * 100) if total_registros > 0 else 0,
                'resumen_por_grupo': resumen_por_grupo
            })
            
        except Exception as e:
            print(f"⚠️ Error en resumen_por_fecha: {e}")
            return Response({
                'error': 'Error interno del servidor',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def periodos_evaluacion(self, request):
        """Obtener todos los períodos de evaluación cargados para trazabilidad"""
        try:
            # ✅ INTELIGENTE: Usar campos de período si existen, sino usar archivos
            try:
                # Intentar usar campos de período (versión completa)
                periodos = HorarioLaboral.objects.filter(
                    fecha_inicio_periodo__isnull=False,
                    fecha_fin_periodo__isnull=False
                ).values(
                    'fecha_inicio_periodo',
                    'fecha_fin_periodo', 
                    'periodo_descripcion',
                    'archivo_origen',
                    'created_at'
                ).distinct().order_by('-created_at')
                
                # Agrupar por archivo para mostrar mejor la trazabilidad
                periodos_agrupados = {}
                for periodo in periodos:
                    archivo = periodo['archivo_origen']
                    if archivo not in periodos_agrupados:
                        periodos_agrupados[archivo] = {
                            'archivo': archivo,
                            'fecha_carga': periodo['created_at'],
                            'periodos': []
                        }
                    
                    periodos_agrupados[archivo]['periodos'].append({
                        'fecha_inicio': periodo['fecha_inicio_periodo'],
                        'fecha_fin': periodo['fecha_fin_periodo'],
                        'descripcion': periodo['periodo_descripcion']
                    })
                
                return Response({
                    'total_archivos': len(periodos_agrupados),
                    'periodos_por_archivo': list(periodos_agrupados.values()),
                    'mode': 'periodos_completos'
                })
                
            except Exception as e:
                print(f"⚠️ Campos de período no disponibles, usando modo archivos: {e}")
                # Fallback: usar solo archivos (versión producción)
                archivos = HorarioLaboral.objects.filter(
                    archivo_origen__isnull=False
                ).values(
                    'archivo_origen',
                    'created_at'
                ).distinct().order_by('-created_at')
                
                # Agrupar por archivo para mostrar mejor la trazabilidad
                archivos_agrupados = {}
                for archivo_info in archivos:
                    archivo = archivo_info['archivo_origen']
                    if archivo not in archivos_agrupados:
                        archivos_agrupados[archivo] = {
                            'archivo': archivo,
                            'fecha_carga': archivo_info['created_at'],
                            'total_registros': HorarioLaboral.objects.filter(
                                archivo_origen=archivo
                            ).count()
                        }
                
                return Response({
                    'total_archivos': len(archivos_agrupados),
                    'archivos_cargados': list(archivos_agrupados.values()),
                    'mode': 'archivos_solo',
                    'message': 'Información basada en archivos de origen (campos de período no disponibles)'
                })
            
        except Exception as e:
            print(f"⚠️ Error en periodos_evaluacion: {e}")
            return Response({
                'error': 'Error interno del servidor',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def estadisticas_usuario(self, request):
        """Obtener estadísticas de horario para un usuario específico"""
        try:
            identificacion = request.query_params.get('identificacion')
            fecha_inicio = request.query_params.get('fecha_inicio')
            fecha_fin = request.query_params.get('fecha_fin')
            
            if not identificacion:
                return Response({'error': 'Se requiere la identificación del usuario'}, 
                             status=status.HTTP_400_BAD_REQUEST)
            
            queryset = self.get_queryset().filter(identificacion=identificacion)
            
            if fecha_inicio:
                queryset = queryset.filter(fecha__gte=fecha_inicio)
            if fecha_fin:
                queryset = queryset.filter(fecha__lte=fecha_fin)
            
            # Calcular estadísticas acumuladas
            total_dias = queryset.count()
            
            # Sumar todos los atrasos acumulados por usuario (ahora en minutos)
            total_atraso_manana = 0
            total_atraso_almuerzo = 0
            total_atraso_salida = 0
            total_adelanto = 0
            total_horas_extra = 0
            total_horas_trabajadas = 0
            
            dias_con_atraso = 0
            dias_con_adelanto = 0
            
            for horario in queryset:
                # Sumar atrasos (ahora en minutos)
                if horario.atraso_manana and horario.atraso_manana > 0:
                    total_atraso_manana += horario.atraso_manana
                    dias_con_atraso += 1
                if horario.atraso_almuerzo and horario.atraso_almuerzo > 0:
                    total_atraso_almuerzo += horario.atraso_almuerzo
                    dias_con_atraso += 1
                if horario.atraso_salida and horario.atraso_salida > 0:
                    total_atraso_salida += horario.atraso_salida
                    dias_con_atraso += 1
                
                # Sumar horas trabajadas (ahora en minutos)
                if horario.total_horas_trabajadas and horario.total_horas_trabajadas > 0:
                    total_horas_trabajadas += horario.total_horas_trabajadas
            
            # Calcular totales (ya están en minutos)
            total_atraso_minutos = total_atraso_manana + total_atraso_almuerzo + total_atraso_salida
            
            # Convertir horas trabajadas de minutos a horas
            total_horas_trabajadas_horas = total_horas_trabajadas / 60 if total_horas_trabajadas > 0 else 0
            
            # Calcular promedio de horas trabajadas por día
            promedio_horas_por_dia = total_horas_trabajadas_horas / total_dias if total_dias > 0 else 0
            
            return Response({
                'identificacion': identificacion,
                'periodo': {
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'total_dias': total_dias
                },
                'resumen_atrasos': {
                    'total_atraso_minutos': total_atraso_minutos,
                    'total_atraso_horas': round(total_atraso_minutos / 60, 2),
                    'dias_con_atraso': dias_con_atraso,
                    'porcentaje_dias_atraso': round((dias_con_atraso / total_dias * 100), 2) if total_dias > 0 else 0,
                    'desglose_atrasos': {
                        'manana': {
                            'total': f"{total_atraso_manana} minutos",
                            'minutos': total_atraso_manana
                        },
                        'almuerzo': {
                            'total': f"{total_atraso_almuerzo} minutos",
                            'minutos': total_atraso_almuerzo
                        },
                        'salida': {
                            'total': f"{total_atraso_salida} minutos",
                            'minutos': total_atraso_salida
                        }
                    }
                },
                'resumen_horas': {
                    'total_horas_trabajadas': round(total_horas_trabajadas_horas, 2),
                    'promedio_horas_por_dia': round(promedio_horas_por_dia, 2)
                },
                'estadisticas_detalladas': {
                    'promedio_atraso_por_dia': round(total_atraso_minutos / total_dias, 2) if total_dias > 0 else 0,
                    'eficiencia_tiempo': round(((total_dias - dias_con_atraso) / total_dias * 100), 2) if total_dias > 0 else 0
                }
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def estadisticas_generales(self, request):
        """Obtener estadísticas generales de todos los usuarios"""
        try:
            fecha_inicio = request.query_params.get('fecha_inicio')
            fecha_fin = request.query_params.get('fecha_fin')
            grupo = request.query_params.get('grupo')
            
            queryset = self.get_queryset()
            
            if fecha_inicio:
                queryset = queryset.filter(fecha__gte=fecha_inicio)
            if fecha_fin:
                queryset = queryset.filter(fecha__lte=fecha_fin)
            if grupo:
                queryset = queryset.filter(grupo=grupo)
            
            # Agrupar por usuario
            usuarios_stats = {}
            
            for horario in queryset:
                identificacion = horario.identificacion
                if identificacion not in usuarios_stats:
                    usuarios_stats[identificacion] = {
                        'identificacion': identificacion,
                        'nombre': horario.nombre_completo,
                        'grupo': horario.grupo_area,
                        'total_dias': 0,
                        'total_atraso_minutos': 0,
                        'dias_con_atraso': 0
                    }
                
                stats = usuarios_stats[identificacion]
                stats['total_dias'] += 1
                
                # Sumar atrasos (ahora en minutos enteros)
                if horario.atraso_manana and horario.atraso_manana > 0:
                    stats['total_atraso_minutos'] += horario.atraso_manana
                    stats['dias_con_atraso'] += 1
                if horario.atraso_almuerzo and horario.atraso_almuerzo > 0:
                    stats['total_atraso_minutos'] += horario.atraso_almuerzo
                    stats['dias_con_atraso'] += 1
                if horario.atraso_salida and horario.atraso_salida > 0:
                    stats['total_atraso_minutos'] += horario.atraso_salida
                    stats['dias_con_atraso'] += 1
                
                # Sumar horas trabajadas (ahora en minutos)
                if horario.total_horas_trabajadas and horario.total_horas_trabajadas > 0:
                    stats['total_horas_trabajadas'] += horario.total_horas_trabajadas
            
            # Calcular promedios y rankings
            for stats in usuarios_stats.values():
                if stats['total_dias'] > 0:
                    stats['promedio_atraso_por_dia'] = round(stats['total_atraso_minutos'] / stats['total_dias'], 2)
                    stats['promedio_adelanto_por_dia'] = round(stats['total_atraso_minutos'] / stats['total_dias'], 2)
                    stats['promedio_horas_por_dia'] = round(stats['total_horas_trabajadas'] / stats['total_dias'], 2)
                    stats['porcentaje_dias_atraso'] = round((stats['dias_con_atraso'] / stats['total_dias']) * 100, 2)
                    stats['eficiencia_tiempo'] = round(((stats['total_dias'] - stats['dias_con_atraso']) / stats['total_dias']) * 100, 2)
                else:
                    stats['promedio_atraso_por_dia'] = 0
                    stats['promedio_adelanto_por_dia'] = 0
                    stats['promedio_horas_por_dia'] = 0
                    stats['porcentaje_dias_atraso'] = 0
                    stats['eficiencia_tiempo'] = 0
            
            # Ordenar por diferentes criterios
            usuarios_lista = list(usuarios_stats.values())
            
            # Crear resumen superior con totales por usuario
            resumen_superior = []
            for stats in usuarios_lista:
                resumen_superior.append({
                    'identificacion': stats.get('identificacion', ''),
                    'nombre': stats['nombre'],
                    'grupo': stats['grupo'],
                    'total_minutos_retraso': stats['total_atraso_minutos'],
                    'total_horas_retraso': round(stats['total_atraso_minutos'] / 60, 2),
                    'dias_con_retraso': stats['dias_con_atraso'],
                    'total_dias': stats['total_dias'],
                    'porcentaje_dias_retraso': stats['porcentaje_dias_atraso'],
                    'eficiencia': stats['eficiencia_tiempo']
                })
            
            # Ordenar por total de minutos de retraso (descendente)
            resumen_superior_ordenado = sorted(resumen_superior, key=lambda x: x['total_minutos_retraso'], reverse=True)
            
            return Response({
                'periodo': {
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'grupo': grupo
                },
                'resumen_superior': {
                    'titulo': 'Resumen de Retrasos por Usuario',
                    'descripcion': 'Total de minutos de retraso acumulados por usuario en el período',
                    'usuarios': resumen_superior_ordenado,
                    'totales_generales': {
                        'total_usuarios': len(usuarios_lista),
                        'total_minutos_retraso': sum(stats['total_atraso_minutos'] for stats in usuarios_lista),
                        'total_horas_retraso': round(sum(stats['total_atraso_minutos'] for stats in usuarios_lista) / 60, 2),
                        'promedio_minutos_por_usuario': round(sum(stats['total_atraso_minutos'] for stats in usuarios_lista) / len(usuarios_lista), 2) if usuarios_lista else 0,
                        'usuarios_con_retraso': len([stats for stats in usuarios_lista if stats['total_atraso_minutos'] > 0]),
                        'usuarios_sin_retraso': len([stats for stats in usuarios_lista if stats['total_atraso_minutos'] == 0])
                    }
                },
                'resumen_general': {
                    'total_usuarios': len(usuarios_lista),
                    'total_dias_registrados': sum(stats['total_dias'] for stats in usuarios_lista),
                    'promedio_atraso_general': round(sum(stats['total_atraso_minutos'] for stats in usuarios_lista) / len(usuarios_lista), 2) if usuarios_lista else 0
                },
                'ranking_usuarios': {
                    'por_eficiencia': sorted(usuarios_lista, key=lambda x: x['eficiencia_tiempo'], reverse=True),
                    'por_atraso': sorted(usuarios_lista, key=lambda x: x['total_atraso_minutos'], reverse=True),
                    'por_adelanto': sorted(usuarios_lista, key=lambda x: x['total_atraso_minutos'], reverse=True)
                },
                'usuarios_detallado': usuarios_lista
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'])
    def resumen_retrasos(self, request):
        """Obtener solo el resumen superior de retrasos por usuario"""
        try:
            fecha_inicio = request.query_params.get('fecha_inicio')
            fecha_fin = request.query_params.get('fecha_fin')
            grupo = request.query_params.get('grupo')
            
            queryset = self.get_queryset()
            
            if fecha_inicio:
                queryset = queryset.filter(fecha__gte=fecha_inicio)
            if fecha_fin:
                queryset = queryset.filter(fecha__lte=fecha_fin)
            if grupo:
                queryset = queryset.filter(grupo=grupo)
            
            # Agrupar por usuario
            usuarios_stats = {}
            
            for horario in queryset:
                identificacion = horario.identificacion
                if identificacion not in usuarios_stats:
                    usuarios_stats[identificacion] = {
                        'identificacion': identificacion,
                        'nombre': horario.nombre_completo,
                        'grupo': horario.grupo_area,
                        'total_dias': 0,
                        'total_atraso_minutos': 0,
                        'dias_con_atraso': 0
                    }
                
                stats = usuarios_stats[identificacion]
                stats['total_dias'] += 1
                
                # Sumar atrasos (ahora en minutos enteros)
                if horario.atraso_manana and horario.atraso_manana > 0:
                    stats['total_atraso_minutos'] += horario.atraso_manana
                    stats['dias_con_atraso'] += 1
                if horario.atraso_almuerzo and horario.atraso_almuerzo > 0:
                    stats['total_atraso_minutos'] += horario.atraso_almuerzo
                    stats['dias_con_atraso'] += 1
                if horario.atraso_salida and horario.atraso_salida > 0:
                    stats['total_atraso_minutos'] += horario.atraso_salida
                    stats['dias_con_atraso'] += 1
            
            # Calcular porcentajes
            for stats in usuarios_stats.values():
                if stats['total_dias'] > 0:
                    stats['porcentaje_dias_retraso'] = round((stats['dias_con_atraso'] / stats['total_dias']) * 100, 2)
                    stats['total_horas_retraso'] = round(stats['total_atraso_minutos'] / 60, 2)
                else:
                    stats['porcentaje_dias_retraso'] = 0
                    stats['total_horas_retraso'] = 0
            
            # Ordenar por total de minutos de retraso (descendente)
            usuarios_lista = list(usuarios_stats.values())
            usuarios_ordenados = sorted(usuarios_lista, key=lambda x: x['total_atraso_minutos'], reverse=True)
            
            return Response({
                'periodo': {
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'grupo': grupo
                },
                'resumen_retrasos': {
                    'titulo': 'Resumen de Retrasos por Usuario',
                    'descripcion': 'Total de minutos de retraso acumulados por usuario en el período',
                    'usuarios': usuarios_ordenados,
                    'totales_generales': {
                        'total_usuarios': len(usuarios_lista),
                        'total_minutos_retraso': sum(stats['total_atraso_minutos'] for stats in usuarios_lista),
                        'total_horas_retraso': round(sum(stats['total_atraso_minutos'] for stats in usuarios_lista) / 60, 2),
                        'promedio_minutos_por_usuario': round(sum(stats['total_atraso_minutos'] for stats in usuarios_lista) / len(usuarios_lista), 2) if usuarios_lista else 0,
                        'usuarios_con_retraso': len([stats for stats in usuarios_lista if stats['total_atraso_minutos'] > 0]),
                        'usuarios_sin_retraso': len([stats for stats in usuarios_lista if stats['total_atraso_minutos'] == 0])
                    }
                }
            })
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

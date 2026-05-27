from datetime import datetime
from django.http import JsonResponse
from rest_framework.response import Response
from django.db import connections
from gedocumental.models import ArchivoFacturacion
from rest_framework.views import APIView
from rest_framework import status
import os
import re
from datetime import datetime
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class LargeDataPagination(PageNumberPagination):
    page_size = 1000  
    page_size_query_param = 'page_size'
    max_page_size = 5000

class LargeDataPagination(PageNumberPagination):
    page_size = 1000
    page_size_query_param = 'page_size'
    max_page_size = 50000

# Vista para procesar los XML
class ProcessedXMLView(APIView):
    def get(self, request, format=None):
        fecha_inicio_str = request.GET.get('fecha_inicio', None)
        fecha_fin_str = request.GET.get('fecha_fin', None)

        if fecha_inicio_str and fecha_fin_str:
            try:
                fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
                fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d')
            except ValueError:
                return Response({"error": "Formato de fecha inválido. El formato correcto es AAAA-MM-DD."},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response({"error": "Se requieren las fechas de inicio y fin."},
                            status=status.HTTP_400_BAD_REQUEST)

        with connections['datosipsndx'].cursor() as cursor:
            query = '''
                SELECT 
                    Id, texto, direccion, FechaCreado, IpOrigen, Procesado, RtaLumier
                FROM tblxmlprocesados
                WHERE FechaCreado BETWEEN %s AND %s
            '''
            cursor.execute(query, [fecha_inicio, fecha_fin])
            rows = cursor.fetchall()

            data = []
            for row in rows:
                record = {
                    'Id': row[0],
                    'Texto': limpiar_caracteres_corruptos(row[1]),  # Limpieza del texto
                    'Direccion': row[2],
                    'FechaCreado': row[3],
                    'IpOrigen': row[4],
                    'Procesado': row[5],
                    'RtaLumier': row[6]
                }
                data.append(record)

        return Response(data)
    
import xml.etree.ElementTree as ET
from io import BytesIO
from django.http import HttpResponse, JsonResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from django.conf import settings
import ftfy  # Librería para corregir caracteres corruptos
from reportlab.graphics.shapes import Drawing, Line
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from PIL import Image as PILImage
import io
import stat
import shutil


# Registrar la fuente Arial y su versión en negrilla
pdfmetrics.registerFont(TTFont('Arial', os.path.join(settings.BASE_DIR, 'fonts', 'Arial.ttf')))
pdfmetrics.registerFont(TTFont('Arial_Bold', os.path.join(settings.BASE_DIR, 'fonts', 'Arial_Bold.ttf')))

# Función para corregir caracteres corruptos usando ftfy
def limpiar_caracteres_corruptos(texto):
    return ftfy.fix_text(texto)

# Función para buscar la orden por OrdenHis y dirección 'E'
def buscar_orden_por_ordenhis(ordenhis):
    with connections['datosipsndx'].cursor() as cursor:
        query = """
            SELECT texto 
            FROM tblxmlprocesados 
            WHERE Direccion = 'E'
        """
        cursor.execute(query)
        rows = cursor.fetchall()

    if not rows:
        print("No se encontraron registros con Direccion = 'E'")
        return None

    for row in rows:
        texto_xml = limpiar_caracteres_corruptos(row[0])  # Limpiar caracteres

        if "<![CDATA[" in texto_xml:
            print("Se detectó CDATA, extrayendo contenido...")
            cdata_match = re.search(r'<!\[CDATA\[(.*?)\]\]>', texto_xml, re.DOTALL)
            if cdata_match:
                texto_xml = cdata_match.group(1)

        orden_encontrada = extract_ordenhis_from_text(texto_xml)
        if orden_encontrada == ordenhis:
            print(f"OrdenHis {ordenhis} coincide, retornando el texto XML.")
            return texto_xml

    return None
# Función para buscar detalles de admisión en la base de datos por OrdenHis
def buscar_admision_por_ordenhis(ordenhis):
    with connections['datosipsndx'].cursor() as cursor:
        query = """
            SELECT CodigoEntidad, FechaCreado 
            FROM admisiones
            WHERE Consecutivo = %s
        """
        cursor.execute(query, [ordenhis])
        result = cursor.fetchone()
    
    if result:
        codigo_entidad, fecha_creado = result
        return {"CodigoEntidad": codigo_entidad, "FechaCreado": fecha_creado}
    else:
        return None

# Función para buscar detalles del paciente usando Documento como IDPaciente
def buscar_paciente_por_documento(documento):
    with connections['datosipsndx'].cursor() as cursor:
        query = """
            SELECT FechaNacimiento 
            FROM pacientes
            WHERE IDPaciente = %s
        """
        cursor.execute(query, [documento])
        result = cursor.fetchone()

    if result:
        fecha_nacimiento = result[0]
        return {"FechaNacimiento": fecha_nacimiento}
    else:
        return None

# Función para extraer OrdenHis del texto XML
def extract_ordenhis_from_text(text):
    try:
        root = ET.fromstring(text)
        return limpiar_caracteres_corruptos(root.findtext('.//OrdenHis', default=None))
    except ET.ParseError as e:
        print(f"Error al parsear XML: {e}")
        return None
    
# Función para calcular la edad a partir de la fecha de nacimiento
def calcular_edad(fecha_nacimiento):
    if isinstance(fecha_nacimiento, str):
        fecha_nacimiento = datetime.strptime(fecha_nacimiento, "%Y-%m-%d").date()
    hoy = datetime.now().date()
    edad = hoy.year - fecha_nacimiento.year - ((hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day))
    return edad

# Función para extraer datos relevantes del XML
def extraer_datos_desde_xml(texto_xml):
    try:
        root = ET.fromstring(texto_xml)
    except ET.ParseError as e:
        print(f"Error al parsear XML: {e}")
        return None

    observaciones = root.findtext('.//Analito/Observaciones', default='No se encontraron observaciones')
    observaciones = limpiar_caracteres_corruptos(observaciones.replace('..br..', '\n').replace('<br>', '\n').replace('br', '\n'))

    datos = {
        "OrdenHis": limpiar_caracteres_corruptos(root.findtext('.//OrdenHis', default='')),
        "Nombre": limpiar_caracteres_corruptos(root.findtext('.//NombreUsuario', default='')),
        "Apellido": limpiar_caracteres_corruptos(root.findtext('.//ApellidoUsuario', default='')),
        "Documento": limpiar_caracteres_corruptos(root.findtext('.//Documento', default='')),
        "CUPS": limpiar_caracteres_corruptos(root.findtext('.//CodigoServicio', default='')),
        "NombreServicio": limpiar_caracteres_corruptos(root.findtext('.//NombreServicio', default='')),
        "Observaciones": observaciones,
        "NombreProfesional": limpiar_caracteres_corruptos(root.findtext('.//NombreProfesional', default='N/A')),
        "IdentificacionProfesional": limpiar_caracteres_corruptos(root.findtext('.//IdentificacionProfesional', default=''))
    }
    return datos

# Función para buscar el registro médico del profesional en la base de datos contabilidadndx
def buscar_registro_medico(nombre_profesional):
    with connections['contabilidadndx'].cursor() as cursor:
        query = """
            SELECT RegMedico 
            FROM usuarios
            WHERE NombreReal = %s
        """
        cursor.execute(query, [nombre_profesional])
        result = cursor.fetchone()

    if result:
        reg_medico = result[0]
        return reg_medico
    else:
        return "N/A"

# Función para obtener la firma del profesional
import tempfile

def obtener_firma_profesional(identificacion_profesional):
    # Conexión a la base de datos y recuperación de datos
    with connections['contabilidadndx'].cursor() as cursor:
        query = """
            SELECT ImgFirma 
            FROM usuarios 
            WHERE Cedula = %s
        """
        cursor.execute(query, [identificacion_profesional])
        result = cursor.fetchone()

    if result and result[0]:
        firma_binaria = result[0]
        
        # Mostrar algunos detalles para depuración
        print(f"Firma recuperada: tamaño de los datos = {len(firma_binaria)} bytes")
        print(f"Primeros 100 bytes de firma_binaria: {firma_binaria[:100]}")

        # Guardar los datos crudos para inspección manual
        try:
            temp_dir = tempfile.gettempdir()
            raw_path = os.path.join(temp_dir, 'firma_recuperada.raw')
            with open(raw_path, 'wb') as f:
                f.write(firma_binaria)
            print(f"Firma guardada en {raw_path} para verificación manual.")
        except Exception as e:
            print(f"Error al guardar los datos de la firma temporalmente: {e}")

        # Intentar encontrar el encabezado del BMP ('BM') y extraer los datos desde allí
        try:
            # Buscar el índice del encabezado 'BM' para BMP
            bmp_header_index = firma_binaria.find(b'BM')
            if bmp_header_index == -1:
                print("Encabezado BMP no encontrado en los datos de la firma")
                return None
            
            # Extraer los datos a partir del encabezado 'BM'
            firma_binaria_bmp = firma_binaria[bmp_header_index:]
            print(f"Datos BMP extraídos a partir del índice {bmp_header_index}")

            # Guardar el BMP extraído para verificarlo manualmente
            bmp_path = os.path.join(temp_dir, 'firma_extraida.bmp')
            with open(bmp_path, 'wb') as bmp_file:
                bmp_file.write(firma_binaria_bmp)
            print(f"Firma extraída guardada como BMP en {bmp_path} para verificación manual.")

            # Intentar abrir la imagen extraída con Pillow para verificar y luego abrir nuevamente
            img_io = io.BytesIO(firma_binaria_bmp)
            try:
                img = PILImage.open(img_io)
                img.verify()  # Verificar que el archivo es una imagen válida
                print(f"Formato de la imagen: {img.format}")
            except Exception as img_open_err:
                print(f"Error al abrir la imagen: {img_open_err}")
                return None

            # Reabrir la imagen ya que 'verify()' invalida el objeto
            img_io.seek(0)
            img = PILImage.open(img_io)

            # Convertir la imagen a PNG si no es compatible con ReportLab
            if img.format not in ['PNG', 'JPEG']:
                img = img.convert('RGB')
                png_path = os.path.join(temp_dir, 'firma_convertida.png')
                img.save(png_path, format="PNG")
                print(f"Firma convertida a PNG y guardada en {png_path}")

                # Devolver la ruta del archivo PNG convertido para que se use en ReportLab
                return png_path
        except Exception as e:
            # Si ocurre un error al abrir la imagen, imprimir el error y retornar None
            print(f"Error al cargar o convertir la imagen de la firma: {e}")
            return None
    else:
        print("Firma no encontrada o es nula")
        return None
    
def registrar_archivo_en_base_de_datos(orden_id, nombre_archivo, ruta_archivo, tipo, user_id=None, regimen=None):
    try:
        # Verificar si ya existe un archivo de tipo 'RESULTADO' para la orden_id
        if tipo == 'RESULTADO':
            with connections['default'].cursor() as cursor:
                query = """
                    SELECT COUNT(*)
                    FROM archivos
                    WHERE Admision_id = %s AND Tipo = %s
                """
                cursor.execute(query, [orden_id, tipo])
                result = cursor.fetchone()

            # Si ya existe un archivo de tipo 'RESULTADO', no registrar nuevamente
            if result and result[0] > 0:
                print(f"El archivo de tipo '{tipo}' ya existe para la orden {orden_id}. No se guardará un nuevo archivo.")
                return  # Retornar inmediatamente para evitar el guardado

        # Ruta relativa para guardar en la base de datos
        ruta_relativa = os.path.join('gdocumental', 'archivosFacturacion', str(orden_id), nombre_archivo)

        # Obtener la fecha de creación actual y la fecha de creación de la admisión
        fecha_creacion_archivo = datetime.now().replace(second=0, microsecond=0)

        try:
            with connections['datosipsndx'].cursor() as cursor:
                cursor.execute("SELECT FechaCreado FROM admisiones WHERE Consecutivo = %s", [orden_id])
                result = cursor.fetchone()
                if result:
                    fecha_creacion_antares = result[0]
                else:
                    print(f"No se encontró la admisión con Consecutivo = {orden_id}")
                    return
        except Exception as e:
            print(f"Error al obtener la fecha de creación de Antares: {e}")
            return

        # Crear instancia del modelo ArchivoFacturacion
        archivo_facturacion = ArchivoFacturacion(
            Admision_id=orden_id,
            Tipo=tipo,
            NombreArchivo=nombre_archivo,
            RutaArchivo=ruta_relativa,
            NumeroAdmision=orden_id,
            FechaCreacionArchivo=fecha_creacion_archivo,
            FechaCreacionAntares=fecha_creacion_antares,
            Usuario_id=user_id,
            Regimen=regimen,
            RevisionPrimera=False,  # Valor predeterminado
            RevisionSegunda=False,  # Valor predeterminado
            RevisionTercera=False,  # Valor predeterminado
            Radicado=False,  # Valor predeterminado
            Modificado1=None,
            Modificado2=None,
            Modificado3=None,
            IdRevisor=None,
            IdRevisorTesoreria=None,
            FechaRevisionPrimera=None
        )

        # Guardar la instancia en la base de datos
        archivo_facturacion.save()
        print(f"Archivo '{nombre_archivo}' registrado exitosamente.")

    except Exception as e:
        print(f"Error al registrar el archivo en la base de datos: {e}")


# Función para generar el PDF de la orden
def generar_pdf_orden(orden_datos, admision_datos, paciente_datos, medico_datos, carpeta_destino, firma_profesional=None):
    try:

        pdf_filename = f"{orden_datos['OrdenHis']}R.pdf"
        ruta_archivo_pdf_final = os.path.join(carpeta_destino, pdf_filename)

        # Verificar si el archivo ya existe
        if os.path.exists(ruta_archivo_pdf_final):
            print(f"El archivo PDF '{ruta_archivo_pdf_final}' ya existe. No se generará un nuevo archivo.")
            return ruta_archivo_pdf_final
        # Crear un archivo temporal
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_pdf_file:
            ruta_temporal = temp_pdf_file.name

        # Inicializar el documento PDF para guardarlo en la ruta temporal
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=10)
        story = []  # Lista de elementos del PDF
        styles = getSampleStyleSheet()

        # Crear estilos de texto
        styles.add(ParagraphStyle(name='Arial', fontName='Arial', fontSize=8))
        bold_header_style = ParagraphStyle(name='BoldHeaderStyle', fontName='Arial_Bold', fontSize=9, alignment=0)
        observations_style = ParagraphStyle(name='Observations', fontName='Arial', fontSize=10, leading=14, leftIndent=0, spaceAfter=6)
        note_style = ParagraphStyle(name='NoteStyle', fontName='Arial', fontSize=7, leading=8)

        # Añadir el logo al encabezado si existe
        logo_path = os.path.join(settings.BASE_DIR, 'media', 'logo.png')
        if os.path.exists(logo_path):
            logo = Image(logo_path, width=1.0 * inch, height=0.75 * inch)
            logo.hAlign = 'LEFT'
            story.append(logo)

        # Añadir encabezado
        header_data = [
            [Paragraph("<b>Diagnostico RIS</b>", ParagraphStyle(name='Header', fontName='Arial_Bold', fontSize=8, alignment=1))]
        ]
        header_table = Table(header_data, colWidths=[1.5 * inch, 5.5 * inch])
        header_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        story.append(header_table)
        story.append(Spacer(1, 12))

        # Calcular la edad del paciente
        edad = calcular_edad(paciente_datos['FechaNacimiento'])

        # Crear tabla con datos del paciente
        paciente_data = [
            [Paragraph(f"<b>Nombre Paciente:</b> {orden_datos['Nombre']} - {orden_datos['Apellido']}", bold_header_style), '', Paragraph(f"<b>Fecha Nacimiento:</b>", bold_header_style)],
            [Paragraph(f"<b>ID Paciente:</b> CC {orden_datos['Documento']}", bold_header_style), '', Paragraph(f"{paciente_datos['FechaNacimiento']} / {edad} Años", bold_header_style)],
            [Paragraph(f"<b>Contrato:</b> {admision_datos['CodigoEntidad']}", bold_header_style), Paragraph(f"<b>Procedencia:</b> Ambulatorio", bold_header_style), ''],
            [Paragraph(f"<b>Procedimientos:</b> {orden_datos['CUPS']} - {orden_datos['NombreServicio']}", bold_header_style), '', Paragraph(f"<b>Fecha Cita:</b> {admision_datos['FechaCreado'].date()}", bold_header_style)]
        ]

        # Definir el tamaño de las columnas
        table = Table(paciente_data, colWidths=[2.5 * inch, 2.5 * inch, 2.5 * inch])
        table.setStyle(TableStyle([
            ('SPAN', (0, 0), (1, 0)),  # Combinar las celdas del nombre del paciente
            ('SPAN', (0, 3), (1, 3)),  # Combinar las celdas del procedimiento
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),  # Línea arriba de la primera fila
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),  # Línea abajo de la última fila
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),  # Alinear a la izquierda
            ('FONTNAME', (0, 0), (-1, -1), 'Arial'),  # Fuente Arial
            ('FONTSIZE', (0, 0), (-1, -1), 8),  # Tamaño de fuente 8
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),  # Espaciado inferior
            ('TOPPADDING', (0, 0), (-1, -1), 2),  # Espaciado superior
        ]))

        story.append(table)
        story.append(Spacer(1, 6))

        # Añadir las observaciones al documento
        for line in orden_datos['Observaciones'].split('\n'):
            if line.strip():
                story.append(Paragraph(line, observations_style))
                story.append(Spacer(1, 4))
        nota_texto =  """<b>NOTA:</b> En la realización del estudio se adoptan los protocolos y guías de atención establecidos para la prevención del <b>SARS-COV 2/COVID 19</b> que incluye lavado de manos según las recomendaciones de la OMS; además de la utilización de equipo de protección personal y las medidas de protección del paciente; así como limpieza y desinfección de los equipos después de la atención que cada usuario."""
        story.append(Spacer(1, 12))
        story.append(Paragraph(nota_texto, note_style))
        # Añadir una nota al documento
      

        identificacion_profesional = orden_datos.get('IdentificacionProfesional')
        print(f"Identificación del profesional utilizada en la consulta: {identificacion_profesional}")
        ruta_firma = obtener_firma_profesional(identificacion_profesional)

    # Si la firma existe, agregarla
        if ruta_firma and isinstance(ruta_firma, str):
            try:
                firma = Image(ruta_firma, width=2.0 * inch, height=0.6 * inch)  
                firma.hAlign = 'LEFT'
                story.append(firma)
                story.append(Spacer(1, 6))
                print("Firma añadida al PDF con éxito.")
            except Exception as e:
             print(f"Error al agregar la firma al PDF: {e}")
        else:
         print("No se pudo obtener la firma para el profesional")




        # Añadir el nombre del médico y su registro
        medico_nombre = medico_datos.get('NombreProfesional', 'N/A')
        reg_medico = buscar_registro_medico(medico_nombre)
        story.append(Paragraph(f"<b>Realizado por:</b> {medico_nombre}", bold_header_style))
        story.append(Paragraph("Médico Radiólogo", bold_header_style))
        story.append(Paragraph(f"<b>Registro Médico:</b> {reg_medico}", bold_header_style))
        story.append(Paragraph("Dosis de radiación: 15mGy", bold_header_style))

        # Añadir la firma del profesional si está disponible
        if firma_profesional and os.path.exists(firma_profesional):
            story.append(Spacer(1, 12))
            firma = Image(firma_profesional, width=2.0 * inch, height=1.0 * inch)
            firma.hAlign = 'LEFT'
            story.append(firma)

        # Añadir una nota adicional después del nombre del médico
        nota_texto_recuerde = """<b>RECUERDE:</b> que los exámenes de imagenología son un apoyo diagnóstico, y su importancia radica en que deben ser analizados e interpretados por su médico tratante, teniendo en cuenta su cuadro clínico. Si hay una discrepancia entre su impresión clínica y nuestro informe, por favor póngase en contacto con nosotros."""
        story.append(Spacer(1, 12))
        story.append(Paragraph(nota_texto_recuerde, note_style))
        story.append(Spacer(1, 12))  # Añadir espacio antes de la línea
         # Crear una línea usando Drawing
        line = Drawing(500, 1)
        line.add(Line(0, 0, 530, 0))  # Crear línea horizontal con largo 500 puntos
        story.append(line)  # Añadir la línea al final del documento
        story.append(line)

        # Construir el PDF en el buffer
        doc.build(story)

        # Guardar el contenido del buffer en el archivo temporal
        with open(ruta_temporal, 'wb') as f:
            f.write(buffer.getvalue())

        # Definir la ruta final dentro de la carpeta de `orden_id`
        pdf_filename = f"{orden_datos['OrdenHis']}R.pdf"
        ruta_archivo_pdf_final = os.path.join(carpeta_destino, pdf_filename)

        # Crear la carpeta de destino si no existe
        os.makedirs(carpeta_destino, exist_ok=True)

        # Mover el archivo temporal al destino final
        shutil.move(ruta_temporal, ruta_archivo_pdf_final)
        print(f"Archivo PDF generado y movido a {ruta_archivo_pdf_final}")

    except Exception as e:
        print(f"Error al generar el PDF: {e}")

    # Devolver la ruta final del archivo PDF para ser utilizada después
    return ruta_archivo_pdf_final


# Función principal de la vista que genera y registra el PDF
def generar_pdf_orden_view(request, orden_id):
    # Verificar si ya existe un archivo de tipo RESULTADO
    with connections['default'].cursor() as cursor:
        query = """
            SELECT COUNT(*) 
            FROM archivos
            WHERE Admision_id = %s AND Tipo = 'RESULTADO'
        """
        cursor.execute(query, [orden_id])
        result = cursor.fetchone()

    if result and result[0] > 0:
        print(f"El archivo de tipo RESULTADO ya existe para la orden {orden_id}. No se guardará un nuevo archivo.")
        return JsonResponse({"message": "El archivo de tipo RESULTADO ya existe y no se guardó un nuevo archivo."}, status=200)

    # Buscar y procesar la orden si no existe un archivo previo
    orden_texto = buscar_orden_por_ordenhis(orden_id)
    if not orden_texto:
        return JsonResponse({"error": "Orden no encontrada o la dirección no es 'E'"}, status=404)

    datos_orden = extraer_datos_desde_xml(orden_texto)
    if datos_orden is None:
        return JsonResponse({"error": "Error al procesar el XML"}, status=500)

    admision_datos = buscar_admision_por_ordenhis(orden_id)
    if admision_datos is None:
        return JsonResponse({"error": "Datos de admisión no encontrados"}, status=404)

    paciente_datos = buscar_paciente_por_documento(datos_orden['Documento'])
    if paciente_datos is None:
        return JsonResponse({"error": "Datos del paciente no encontrados"}, status=404)

    medico_datos = {
        "NombreProfesional": datos_orden.get('NombreProfesional', 'N/A')
    }

    # Crear el directorio para guardar el archivo PDF si no existe
    folder_path = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'archivosFacturacion', str(orden_id))
    os.makedirs(folder_path, exist_ok=True)
    os.chmod(folder_path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    # Definir la ruta donde se guardará el archivo PDF dentro de la carpeta del orden_id
    pdf_filename = f"{orden_id}R.pdf"
    ruta_archivo_pdf = os.path.join(folder_path, pdf_filename)

    # Generar el PDF y guardar en la ruta especificada dentro de la carpeta
    ruta_archivo_pdf_final = generar_pdf_orden(datos_orden, admision_datos, paciente_datos, medico_datos, ruta_archivo_pdf)

    # Registrar el archivo en la base de datos con el tipo 'RESULTADO'
    registrar_archivo_en_base_de_datos(orden_id, pdf_filename, ruta_archivo_pdf_final, 'RESULTADO')

    # Devolver el PDF en la respuesta HTTP
    with open(ruta_archivo_pdf_final, 'rb') as pdf_file:
        response = HttpResponse(pdf_file.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{pdf_filename}"'

    return response

from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
import os
from django.core.management.base import BaseCommand

# Clase para paginar los resultados de la consulta
class LargeDataPagination(PageNumberPagination):
    page_size = 100  # Número de registros por página
    page_size_query_param = 'page_size'
    max_page_size = 1000

# Clase para la generación automática de PDFs


class Command(BaseCommand):
    help = 'Generar PDFs automáticamente desde la fecha de inicio hasta la fecha actual'

    def add_arguments(self, parser):
        # Ahora solo se recibe una fecha de inicio como argumento
        parser.add_argument(
            'fecha_inicio',
            type=str,
            nargs='?',
            default=None,  # Si no se especifica, será None
            help='Fecha de inicio en formato AAAA-MM-DD. Si no se especifica, se toma la fecha actual.'
        )

    def handle(self, *args, **kwargs):
        fecha_inicio_str = kwargs['fecha_inicio']

        # Si no se proporciona una fecha de inicio, usar la fecha actual
        if not fecha_inicio_str:
            fecha_inicio_str = datetime.now().strftime('%Y-%m-%d')  # Fecha de hoy en formato AAAA-MM-DD

        try:
            fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
        except ValueError:
            self.stdout.write(self.style.ERROR("Formato de fecha inválido. El formato correcto es AAAA-MM-DD."))
            return

        # La fecha de fin siempre es la fecha actual
        fecha_fin = datetime.now()

        paginator = LargeDataPagination()

        try:
            with connections['datosipsndx'].cursor() as cursor:
                query = '''
                    SELECT 
                        Id, texto, direccion, FechaCreado, IpOrigen, Procesado, RtaLumier
                    FROM tblxmlprocesados
                    WHERE FechaCreado BETWEEN %s AND %s
                    AND Direccion = 'E'
                '''
                cursor.execute(query, [fecha_inicio, fecha_fin])
                rows = cursor.fetchall()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error al ejecutar la consulta SQL: {e}"))
            return

        # Procesar cada registro obtenido
        data = []
        for row in rows:
            try:
                record = {
                    'Id': row[0],
                    'Texto': limpiar_caracteres_corruptos(row[1]),  # Limpieza del texto
                    'Direccion': row[2],
                    'FechaCreado': row[3],
                    'IpOrigen': row[4],
                    'Procesado': row[5],
                    'RtaLumier': row[6]
                }
                data.append(record)

                orden_id = extract_ordenhis_from_text(record['Texto'])
                if not orden_id:
                    self.stdout.write(self.style.WARNING(f"No se pudo extraer el OrdenHis para el registro {record['Id']}"))
                    continue

                orden_texto = record['Texto']
                datos_orden = extraer_datos_desde_xml(orden_texto)
                if datos_orden is None:
                    self.stdout.write(self.style.WARNING(f"Error al extraer datos de la orden {orden_id}"))
                    continue

                # Obtener datos de admisión
                admision_datos = buscar_admision_por_ordenhis(orden_id)
                if admision_datos is None:
                    self.stdout.write(self.style.WARNING(f"Datos de admisión no encontrados para la orden {orden_id}"))
                    continue

                # Obtener datos del paciente
                paciente_datos = buscar_paciente_por_documento(datos_orden['Documento'])
                if paciente_datos is None:
                    self.stdout.write(self.style.WARNING(f"Datos del paciente no encontrados para la orden {orden_id}"))
                    continue

                # Datos del médico
                medico_datos = {"NombreProfesional": datos_orden.get('NombreProfesional', 'N/A')}

                # Crear el directorio para guardar el archivo PDF
                output_dir = os.path.join(settings.MEDIA_ROOT, 'gdocumental', 'archivosFacturacion', str(orden_id))
                os.makedirs(output_dir, exist_ok=True)

                # Generar el PDF
                self.stdout.write(f"Generando el PDF para la orden {orden_id}")
                generar_pdf_orden(datos_orden, admision_datos, paciente_datos, medico_datos, output_dir)

                # Registrar el archivo en la base de datos
                self.stdout.write(f"Registrando el archivo PDF para la orden {orden_id}")
                pdf_filename = f"{orden_id}R.pdf"
                ruta_archivo_pdf = os.path.join(output_dir, pdf_filename)
                registrar_archivo_en_base_de_datos(orden_id, pdf_filename, ruta_archivo_pdf, 'RESULTADO')

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error procesando la orden {record['Id']}: {e}"))
                continue

       
        self.stdout.write(self.style.SUCCESS('Proceso completado con éxito.'))
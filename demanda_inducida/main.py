import os
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pyodbc
import sqlite3
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Configuración de base de datos SQL Server (Zeus)
DB_SERVER = os.getenv("DB_SERVER", "localhost")
DB_NAME = os.getenv("DB_NAME", "ZeusSalud_Neuro")
DB_USER = os.getenv("DB_USER", "sa")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Configuración de correo y web
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.office365.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "correo@tudominio.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_TO = os.getenv("EMAIL_TO", "destinatario@tudominio.com")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:5000")

# Ruta de la base de datos local SQLite para históricos
if os.path.exists('/app/data'):
    DATA_DIR = '/app/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_LOCAL = os.path.join(DATA_DIR, 'historico.db')

def init_local_db():
    """Inicializa la base de datos local SQLite para el histórico."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(DB_LOCAL, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ordenes_historico_v2 (
                id_zeus TEXT PRIMARY KEY,
                fecha_registro TEXT,
                medico TEXT,
                paciente TEXT,
                documento TEXT,
                telefono TEXT,
                direccion TEXT,
                entidad TEXT,
                contrato TEXT,
                cups TEXT,
                orden TEXT,
                estado TEXT,
                fecha_procesado TEXT
            )
        ''')
        try:
            cursor.execute("ALTER TABLE ordenes_historico_v2 ADD COLUMN observacion TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass # Si ya existe, ignoramos

        for col in ["val_eps_estado", "val_eps_autorizacion", "val_eps_cups", "val_eps_vigencia", "val_eps_fecha", "val_eps_soporte", "val_eps_mensaje", "observacion_gestion", "fecha_observacion", "nro_admision", "numero_orden"]:
            try:
                cursor.execute(f"ALTER TABLE ordenes_historico_v2 ADD COLUMN {col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
        conn.commit()

def save_to_local_history(data, estado):
    """Guarda o actualiza el registro en la base de datos local."""
    with sqlite3.connect(DB_LOCAL, timeout=10) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO ordenes_historico_v2
            (id_zeus, fecha_registro, medico, paciente, documento, telefono, direccion, entidad, contrato, cups, orden, observacion, estado, fecha_procesado, nro_admision, numero_orden)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), ?, ?)
        ''', (
            data['Id'], data['FechaRegistro'], data['Medico'], data['NombresPaciente'],
            data['NumeroDocumento'], data['TelefonosContacto'], data['DireccionResidencia'],
            data['Entidad'], data['Contrato'], data['Cups'], data['DetalleOrden'], data.get('Observacion', ''), estado,
            data.get('NroAdmision', ''), data.get('NumeroOrden', '')
        ))
        conn.commit()

def get_db_connection():
    """Establece conexión SOLO-LECTURA con SQL Server."""
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        f"ApplicationIntent=ReadOnly"
    )
    conn = pyodbc.connect(conn_str, readonly=True)
    return conn

def safe_execute(cursor, query, params=None):
    """Ejecuta una consulta SOLO si es SELECT. Bloquea cualquier escritura."""
    clean = query.strip().upper()
    blocked = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'EXEC', 'EXECUTE', 'MERGE']
    for keyword in blocked:
        if clean.startswith(keyword):
            raise PermissionError(f"⛔ BLOQUEADO: No se permiten consultas de tipo '{keyword}'. Este sistema es SOLO LECTURA.")
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    return cursor

def send_email(paciente_data):
    """Envía el correo de notificación."""
    msg = MIMEMultipart("alternative")
    msg['Subject'] = f"Nueva Orden de Demanda Inducida: {paciente_data['DetalleOrden']}"
    msg['From'] = SMTP_USER
    msg['To'] = EMAIL_TO

    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; color: #333; }}
            .container {{ width: 80%; margin: auto; border: 1px solid #ddd; padding: 20px; border-radius: 5px; }}
            .header {{ background-color: #0056b3; color: white; padding: 10px; text-align: center; border-radius: 5px 5px 0 0; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ padding: 10px; border-bottom: 1px solid #ddd; text-align: left; }}
            th {{ background-color: #f2f2f2; width: 30%; }}
            .btn {{ display: inline-block; padding: 10px 20px; margin-top: 20px; background-color: #28a745; color: #FFFFFF; text-decoration: none; border-radius: 5px; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>Alerta de Demanda Inducida</h2>
            </div>
            <p>Se ha generado una nueva orden que requiere gestión:</p>
            <table>
                <tr><th>Médico</th><td>{paciente_data['Medico']}</td></tr>
                <tr><th>Paciente</th><td>{paciente_data['NombresPaciente']}</td></tr>
                <tr><th>Documento</th><td>{paciente_data['NumeroDocumento']}</td></tr>
                <tr><th>Teléfonos</th><td>{paciente_data['TelefonosContacto']}</td></tr>
                <tr><th>Dirección</th><td>{paciente_data['DireccionResidencia']}</td></tr>
                <tr><th>Entidad (EPS)</th><td>{paciente_data['Entidad']}</td></tr>
                <tr><th>Contrato</th><td>{paciente_data['Contrato']}</td></tr>
                <tr><th>Servicio Solicitado</th><td><strong>{paciente_data['DetalleOrden']}</strong> (CUPS: {paciente_data['Cups']})</td></tr>
            </table>
            
            <div style="text-align: center;">
                <a href="{DASHBOARD_URL}" class="btn">Ver Listado en el Dashboard</a>
            </div>
            <br>
            <p><em>Notificación generada automáticamente.</em></p>
        </div>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(html_content, 'html'))
    
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

def process_queue():
    try:
        conn_sql = get_db_connection()
        cursor_sql = conn_sql.cursor()
        
        # Conexión local a SQLite para verificar duplicados
        conn_sqlite = sqlite3.connect(DB_LOCAL)
        cursor_sqlite = conn_sqlite.cursor()
        
        # Consultar DIRECTAMENTE las tablas nativas de ZeusSalud (Sin Triggers ni Tablas extra)
        # Miramos solo los últimos 3 días para no sobrecargar el servidor
        query = """
            SELECT 
                CAST(o.numero AS VARCHAR) + '-' + CAST(i.codigo AS VARCHAR) AS IdUnico,
                o.numero AS NumeroOrden,
                CONVERT(varchar, o.fecha, 23) + ' ' + ISNULL(o.hora, '00:00') AS FechaRegistro,
                ISNULL(p.primer_nom, '') + ' ' + ISNULL(p.segundo_nom, '') + ' ' + ISNULL(p.primer_ape, '') + ' ' + ISNULL(p.segundo_ape, '') AS NombresPaciente,
                p.num_id AS NumeroDocumento,
                ISNULL(p.telefono, 'No registra') AS TelefonosContacto,
                ISNULL(p.direccion, 'No registra') AS DireccionResidencia,
                e.nombre AS Entidad,
                c.nombre AS Contrato,
                i.codigo AS Cups,
                i.detalle AS DetalleOrden,
                ISNULL(i.ObservacionItem, '') AS ObservacionItem,
                ISNULL(med.nombre, 'MÉDICO NO ASIGNADO') AS Medico,
                m.con_estudio AS NroAdmision
            FROM ordenes_detalle i
            JOIN ordenes o ON i.numero_orden = o.numero
            JOIN sis_maes m ON o.ingreso = m.con_estudio
            JOIN sis_paci p ON m.autoid = p.autoid
            JOIN contratos c ON m.contrato = c.codigo
            JOIN sis_empre e ON c.empresa = e.codigo
            LEFT JOIN sis_medi med ON o.cod_med = med.codigo
            WHERE 
                c.codigo NOT IN (5, 6)
                AND 
                -- Validar directamente contra la tabla de servicios parametrizados del contrato (Ambulatorio = 1)
                EXISTS (
                    SELECT 1 
                    FROM servicios s 
                    WHERE s.contrato = c.codigo 
                      AND LTRIM(RTRIM(s.cod_proc)) = LTRIM(RTRIM(i.codigo))
                      AND s.A = 1
                )
                AND o.fecha >= DATEADD(day, -30, GETDATE())
        """
        safe_execute(cursor_sql, query)
        rows = cursor_sql.fetchall()
        
        for row in rows:
            # El ID será una combinación del número de orden y el CUPS para que sea único
            record_id = row.IdUnico
            
                        # Verificamos si ya procesamos esta orden buscando en el histórico local de SQLite
            cursor_sqlite.execute("SELECT observacion FROM ordenes_historico_v2 WHERE id_zeus = ?", (record_id,))
            existing_row = cursor_sqlite.fetchone()
            if existing_row:
                # Actualizar observación si antes no la tenía
                if (not existing_row[0] or str(existing_row[0]).strip() == '') and row.ObservacionItem and str(row.ObservacionItem).strip() != '':
                    cursor_sqlite.execute("UPDATE ordenes_historico_v2 SET observacion = ? WHERE id_zeus = ?", (row.ObservacionItem, record_id))
                    conn_sqlite.commit()
                continue # Ya la notificamos antes, pasamos a la siguiente
            
            paciente_data = {
                'Id': record_id,
                'FechaRegistro': row.FechaRegistro,
                'Medico': row.Medico.strip(),
                'NombresPaciente': row.NombresPaciente.strip(),
                'NumeroDocumento': row.NumeroDocumento,
                'TelefonosContacto': row.TelefonosContacto,
                'DireccionResidencia': row.DireccionResidencia,
                'Entidad': row.Entidad,
                'Contrato': row.Contrato,
                'Cups': row.Cups,
                'DetalleOrden': row.DetalleOrden,
                'Observacion': row.ObservacionItem,
                'NroAdmision': str(row.NroAdmision) if row.NroAdmision else '',
                'NumeroOrden': str(row.NumeroOrden) if row.NumeroOrden else '',
            }
            
            try:
                print(f"Nueva Orden Detectada! ID {row.NumeroOrden} para {paciente_data['NombresPaciente']}...")
                try:
                    send_email(paciente_data)
                    estado_correo = 'NOTIFICADO'
                except Exception as mail_e:
                    print(f"Error de correo para {row.NumeroOrden}: {mail_e}")
                    estado_correo = 'ERROR_CORREO'
                
                # Guardar en base de datos historica SIEMPRE para que avance y no se quede en un bucle infinito
                save_to_local_history(paciente_data, estado_correo)
                print(f"Orden {row.NumeroOrden} guardada en SQLite con estado {estado_correo}.")
                
            except Exception as e:
                print(f"Error general guardando la orden {row.NumeroOrden}: {e}")
                
    except Exception as e:
        print(f"Error general consultando a Zeus: {e}")
    finally:
        if 'conn_sql' in locals():
            conn_sql.close()
        if 'conn_sqlite' in locals():
            conn_sqlite.close()

if __name__ == "__main__":
    print("Inicializando base de datos histórica local...")
    init_local_db()
    print("Iniciando servicio de notificaciones Demanda Inducida (Modo Solo-Lectura)...")
    while True:
        process_queue()
        time.sleep(60)

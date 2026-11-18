from django.db import models
from django.conf import settings


class CasoGestion(models.Model):
    BANDEJA_CHOICES = [
        ('realizados',  'Realizados sin factura'),
        ('radicados',   'Facturados sin radicar'),
        ('devoluciones','Devoluciones EPS'),
    ]
    ESTADO_CHOICES = [
        ('pendiente',   'Pendiente'),
        ('en_gestion',  'En gestión'),
        ('resuelto',    'Resuelto'),
    ]

    admision_id          = models.IntegerField(db_index=True)
    bandeja              = models.CharField(max_length=20, choices=BANDEJA_CHOICES, db_index=True)
    estado               = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    responsable          = models.ForeignKey(
                               settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='casos_gestion'
                           )
    fecha_ingreso_bandeja = models.DateTimeField(auto_now_add=True)
    fecha_resolucion      = models.DateTimeField(null=True, blank=True)
    usuario_resolvio      = models.ForeignKey(
                               settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='casos_resueltos'
                           )
    numero_radicado       = models.CharField(max_length=100, blank=True, default='')
    fecha_radicado        = models.DateField(null=True, blank=True)
    usuario_radico        = models.ForeignKey(
                               settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='casos_radicados'
                           )
    motivo_devolucion     = models.TextField(blank=True, default='')
    observacion           = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'gestion_caso'
        unique_together = [('admision_id', 'bandeja')]
        ordering = ['-fecha_ingreso_bandeja']

    def __str__(self):
        return f"{self.bandeja} · {self.admision_id} · {self.estado}"


class DetalleFactura(models.Model):
    RegistroNo = models.IntegerField()
    AdmisionNo =  models.IntegerField(primary_key=True)
    FechaServicio = models.DateField()
    IDServicio= models.CharField(max_length=255)
    CodigoCUPS= models.CharField(max_length=255)
    CodigoSOAT= models.CharField(max_length=255)
    CodigoISS= models.CharField(max_length=255) 
    Cantidad= models.CharField(max_length=255)
    ValorUnitario = models.DecimalField(max_digits=12, decimal_places=2)
    FacturaNo= models.CharField(max_length=255)
    RegistroGlosa = models.IntegerField()
    IdEspecialista = models.IntegerField()
    CreadoPor = models.IntegerField()
    ModificadoPor = models.IntegerField()
    FechaCreado =   models.DateField()
    FechaModificado= models.DateField()
    VrUnitarioCompartido = models.IntegerField()
    VrPorCopago= models.IntegerField()
    VrPorCuota = models.IntegerField()
    OrdenNo = models.IntegerField()
    Ccosto= models.IntegerField()
     
    class Meta:
        
        db_table = 'detallefactura'
        managed = False
        


class PxCita(models.Model):
    RegistroNo = models.IntegerField()
    IdCita =  models.IntegerField(primary_key=True)
    FechaCreado = models.DateField()
    CUPS= models.CharField(max_length=255)
    Cantidad = models.IntegerField()
    VrUnitario = models.IntegerField()
    IdServicio= models.CharField(max_length=255) 
    Facturado= models.IntegerField()
    IdPaquete= models.IntegerField()
    
     
    class Meta:
        
        db_table = 'pxcita'
        managed = False




class CUPS(models.Model):
    RegistroNo = models.IntegerField()
    CUPS= models.CharField(max_length=255)
    Servicio = models.IntegerField()
    FechaCreado  = models.DateField()   
     
    class Meta:
        
        db_table = 'cupsxservicio'
        managed = False



class Servicios(models.Model):
    IdServicio = models.CharField(max_length=255)
    NombreServicio= models.CharField(max_length=255)
    CuentaIngresos= models.CharField(max_length=255) 
          
    class Meta:
        
        db_table = 'servicios'
        managed = False




class CodigoSoat(models.Model):
        CodigoCUPS = models.CharField(max_length=255)
        CodigoISS= models.CharField(max_length=255)
        DescripcionCUPS= models.CharField(max_length=255) 
        Descripcion= models.CharField(max_length=255) 
        Tarifa01=models.FloatField() 
        Tarifa02=models.FloatField() 
        Tarifa03=models.FloatField() 
        Tarifa04=models.FloatField() 
        Tarifa05=models.FloatField() 
        Tarifa06=models.FloatField() 
        Tarifa07=models.FloatField() 
        Tarifa08=models.FloatField() 
        Tarifa09=models.FloatField() 
        Tarifa10=models.FloatField() 
        Tarifa11=models.FloatField() 
        Tarifa12=models.FloatField() 
        Tarifa13=models.FloatField() 
        Tarifa14=models.FloatField() 
        Tarifa15=models.FloatField() 
        Tarifa16=models.FloatField() 
        Tarifa17=models.FloatField() 
        Tarifa18=models.FloatField() 
        Tarifa19=models.FloatField() 
        Tarifa20=models.FloatField() 
        Tarifa21=models.FloatField() 
        Tarifa22=models.FloatField() 
        Tarifa23=models.FloatField() 
        Tarifa24=models.FloatField() 
        Tarifa25=models.FloatField() 
        Tarifa26=models.FloatField() 
        Tarifa27=models.FloatField()
        Tarifa28=models.FloatField() 
        Tarifa29=models.FloatField() 
        Tarifa30=models.FloatField() 
        Tarifa31=models.FloatField() 
        Tarifa32=models.FloatField() 
        Tarifa33=models.FloatField() 
        Tarifa34=models.FloatField() 
        Tarifa35=models.FloatField()
        Tarifa36=models.FloatField() 
        Tarifa37=models.FloatField() 
        Tarifa38=models.FloatField() 
        Tarifa39=models.FloatField() 
        Tarifa40=models.FloatField() 
        Tarifa41=models.FloatField()
        
        class Meta:
            db_table = 'codigossoat'
            managed = False

# ── Auditoría de honorarios médicos ──────────────────────────────────────────

class TarifaHonorario(models.Model):
    """Honorario de un médico por CUPS (importado del Excel 'Cups Neuro - Honorarios médicos', editable)."""
    GRUPOS = [('TODOS', 'Todas las entidades'), ('PARTICULAR', 'Particular'),
              ('ENTIDADES', 'Entidades / otras entidades'), ('SANITAS', 'Sanitas (evento y MRC)')]
    TIPOS = [('FIJO', 'Valor fijo'), ('PORCENTAJE', '% del valor facturado'),
             ('POR_PUNTO', 'Valor por punto'), ('ESCALONADO', 'Según número de procedimientos')]

    area          = models.CharField(max_length=60, blank=True, default='')       # hoja del Excel
    encabezado    = models.CharField(max_length=120, blank=True, default='')      # médico como está en el Excel
    cod_medico    = models.IntegerField(null=True, blank=True, db_index=True)     # Medicos.codigo en Zeus
    medico        = models.CharField(max_length=150, blank=True, default='')
    cups          = models.CharField(max_length=20, db_index=True)
    descripcion   = models.CharField(max_length=255, blank=True, default='')
    grupo         = models.CharField(max_length=12, choices=GRUPOS, default='TODOS')
    tipo          = models.CharField(max_length=12, choices=TIPOS, default='FIJO')
    valor         = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    porcentaje    = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    max_puntos    = models.IntegerField(null=True, blank=True)
    escalas       = models.CharField(max_length=200, blank=True, default='')      # "400000,500000,600000"
    por_unidad    = models.BooleanField(default=False)    # multiplica por la cantidad (cada nervio, extremidad...)
    solo_contrato = models.CharField(max_length=60, blank=True, default='')       # ej. IPS CONSULTORIO
    texto_original = models.CharField(max_length=120, blank=True, default='')
    activo        = models.BooleanField(default=True)
    actualizado   = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auditoria_tarifa'
        ordering = ['area', 'medico', 'cups']


class AuditoriaLinea(models.Model):
    """Decisión del auditor sobre una línea de servicio de Zeus (sis_deta.id)."""
    ESTADOS = [('PENDIENTE', 'Pendiente'), ('OK', 'OK'), ('GLOSA', 'Glosa')]

    sis_deta_id    = models.IntegerField(unique=True)
    periodo        = models.CharField(max_length=7, db_index=True)    # YYYY-MM
    cod_medico     = models.IntegerField(null=True, blank=True, db_index=True)
    estado         = models.CharField(max_length=10, choices=ESTADOS, default='PENDIENTE')
    valor_ajustado = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    observacion    = models.TextField(blank=True, default='')
    usuario        = models.CharField(max_length=150, blank=True, default='')
    fecha          = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auditoria_linea'


class AuditoriaCierre(models.Model):
    """Auditoría de un médico en un mes, cerrada (ya no se edita)."""
    periodo    = models.CharField(max_length=7)
    cod_medico = models.IntegerField()
    total      = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    usuario    = models.CharField(max_length=150, blank=True, default='')
    fecha      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auditoria_cierre'
        unique_together = [('periodo', 'cod_medico')]


class LumierEstudio(models.Model):
    """Estudio de imágenes del informe de Lumier (radiólogo que lo leyó)."""
    periodo       = models.CharField(max_length=7, db_index=True)
    fecha         = models.DateField()
    documento     = models.CharField(max_length=30, db_index=True)
    paciente      = models.CharField(max_length=200, blank=True, default='')
    cups          = models.CharField(max_length=20)
    examen        = models.CharField(max_length=255, blank=True, default='')
    cantidad      = models.IntegerField(default=1)
    contrato      = models.CharField(max_length=255, blank=True, default='')
    estado        = models.CharField(max_length=60, blank=True, default='')
    profesional   = models.CharField(max_length=200, blank=True, default='')
    profesional_doc = models.CharField(max_length=30, blank=True, default='')
    cod_medico    = models.IntegerField(null=True, blank=True)
    id_cita       = models.CharField(max_length=40, blank=True, default='')

    class Meta:
        db_table = 'auditoria_lumier'


class ResolucionLumier(models.Model):
    """Validación manual de un hallazgo del cruce Lumier ↔ Zeus."""
    DECISIONES = [('ZEUS', 'Se paga al médico de Zeus'), ('LUMIER', 'Se paga al médico de Lumier'),
                  ('REVISADO', 'Revisado'), ('DESCARTADO', 'Descartado')]

    clave    = models.CharField(max_length=40, unique=True)   # MD/CD/SZ:<sis_deta_id> | SL:<lumier_id>
    periodo  = models.CharField(max_length=7, db_index=True)
    decision = models.CharField(max_length=12, choices=DECISIONES)
    nota     = models.TextField(blank=True, default='')
    usuario  = models.CharField(max_length=150, blank=True, default='')
    fecha    = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'auditoria_lumier_resolucion'

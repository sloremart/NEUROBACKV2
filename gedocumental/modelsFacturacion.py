
from django.contrib.auth.models import AbstractUser
from email.headerregistry import Group
from django.db import models

class Admisiones(models.Model):
    # Mapeado a sis_maes de ZeusSalud_Neuro
    Consecutivo = models.AutoField(primary_key=True, db_column='autoid')
    IDPaciente = models.CharField(max_length=50, db_column='cod_entidad')
    CodigoEntidad = models.CharField(max_length=50, db_column='EPSPaciente', null=True, blank=True)
    FacturaNo = models.IntegerField(db_column='nro_factura', null=True, blank=True)
    tRegimen = models.CharField(max_length=10, db_column='tipoUsuario', null=True, blank=True)
    FechaCreado = models.DateTimeField(db_column='fecha_ing', null=True, blank=True)
    FechaSalida = models.DateTimeField(db_column='fecha_egr', null=True, blank=True)
    AutorizacionNo = models.CharField(max_length=100, db_column='nro_autoriza', null=True, blank=True)
    Diag1 = models.CharField(max_length=15, db_column='diagno_ing', null=True, blank=True)
    Estado = models.CharField(max_length=5, db_column='estado', null=True, blank=True)
    Contrato = models.BigIntegerField(db_column='contrato', null=True, blank=True)
    NombreResponsable = models.CharField(max_length=50, db_column='acompanante', null=True, blank=True)

    class Meta:
        db_table = 'sis_maes'
        managed = False
        






class Factura(models.Model):
    AdmisionNo = models.IntegerField(primary_key=True)
    FacturaNo =  models.IntegerField()
    Fecha = models.DateField()
    Plan= models.TextField()
    TotalServicio= models.DecimalField(max_digits=12, decimal_places=2)
    TotalTerceros= models.DecimalField(max_digits=12, decimal_places=2)
    TotalFactura= models.DecimalField(max_digits=12, decimal_places=2)
    VrAbono= models.IntegerField()
    VrDescuento = models.IntegerField()
    VrAbonado= models.IntegerField()
    FechaAdmision = models.DateField()
    EnviadoEntidad = models.SmallIntegerField()
    FechaEnvio = models.DateField()
    RemisionNo = models.CharField(max_length=15)
    VrOtros =  models.IntegerField()
    Contabilizada  = models.SmallIntegerField()
    Revisada  = models.SmallIntegerField()
    FechaRecibo = models.DateField()
    FechaReenvio = models.DateField()
    Ruta= models.CharField(max_length=20)
    ReemplazadaPor = models.IntegerField()
    ReemplazadaFactura = models.IntegerField()
    Observaciones  = models.TextField()
    FechaDevolucion = models.DateField()
    Devuelta= models.SmallIntegerField()
    TarifarioFactura= models.SmallIntegerField()
    VrGlosa= models.DecimalField(max_digits=12, decimal_places=2)
    TipoGlosa = models.SmallIntegerField()
    MotivoGlosa= models.TextField()
    Prefijo = models.CharField(max_length=15)
    VrCapitacion = models.IntegerField()
    FechaCreado= models.DateField()
    FechaModificado = models.DateField()
    FechaGlosa = models.DateField()
    FechaRespuesta = models.DateField()
    FechaReciboGlosa= models.DateField()
    FechaElaboracionGlosa= models.DateField()
    VrIVA = models.IntegerField()
    VrAceptado= models.DecimalField(max_digits=12, decimal_places=2)
    VrRecibidoAdmision= models.IntegerField()
    Etimer= models.IntegerField()
    IncluirCuentaCobro = models.SmallIntegerField()
    Impresa= models.SmallIntegerField()
    Modificadapor= models.IntegerField()
    VrLevantadoGlosa= models.DecimalField(max_digits=12, decimal_places=2)
    FechaLevante = models.DateField()
    IDPaciente= models.CharField(max_length=15)
    FacturaCC = models.IntegerField()
    VrCopago= models.IntegerField()
    VrCuotaModeradora= models.IntegerField()
    FacturaAnulada= models.IntegerField()
    EstadoContGlosa= models.IntegerField()
    CreoGlosa= models.IntegerField()
    ContestoGlosa = models.IntegerField()
    DetalleFact= models.TextField()
    TipoDoc1= models.TextField()
    TipoDoc2= models.TextField()
    TipoDoc3= models.TextField()
    TipoDoc4= models.TextField()
    TipoDoc5= models.TextField()
    TipoDoc6= models.TextField()
    VrRatificado= models.DecimalField(max_digits=12, decimal_places=2)
    VrAceptadoConc= models.DecimalField(max_digits=12, decimal_places=2)
    VrSoportadoEntidad = models.DecimalField(max_digits=12, decimal_places=2)
    FechaRatificado = models.DateField()
    FechaConciliacion = models.DateField()
    rCUFE= models.CharField(max_length=15)
    rHora= models.CharField(max_length=15)
    regResolucion= models.IntegerField()
    rEnviado= models.SmallIntegerField()
    EstadoAuditoria = models.SmallIntegerField()
    FechaAnulada= models.DateField()
    AnuladaPor= models.IntegerField()
    QRCode= models.BinaryField()
    MedioPago= models.SmallIntegerField()
     
    class Meta:
        
        db_table = 'facturas'
        managed = False
        

from django.db import models


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
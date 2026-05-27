from django.db import models


class DetalleFactura(models.Model):
    AdmisionNo = models.CharField(max_length=20)
    FechaServicio = models.DateField()
    CodigoCups = models.IntegerField()
    Cantidad = models.IntegerField()
    ValorUnitario = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        db_table = 'detallefactura'
        managed = False


class Servicio(models.Model):
    IdServicio = models.CharField(primary_key=True, max_length=8)
    NombreServicio = models.CharField(max_length=60)

    class Meta:
        db_table = 'servicios'
        managed = False


class CUPSxServicio(models.Model):
    CUPS = models.CharField(max_length=10)
    Servicio = models.CharField(max_length=6)

    class Meta:
        db_table = 'cupsxservicio'
        managed = False


class Entidades(models.Model):
    IDEntidad = models.CharField(max_length=10)
    NombreEntidad = models.CharField(max_length=6)

    class Meta:
        db_table = 'cupsxservicio'
        managed = False
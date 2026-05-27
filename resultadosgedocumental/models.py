from django.db import models

class ConsolidadoEstudios(models.Model):
    Idcita = models.IntegerField(verbose_name="ID de la Cita", unique=True)
    Admision = models.IntegerField(verbose_name="Número de Admisión")
    FechaCita = models.DateTimeField(verbose_name="Fecha de la Cita")
    IdMedico = models.CharField(max_length=20, verbose_name="ID del Médico")
    NombreMedico = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nombre Medico")
    NumeroPaciente = models.IntegerField(verbose_name="Número de Paciente")
    Cups = models.CharField(max_length=20, verbose_name="Código CUPS")
    Cantidad = models.IntegerField(verbose_name="Cantidad")
    NombreCompleto = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nombre Completo")
    CodigoEntidad = models.CharField(max_length=50, null=True, blank=True, verbose_name="Código de Entidad")
    ResultadoArchivo = models.CharField(max_length=255, null=True, blank=True, verbose_name="Resultado de Archivos")
    DescripcionCups = models.CharField(max_length=255, null=True, blank=True, verbose_name="Descripción CUPS")

    class Meta:
        db_table = "consolidado_estudios"
        managed = True
  


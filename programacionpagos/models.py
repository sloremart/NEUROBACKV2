from django.db import models
from django.conf import settings
from django.utils.timezone import now 

class FacturaProgramacionPagos(models.Model):
    Id = models.AutoField(primary_key=True)
    Cuenta = models.CharField(max_length=16)
    NIT = models.CharField(max_length=20)
    Sucursal = models.IntegerField(null=True, blank=True)
    Documento = models.CharField(max_length=15)
    NombreNit = models.CharField(max_length=255, null=True, blank=True)
    CuentaNombre = models.CharField(max_length=255, null=True, blank=True)
    CuentaNit = models.CharField(max_length=20, null=True, blank=True)
    RevisionFinanciera = models.BooleanField(default=False)
    PagoTesoreria = models.BooleanField(default=False, verbose_name="Pago por tesorería")
    NumeroEgreso = models.IntegerField(null=True, blank=True)
    Fecha = models.DateField()  # ✅ Asegurar que es `DateField`
    FechaComitePago = models.DateField(null=True, blank=True)  # ✅ Permitir valores vacíos
    FechaVence = models.DateField()
    FechaRecibo = models.DateField(null=True, blank=True)
    FechaReenvio = models.DateField(null=True, blank=True)
    FechaPago = models.DateField(null=True, blank=True)
    FechaCreado = models.DateField(auto_now_add=True)  # ✅ Cambiado de `DateTimeField` a `DateField`
    Debito = models.DecimalField(max_digits=19, decimal_places=2, default=0.00)
    Credito = models.DecimalField(max_digits=19, decimal_places=2, default=0.00)
    RetencionCausada = models.IntegerField(null=True, blank=True)
    Paga = models.IntegerField(null=True, blank=True)
    CtaCobro = models.IntegerField(null=True, blank=True)
    Prefijo = models.CharField(max_length=5)
    Registro = models.IntegerField(null=True, blank=True)
    PrioridadAlta = models.BooleanField(default=False, verbose_name="Alta urgencia (Rojo)")
    PrioridadMedia = models.BooleanField(default=False, verbose_name="Mitad de mes (Amarillo)")
    PrioridadBaja = models.BooleanField(default=False, verbose_name="Final de mes (Azul)")
    PrioridadInmediata = models.BooleanField(default=False, verbose_name="Pago inmediato (Verde)")
    UsuarioAsignador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facturas_asignadas"
    )

    class Meta:
        db_table = "facturas_programacion_pagos"
        managed = True

    def __str__(self):
        return f"Factura {self.Documento} - {self.Cuenta} - Prioridad: {self.get_prioridad()}"

    def get_prioridad(self):
        """ Devuelve el nivel de prioridad como string """
        if self.PrioridadInmediata:
            return "Inmediata"
        elif self.PrioridadAlta:
            return "Alta"
        elif self.PrioridadMedia:
            return "Media"
        elif self.PrioridadBaja:
            return "Baja"
        return "Sin asignar"

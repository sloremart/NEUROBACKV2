from rest_framework import serializers

from programacionpagos.models import FacturaProgramacionPagos

class FacturaprogramacionPagoSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacturaProgramacionPagos
        fields = '__all__'  

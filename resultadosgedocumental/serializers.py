from rest_framework import serializers
from .models import ConsolidadoEstudios


        
class ConsolidadoEstudiosSerializer(serializers.ModelSerializer):
    FechaCita = serializers.DateField(format="%Y-%m-%d") 
    class Meta:
        model = ConsolidadoEstudios
        fields = '__all__' 
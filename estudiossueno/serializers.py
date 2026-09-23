from rest_framework import serializers
from .models import RegistroEstudioSueno


class RegistroEstudioSuenoSerializer(serializers.ModelSerializer):
    class Meta:
        model  = RegistroEstudioSueno
        fields = "__all__"
        read_only_fields = ["fecha_registro"]

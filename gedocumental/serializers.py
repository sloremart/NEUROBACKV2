from rest_framework import serializers

from gedocumental.modelsFacturacion import Admisiones
from .models import ArchivoFacturacion, AuditoriaCuentasMedicas, ObservacionSinArchivo, ObservacionesArchivos
from django.contrib.auth import authenticate


class ObservacionesArchivosSerializer(serializers.ModelSerializer):
    class Meta:
        model = ObservacionesArchivos
        fields = '__all__'

class ArchivoFacturacionSerializer(serializers.ModelSerializer):
    Observaciones = ObservacionesArchivosSerializer(many=True, read_only=True)
    FechaRechazo = serializers.DateTimeField(read_only=True)
    Usuario = serializers.IntegerField(source='Usuario.id', read_only=True)
    UsuarioRechazo = serializers.IntegerField(source='UsuarioRechazo.id', read_only=True)
    IdRevisor = serializers.IntegerField(read_only=True)
    IdRevisorTesoreria = serializers.IntegerField(read_only=True)
    UsuarioCuentasMedicas = serializers.IntegerField(source='UsuarioCuentasMedicas.id', read_only=True)
    UsuariosTesoreria = serializers.IntegerField(source='UsuariosTesoreria.id', read_only=True)

    class Meta:
        model = ArchivoFacturacion
        fields = '__all__'

class AdmisionConArchivosSerializer(serializers.ModelSerializer):
    archivos_facturacion = ArchivoFacturacionSerializer(many=True, read_only=True)
    observaciones = ObservacionesArchivosSerializer(many=True, read_only=True)

    class Meta:
        model = ArchivoFacturacion
        fields = ['Admision_id', 'FechaCreacionArchivo', 'archivos_facturacion', 'observaciones']

class RevisionCuentaMedicaSerializer(serializers.ModelSerializer):
    archivos_facturacion = ArchivoFacturacionSerializer(many=True, read_only=True)

    class Meta:
        model = ArchivoFacturacion
        fields = ['Admision_id', 'FechaCreacionArchivo', 'archivos_facturacion', 'RevisionPrimera', 'RevisionSegunda', 'FechaRevisionPrimera', 'FechaRevisionSegunda', 'UsuarioCuentasMedicas', 'UsuariosTesoreria']

class AuditoriaCuentasMedicasSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditoriaCuentasMedicas
        fields = '__all__'


class AdmisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Admisiones
        fields = '__all__'

class ObservacionSinArchivoSerializer(serializers.ModelSerializer):
    Usuario = serializers.IntegerField(source='Usuario.id', read_only=True)
    
    class Meta:
        model = ObservacionSinArchivo
        fields = '__all__'
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import CustomUser


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'nombre', 'email', 'cargo', 'is_staff', 'is_active',
                  'id_usuario_antares', 'usuario_antares')
        extra_kwargs = {
            'username': {'read_only': True},  # se genera automático, no se edita directo
        }


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=4)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = CustomUser
        fields = ('nombre', 'email', 'cargo', 'password', 'password_confirm')

    def validate_email(self, value):
        if CustomUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Ya existe un usuario con este correo.')
        return value.lower()

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError({'password_confirm': 'Las contraseñas no coinciden.'})
        try:
            validate_password(data['password'])
        except ValidationError as e:
            raise serializers.ValidationError({'password': list(e.messages)})
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        email = validated_data['email']
        base_username = email.split('@')[0]
        username = base_username
        counter = 1
        while CustomUser.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        user = CustomUser(
            username=username,
            email=validated_data['email'],
            nombre=validated_data['nombre'],
            cargo=validated_data['cargo'],
        )
        user.set_password(validated_data['password'])
        user.save()
        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ChangePasswordSerializer(serializers.Serializer):
    password_actual = serializers.CharField(write_only=True)
    password_nuevo = serializers.CharField(write_only=True, min_length=8)
    password_nuevo_confirm = serializers.CharField(write_only=True)

    def validate(self, data):
        if data['password_nuevo'] != data['password_nuevo_confirm']:
            raise serializers.ValidationError({'password_nuevo_confirm': 'Las contraseñas nuevas no coinciden.'})
        try:
            validate_password(data['password_nuevo'])
        except ValidationError as e:
            raise serializers.ValidationError({'password_nuevo': list(e.messages)})
        return data


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('nombre', 'cargo', 'username')

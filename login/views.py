from django.shortcuts import render
from rest_framework import generics
from django.db.models import Q
from login.serializer import UserSerializer
from .models import CustomUser


class FacturadorListView(generics.ListAPIView):
    queryset = CustomUser.objects.filter(cargo='Facturador')  
    serializer_class = UserSerializer

class AsistencialListView(generics.ListAPIView):
    queryset = CustomUser.objects.filter(cargo='Asistencial') 
    serializer_class = UserSerializer

class TotalCargosListView(generics.ListAPIView):
    queryset = CustomUser.objects.filter(
        Q(cargo='Asistencial') | 
        Q(cargo='Facturador') 
    
    )
    serializer_class = UserSerializer

class CuentasMedicasListView(generics.ListAPIView):
    queryset = CustomUser.objects.filter(
        Q(cargo='CuentasMedicas')
    
    )
    serializer_class = UserSerializer
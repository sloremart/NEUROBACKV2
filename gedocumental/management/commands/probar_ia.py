"""
Prueba en seco de la revisión IA sobre un PDF o imagen local. No escribe en la BD.

Uso:
  python manage.py probar_ia ruta/al/archivo.pdf --tipo ORDEN --consecutivo 17206
  python manage.py probar_ia ruta/al/archivo.pdf --solo-leer   # sin llamar a OpenAI

Con --consecutivo los datos del paciente y los CUPS se leen de ZeusSalud,
igual que en la subida real.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gedocumental.services.ai_revision import (
    _formatear_contexto, _leer_documento, analizar_documento, obtener_contexto_admision,
)


class Command(BaseCommand):
    help = "Ejecuta la revisión IA sobre un PDF sin guardar nada en la base de datos"

    def add_arguments(self, parser):
        parser.add_argument('pdf', help="Ruta al PDF o imagen (JPG, PNG...)")
        parser.add_argument('--tipo', default='FACTURA', help="Tipo de documento (FACTURA, ORDEN, COMPROBANTE, ...)")
        parser.add_argument('--consecutivo', help="Número de estudio/admisión en ZeusSalud")
        parser.add_argument('--solo-leer', action='store_true', help="Solo lee el PDF y el contexto, no llama a OpenAI")

    def handle(self, *args, **opts):
        ruta = opts['pdf']
        if not os.path.exists(ruta):
            raise CommandError(f"No existe: {ruta}")

        api_key = getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
        self.stdout.write(f"REVISAR_CON_IA = {getattr(settings, 'REVISAR_CON_IA', None)}")
        self.stdout.write(f"OPENAI_API_KEY = {'configurada' if api_key else 'NO configurada'}")

        datos_admision = obtener_contexto_admision(opts['consecutivo']) if opts['consecutivo'] else {}
        self.stdout.write("--- Contexto de la admisión ---\n" + _formatear_contexto(datos_admision))

        texto, imagenes = _leer_documento(ruta, opts["tipo"])
        if imagenes and texto:
            self.stdout.write(f"PDF mixto: {len(texto)} caracteres de texto + {len(imagenes)} página(s) como imagen")
        elif imagenes:
            self.stdout.write(f"Escaneado/imagen: se enviarán {len(imagenes)} página(s) como imagen")
        elif texto:
            self.stdout.write(f"PDF digital: {len(texto)} caracteres de texto")
        else:
            self.stdout.write(self.style.WARNING("Archivo ilegible. En producción la IA lo omitiría."))
            return

        if opts['solo_leer']:
            return
        if not api_key:
            raise CommandError("Falta OPENAI_API_KEY para llamar a la IA")

        resultado = analizar_documento(ruta, opts['tipo'], datos_admision)
        for campo in ("firma_paciente", "paciente_documento", "paciente_coincide", "procedimientos_documento", "procedimientos_coinciden", "estudio_documento", "estudio_admision", "ajuste_procedimiento"):
            self.stdout.write(f"  {campo}: {resultado.get(campo, '-')}")
        estilo = self.style.SUCCESS if resultado['tipo'] == 'OK' else self.style.WARNING
        self.stdout.write(estilo(f"Resultado IA ({resultado['modo']}): {resultado['tipo']} — {resultado.get('descripcion', '')}"))

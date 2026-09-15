"""
Importa archivos de resultado desde /media/disco1/examenes/ a Gestión Documental.

Los archivos deben tener el formato: {numero_admision}R{cualquier_cosa}.pdf
Ejemplos válidos: 12345R.pdf  12345Rinforme.pdf  12345R_2024.pdf

Uso:
    python manage.py importar_resultados               # archivos de hoy
    python manage.py importar_resultados --dias 3      # archivos de los últimos 3 días
    python manage.py importar_resultados --todos       # todos los archivos históricos
    python manage.py importar_resultados --dry-run     # simula sin crear registros
"""

import os
import re
import shutil
import time
from django.core.management.base import BaseCommand
from gedocumental.models import ArchivoFacturacion

CARPETA_EXAMENES_DEFAULT = '/neuro/examenes'
PATRON_ARCHIVO = re.compile(r'^(\d+)[Rr]', re.IGNORECASE)
TIPO = 'RESULTADO'
# Admisiones válidas: entre 1 y 99999 (ajustar si el rango es mayor)
ADMISION_MAX = 99999


class Command(BaseCommand):
    help = 'Importa archivos de resultados de la carpeta examenes a Gestión Documental'

    def add_arguments(self, parser):
        parser.add_argument(
            '--carpeta',
            default=CARPETA_EXAMENES_DEFAULT,
            help=f'Carpeta donde están los archivos (default: {CARPETA_EXAMENES_DEFAULT})',
        )
        parser.add_argument(
            '--dias',
            type=int,
            default=1,
            help='Solo importar archivos modificados en los últimos N días (default: 1 = hoy)',
        )
        parser.add_argument(
            '--todos',
            action='store_true',
            help='Importar todos los archivos sin filtro de fecha',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Muestra qué se importaría sin crear registros',
        )

    def handle(self, *args, **options):
        carpeta = options['carpeta']
        dry_run = options['dry_run']
        importar_todos = options['todos']
        dias = options['dias']

        if not os.path.isdir(carpeta):
            self.stderr.write(self.style.ERROR(f'Carpeta no encontrada: {carpeta}'))
            return

        # Límite de tiempo: archivos más recientes que N días
        tiempo_limite = time.time() - (dias * 86400) if not importar_todos else 0

        archivos = sorted(os.listdir(carpeta))
        importados = 0
        omitidos = 0
        sin_patron = 0
        fuera_de_rango = 0

        for nombre_archivo in archivos:
            ruta_completa = os.path.join(carpeta, nombre_archivo)

            if not os.path.isfile(ruta_completa) or nombre_archivo.startswith('.'):
                continue

            # Filtro de fecha: solo archivos recientes (salvo --todos)
            if not importar_todos:
                mtime = os.path.getmtime(ruta_completa)
                if mtime < tiempo_limite:
                    continue

            # Verificar patrón {número}R
            match = PATRON_ARCHIVO.match(nombre_archivo)
            if not match:
                sin_patron += 1
                continue

            admision_id = int(match.group(1))

            # Descartar números que no parecen admisiones válidas
            if admision_id > ADMISION_MAX:
                fuera_de_rango += 1
                self.stdout.write(f'  Ignorado (número inválido): {nombre_archivo}')
                continue

            # Verificar si ya existe este archivo en GE Documental
            ya_existe = ArchivoFacturacion.objects.filter(
                Admision_id=admision_id,
                Tipo=TIPO,
                NombreArchivo=nombre_archivo,
            ).exists()

            if ya_existe:
                omitidos += 1
                continue

            # Ruta destino igual que los demás archivos de GE Documental
            carpeta_destino = f'/neuro/gdocumental/archivosFacturacion/{admision_id}'
            ruta_destino = os.path.join(carpeta_destino, nombre_archivo)
            ruta_relativa = f'gdocumental/archivosFacturacion/{admision_id}/{nombre_archivo}'

            if dry_run:
                self.stdout.write(
                    f'  [DRY-RUN] Importaría: {nombre_archivo} → admisión {admision_id}'
                )
                importados += 1
                continue

            # Copiar archivo a la carpeta estándar de GE Documental
            os.makedirs(carpeta_destino, exist_ok=True)
            shutil.copy2(ruta_completa, ruta_destino)

            # Crear registro en ArchivoFacturacion igual que los demás archivos
            archivo_obj = ArchivoFacturacion(
                Admision_id=admision_id,
                NumeroAdmision=admision_id,
                Tipo=TIPO,
                NombreArchivo=nombre_archivo,
                RevisionPrimera=False,
            )
            archivo_obj.RutaArchivo.name = ruta_relativa
            archivo_obj.save()

            importados += 1
            self.stdout.write(f'  Importado: {nombre_archivo} → admisión {admision_id}')

        prefijo = '[DRY-RUN] ' if dry_run else ''
        if not importar_todos:
            self.stdout.write(f'  (Filtro: archivos de los últimos {dias} día(s))')
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Listo — importados: {importados} | ya existían: {omitidos} | '
            f'sin patrón: {sin_patron} | número inválido: {fuera_de_rango}'
        ))

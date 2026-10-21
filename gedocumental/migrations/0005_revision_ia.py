# Escrita a mano: solo crea la tabla revision_ia.
# (makemigrations además detecta columnas de 'archivos' que ya existen en la BD
#  pero no tienen migración en el repo; no se incluyen para no romper el migrate.)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gedocumental', '0004_archivofacturacion_hasharchivo'),
    ]

    operations = [
        migrations.CreateModel(
            name='RevisionIA',
            fields=[
                ('IdRevision', models.AutoField(primary_key=True, serialize=False)),
                ('AdmisionId', models.IntegerField(db_index=True)),
                ('TipoDocumento', models.CharField(blank=True, default='', max_length=255)),
                ('UsuarioId', models.IntegerField(blank=True, db_index=True, null=True)),
                ('Fecha', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('TipoHallazgo', models.CharField(db_index=True, max_length=20)),
                ('Descripcion', models.TextField(blank=True, default='')),
                ('Modo', models.CharField(blank=True, default='', max_length=10)),
                ('Resuelta', models.BooleanField(db_index=True, default=False)),
                ('Resolucion', models.CharField(blank=True, choices=[('CORREGIDO', 'Corregido (nueva revisión OK)'), ('REEMPLAZADO', 'Reemplazado por una nueva revisión'), ('ARCHIVO_ELIMINADO', 'Archivo eliminado')], max_length=20, null=True)),
                ('FechaResolucion', models.DateTimeField(blank=True, null=True)),
                ('IdArchivo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='RevisionesIA', to='gedocumental.archivofacturacion')),
            ],
            options={
                'db_table': 'revision_ia',
                'managed': True,
            },
        ),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CasoGestion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('admision_id', models.IntegerField(db_index=True)),
                ('bandeja', models.CharField(
                    choices=[
                        ('realizados',   'Realizados sin factura'),
                        ('radicados',    'Facturados sin radicar'),
                        ('devoluciones', 'Devoluciones EPS'),
                    ],
                    db_index=True, max_length=20,
                )),
                ('estado', models.CharField(
                    choices=[
                        ('pendiente',  'Pendiente'),
                        ('en_gestion', 'En gestión'),
                        ('resuelto',   'Resuelto'),
                    ],
                    default='pendiente', max_length=20,
                )),
                ('fecha_ingreso_bandeja', models.DateTimeField(auto_now_add=True)),
                ('fecha_resolucion',      models.DateTimeField(blank=True, null=True)),
                ('numero_radicado',       models.CharField(blank=True, default='', max_length=100)),
                ('fecha_radicado',        models.DateField(blank=True, null=True)),
                ('motivo_devolucion',     models.TextField(blank=True, default='')),
                ('observacion',           models.TextField(blank=True, default='')),
                ('responsable', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='casos_gestion',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('usuario_resolvio', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='casos_resueltos',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('usuario_radico', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='casos_radicados',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'gestion_caso',
                'ordering': ['-fecha_ingreso_bandeja'],
                'unique_together': {('admision_id', 'bandeja')},
            },
        ),
    ]

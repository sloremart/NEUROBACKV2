from django.db import migrations, models


class Migration(migrations.Migration):
    """Seguimiento de hallazgos cerrados por el sistema sin IA (no incluye otros cambios de modelos pendientes)."""

    dependencies = [
        ('gedocumental', '0009_autorizacion_rpa_cups_textfield'),
    ]

    operations = [
        migrations.AddField(
            model_name='revisionia',
            name='NotaResolucion',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='revisionia',
            name='FechaReverificacion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='revisionia',
            name='Resolucion',
            field=models.CharField(blank=True, choices=[
                ('CORREGIDO', 'Corregido (nueva revisión OK)'),
                ('REEMPLAZADO', 'Reemplazado por una nueva revisión'),
                ('ARCHIVO_ELIMINADO', 'Archivo eliminado'),
                ('DESCARTADO', 'Descartado por el sistema (la IA leyó mal el documento)'),
            ], max_length=20, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    """Resolución ACEPTADO: hallazgo real que facturación acepta a mano (solo cambian los choices)."""

    dependencies = [
        ('gedocumental', '0010_revision_ia_seguimiento'),
    ]

    operations = [
        migrations.AlterField(
            model_name='revisionia',
            name='Resolucion',
            field=models.CharField(blank=True, choices=[
                ('CORREGIDO', 'Corregido (nueva revisión OK)'),
                ('REEMPLAZADO', 'Reemplazado por una nueva revisión'),
                ('ARCHIVO_ELIMINADO', 'Archivo eliminado'),
                ('DESCARTADO', 'Descartado por el sistema (la IA leyó mal el documento)'),
                ('ACEPTADO', 'Aceptado manualmente (el documento tiene el error y se acepta así)'),
            ], max_length=20, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    """Solo agrega RevisionIA.DatosRecaudo (no incluye otros cambios de modelos pendientes)."""

    dependencies = [
        ('gedocumental', '0006_add_autorizacion_rpa'),
    ]

    operations = [
        migrations.AddField(
            model_name='revisionia',
            name='DatosRecaudo',
            field=models.TextField(blank=True, default=''),
        ),
    ]

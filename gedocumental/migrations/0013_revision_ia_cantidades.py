from django.db import migrations, models


class Migration(migrations.Migration):
    """Solo agrega RevisionIA.Cantidades (no incluye otros cambios de modelos pendientes)."""

    dependencies = [
        ('gedocumental', '0012_gestion_llamada_demanda'),
    ]

    operations = [
        migrations.AddField(
            model_name='revisionia',
            name='Cantidades',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    """cups puede traer texto largo del bot (descripción del procedimiento)."""

    dependencies = [
        ('gedocumental', '0008_autorizacion_rpa_ampliar_campos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='autorizacionrpa',
            name='cups',
            field=models.TextField(blank=True, null=True),
        ),
    ]

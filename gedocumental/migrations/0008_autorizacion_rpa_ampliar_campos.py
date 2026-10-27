from django.db import migrations, models


class Migration(migrations.Migration):
    """Amplía columnas de AutorizacionRPA que pueden recibir textos largos del bot."""

    dependencies = [
        ('gedocumental', '0007_revision_ia_datos_recaudo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='autorizacionrpa',
            name='num_autorizacion',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AlterField(
            model_name='autorizacionrpa',
            name='mensaje',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='autorizacionrpa',
            name='servicio',
            field=models.TextField(blank=True, null=True),
        ),
    ]

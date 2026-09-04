from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gedocumental', '0003_archivofacturacion_tipohallazgo'),
    ]

    operations = [
        migrations.AddField(
            model_name='archivofacturacion',
            name='HashArchivo',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
    ]

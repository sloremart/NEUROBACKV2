from django.db import migrations, models


class Migration(migrations.Migration):
    """Solo crea AdmisionSinDocumentos (admisiones que por directriz no llevan documentos)."""

    dependencies = [
        ('gedocumental', '0013_revision_ia_cantidades'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdmisionSinDocumentos',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('AdmisionId', models.IntegerField(unique=True)),
                ('Usuario', models.CharField(blank=True, default='', max_length=150)),
                ('Nota', models.TextField(blank=True, default='')),
                ('Fecha', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'admision_sin_documentos', 'managed': True},
        ),
    ]

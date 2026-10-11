from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mandanten', '0018_demo_standard'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginSperre',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('schluessel', models.CharField(max_length=300, unique=True)),
                ('fehlversuche', models.PositiveIntegerField(default=0)),
                ('letzter_versuch', models.DateTimeField(auto_now=True)),
                ('gesperrt_bis', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Anmelde-Sperre',
                'verbose_name_plural': 'Anmelde-Sperren',
            },
        ),
        migrations.AddField(
            model_name='backupziel',
            name='verschluesselung',
            field=models.BooleanField(default=True, help_text='Erzwingt eine verschlüsselte Verbindung (SMB 3 mit AES). Nur abschalten, wenn der Server das nicht kann (sehr alte NAS/Samba-Versionen) und das Netz vertrauenswürdig ist.', verbose_name='Übertragung verschlüsseln (SMB 3)'),
        ),
    ]

from django.db import migrations, models
import planb.common.fields


class Migration(migrations.Migration):

    dependencies = [
        ('transport_exec', '0002_auto_20200512_1700'),
    ]

    operations = [
        migrations.AlterField(
            model_name='config',
            name='transport_command',
            field=planb.common.fields.CommandField(help_text='Program to run to do the transport (data import). It is split by spaces and fed to execve(). Useful variables are available in the environment.', max_length=8000),
        ),
    ]

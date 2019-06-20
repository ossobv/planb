# Generated by Django 2.0.4 on 2019-06-07 13:47

from django.db import migrations, models
import django.db.models.deletion
import planb.common.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('planb', '0012_drop_rsync_config'),
    ]

    operations = [
        migrations.CreateModel(
            name='Config',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('transport_command', planb.common.fields.CommandField(help_text='Program to run to do the transport (data import). It is split by spaces and fed to execve(). Useful variables are available in the environment.', max_length=254)),
                ('fileset', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='planb.Fileset')),
            ],
            options={
                'db_table': 'planb_transport_exec',
            },
        ),
    ]

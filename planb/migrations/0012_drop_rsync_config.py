# Generated by Django 2.0.4 on 2019-06-07 08:34

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0011_hostconfig_to_fileset'),
        ('transport_rsync', '0002_import_fileset'),
    ]

    operations = [
        migrations.RenameField(
            model_name='backuprun',
            old_name='hostconfig',
            new_name='fileset',
        ),
        migrations.RenameField(
            model_name='fileset',
            old_name='description',
            new_name='notes',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='excludes',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='flags',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='host',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='includes',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='ionice_path',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='rsync_path',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='src_dir',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='transport',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='use_ionice',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='use_sudo',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='user',
        ),
        migrations.AlterField(
            model_name='fileset',
            name='hostgroup',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='filesets', to='planb.HostGroup'),
        ),
    ]
# Generated by Django 2.0.4 on 2019-06-13 12:59

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0012_drop_rsync_config'),
    ]

    operations = [
        migrations.RenameField(
            model_name='fileset',
            old_name='enabled',
            new_name='is_enabled',
        ),
        migrations.RenameField(
            model_name='fileset',
            old_name='queued',
            new_name='is_queued',
        ),
        migrations.RenameField(
            model_name='fileset',
            old_name='running',
            new_name='is_running',
        ),
        migrations.AddField(
            model_name='fileset',
            name='do_snapshot_size_listing',
            field=models.BooleanField(default=True, help_text='Summarize disk usage after the transport. This can be slow if there are many files.', verbose_name='Create disk usage summary'),
        ),
    ]

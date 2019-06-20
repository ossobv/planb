# -*- coding: utf-8 -*-
# Generated by Django 1.11.1 on 2017-06-01 07:34
from __future__ import unicode_literals

from django.db import migrations, models
import planb.common.fields


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='hostconfig',
            name='description',
            field=models.TextField(default='', help_text='Quick description/tips. Use the first line for labels/tags.'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='hostconfig',
            name='flags',
            field=models.CharField(default='-az --numeric-ids --stats --delete', help_text='Default "-az --delete", add "--no-perms --chmod=D0700,F600" for (windows) hosts without permission bits, add "--iconv=utf8,latin1" for hosts with files with legacy (Latin-1) encoding.', max_length=511),
        ),
        migrations.AlterField(
            model_name='hostconfig',
            name='includes',
            field=planb.common.fields.FilelistField(default='data etc home root srv usr/local/bin var/backups var/lib/dpkg/status* var/lib/psdiff.db* var/spool/cron var/www', max_length=1023),
        ),
    ]

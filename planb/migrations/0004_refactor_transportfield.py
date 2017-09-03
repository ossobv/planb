# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-09-03 14:36
from __future__ import unicode_literals

from django.db import migrations
import planb.models


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0003_auto_20170710_1814'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hostconfig',
            name='transport',
            field=planb.models.TransportChoices(choices=[(0, 'ssh (default)'), (1, 'rsync (port 873)')], default=0),
        ),
    ]

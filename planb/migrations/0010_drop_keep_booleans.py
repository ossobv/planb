# Generated by Django 2.0.4 on 2018-04-09 13:00

import django.core.validators
from django.db import migrations, models


def set_values(apps, schema_editor):
    HostConfig = apps.get_model('planb', 'HostConfig')
    for hc in HostConfig.objects.order_by('id'):
        if not hc.keep_weekly:
            hc.weekly_retention = 0
            hc.save()
        if not hc.keep_monthly:
            hc.monthly_retention = 0
            hc.save()
        if not hc.keep_yearly:
            hc.yearly_retention = 0
            hc.save()


def reset_values(apps, schema_editor):
    HostConfig = apps.get_model('planb', 'HostConfig')
    for hc in HostConfig.objects.order_by('id'):
        hc.keep_weekly = bool(hc.weekly_retention)
        hc.keep_monthly = bool(hc.monthly_retention)
        hc.keep_yearly = bool(hc.yearly_retention)
        hc.save()


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0009_rename_total_size_mb'),
    ]

    operations = [
        migrations.RunPython(set_values, reset_values),
        migrations.AlterField(
            model_name='hostconfig',
            name='monthly_retention',
            field=models.IntegerField(default=11, help_text="How many monthly's do we keep?", validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1000)]),
        ),
        migrations.AlterField(
            model_name='hostconfig',
            name='weekly_retention',
            field=models.IntegerField(default=3, help_text="How many weekly's do we keep?", validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1000)]),
        ),
        migrations.AlterField(
            model_name='hostconfig',
            name='yearly_retention',
            field=models.IntegerField(default=1, help_text="How many yearly's do we keep?", validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1000)]),
        ),
        migrations.RemoveField(
            model_name='hostconfig',
            name='keep_monthly',
        ),
        migrations.RemoveField(
            model_name='hostconfig',
            name='keep_weekly',
        ),
        migrations.RemoveField(
            model_name='hostconfig',
            name='keep_yearly',
        ),
        migrations.RenameField(
            model_name='hostconfig',
            old_name='retention',
            new_name='daily_retention',
        ),
        migrations.AlterField(
            model_name='hostconfig',
            name='daily_retention',
            field=models.IntegerField(default=15, help_text="How many daily's do we keep?", validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(1000)]),
        ),
    ]

# Generated by Django PlanB 1.7 (Django 2.2.12) on 2020-05-05 14:38

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0017_migrate_retention'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='fileset',
            name='daily_retention',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='monthly_retention',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='weekly_retention',
        ),
        migrations.RemoveField(
            model_name='fileset',
            name='yearly_retention',
        ),
    ]

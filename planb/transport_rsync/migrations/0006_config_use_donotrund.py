# Generated by Django PlanB 1.7 (Django 3.2.16) on 2023-03-14 10:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transport_rsync', '0005_change_rsync_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='config',
            name='use_donotrund',
            field=models.BooleanField(default=True, help_text='Delay backup when /var/lib/planb/do-not-run.d has files.', verbose_name='Use do-not-run.d'),
        ),
    ]
# Generated by Django PlanB 1.6.post1+244.g4bbdb5b.dirty (Django 3.2.4) on 2021-08-24 10:31

from django.db import migrations, models


def forwards(apps, schema_editor):
    Tag = apps.get_model('planb', 'Tag')
    Tag.objects.get_or_create(name='double-backup', defaults={'description': (
        'Select for backup to a 2nd location')})


class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0018_auto_20200505_1638'),
    ]

    operations = [
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=63, unique=True)),
                ('description', models.TextField()),
            ],
        ),
        migrations.AlterField(
            model_name='fileset',
            name='notes',
            field=models.TextField(blank=True, help_text='Quick description/tips. The first line is shown in the list view.'),
        ),
        migrations.AddField(
            model_name='fileset',
            name='tags',
            field=models.ManyToManyField(to='planb.Tag', blank=True),
        ),
        migrations.RunPython(forwards, reverse_code=(lambda *args: None)),
    ]

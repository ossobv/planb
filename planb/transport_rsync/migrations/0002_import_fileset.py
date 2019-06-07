from django.db import migrations


def forward(apps, schema_editor):
    Fileset = apps.get_model('planb', 'Fileset')
    Config = apps.get_model('transport_rsync', 'Config')

    for fileset in Fileset.objects.order_by('id').iterator():
        Config.objects.create(
            fileset=fileset,

            host=fileset.host,

            src_dir=fileset.src_dir,
            includes=fileset.includes,
            excludes=fileset.excludes,

            transport=fileset.transport,
            user=fileset.user,

            use_sudo=fileset.use_sudo,
            use_ionice=fileset.use_ionice,

            rsync_path=fileset.rsync_path,
            ionice_path=fileset.ionice_path,

            flags=fileset.flags
        )

        Fileset.objects.filter(pk=fileset.pk).update(host='-converted-')


def backward(apps, schema_editor):
    Fileset = apps.get_model('planb', 'Fileset')
    Config = apps.get_model('transport_rsync', 'Config')

    for fileset in Fileset.objects.order_by('id').iterator():
        config = Config.objects.get(fileset=fileset)

        Fileset.objects.filter(pk=config.fileset_id).update(
            host=config.host,

            src_dir=config.src_dir,
            includes=config.includes,
            excludes=config.excludes,

            transport=config.transport,
            user=config.user,

            use_sudo=config.use_sudo,
            use_ionice=config.use_ionice,

            rsync_path=config.rsync_path,
            ionice_path=config.ionice_path,

            flags=config.flags
        )

        config.delete()


# self.create_exclude_string
# self.create_include_string
# self.generate_rsync_command
# self.get_transport_rsync_uri
# self.get_transport_ssh_known_hosts_args
# self.get_transport_ssh_known_hosts_d
# self.get_transport_ssh_options
# self.get_transport_ssh_rsync_path
# self.get_transport_ssh_uri
# self.get_transport_uri

# run_rsync -> run_transport

class Migration(migrations.Migration):

    dependencies = [
        ('planb', '0011_hostconfig_to_fileset'),
        ('transport_rsync', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

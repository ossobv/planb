import factory
from factory.django import DjangoModelFactory

from planb.factories import FilesetFactory


class ExecConfigFactory(DjangoModelFactory):
    fileset = factory.SubFactory(FilesetFactory)
    transport_command = 'echo "Backing up ${planb_fileset_friendly_name}"'

    class Meta:
        model = 'transport_exec.Config'

import factory
from factory.django import DjangoModelFactory

from planb.factories import FilesetFactory


class RsyncConfigFactory(DjangoModelFactory):
    fileset = factory.SubFactory(FilesetFactory)
    host = factory.SelfAttribute('fileset.friendly_name')

    transport = 0  # SSH

    class Meta:
        model = 'transport_rsync.Config'

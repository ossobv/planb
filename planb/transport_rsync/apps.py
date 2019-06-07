from django.apps import AppConfig


TABLE_PREFIX = 'planb_transport_rsync'


class DefaultConfig(AppConfig):
    name = 'planb.transport_rsync'
    verbose_name = 'Planb Transport rsync/ssh'

import django

if django.VERSION < (3, 2):
    default_app_config = 'planb.transport_rsync.apps.DefaultConfig'

import django.dispatch


backup_done = django.dispatch.Signal(providing_args=['fileset', 'success'])

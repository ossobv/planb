import django.dispatch


# The backup done signal provides the 'fileset' instance and 'success' boolean
# as parameters.
backup_done = django.dispatch.Signal()

from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.views.generic.base import View

from .models import HostConfig
from .tasks import async_backup_job


class EnqueueJob(View):
    def post(self, request, hostconfig_id):
        if not request.user.is_superuser:
            raise PermissionDenied()
        try:
            hostconfig = HostConfig.objects.get(id=hostconfig_id, enabled=True)
        except HostConfig.DoesNotExist:
            raise PermissionDenied()

        self.enqueue(hostconfig)

        return HttpResponseRedirect(
            # Our URL is /bla/bla/123/enqueue/.
            # Drop the "enqueue/".
            # FIXME: Should use proper reverse() instead!
            self.request.path_info.rsplit('/', 2)[0] + '/')

    def enqueue(self, hostconfig):
        if hostconfig.queued or hostconfig.running:
            messages.add_message(
                self.request, messages.ERROR,
                'Job was already queued/running!')
            return False

        # Spawn a single run.
        HostConfig.objects.filter(pk=hostconfig.pk).update(queued=True)
        task_id = async_backup_job(hostconfig)
        messages.add_message(
            self.request, messages.INFO,
            'Spawned job %s as requested.' % (task_id,))

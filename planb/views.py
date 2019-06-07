from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.views.generic.base import View

from .models import Fileset
from .tasks import async_backup_job


class EnqueueJob(View):
    def post(self, request, fileset_id):
        if not request.user.has_perm('planb.add_backuprun'):
            raise PermissionDenied()
        try:
            fileset = Fileset.objects.get(id=fileset_id, enabled=True)
        except Fileset.DoesNotExist:
            raise PermissionDenied()

        self.enqueue(fileset)

        return HttpResponseRedirect(
            # Our URL is /bla/bla/123/enqueue/.
            # Drop the "enqueue/".
            # FIXME: Should use proper reverse() instead!
            self.request.path_info.rsplit('/', 2)[0] + '/')

    def enqueue(self, fileset):
        if fileset.queued or fileset.running:
            messages.add_message(
                self.request, messages.ERROR,
                'Job was already queued/running!')
            return False

        # Spawn a single run.
        Fileset.objects.filter(pk=fileset.pk).update(queued=True)
        task_id = async_backup_job(fileset)
        messages.add_message(
            self.request, messages.INFO,
            'Spawned job %s as requested.' % (task_id,))

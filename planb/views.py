from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.views.generic.base import View

from .models import Fileset
from .tasks import schedule_manual_backup_job
from planb.storage.base import SNAPNAME_PREFIX_RE


class EnqueueJob(View):
    def post(self, request, fileset_id):
        if not request.user.has_perm('planb.add_backuprun'):
            raise PermissionDenied()

        custom_snapname = None

        if request.POST.get('snapname'):
            snapname = request.POST.get('snapname')
            # Snapshots prefixed with settings.PLANB_PREFIX are selected for
            # rotation and should not be used as a custom snapname.
            # The date is always suffixed to the name.
            if (snapname in (None, '', 'planb', settings.PLANB_PREFIX)
                    or not SNAPNAME_PREFIX_RE.match(snapname)):
                raise HttpResponseBadRequest('Invalid snapname')
            custom_snapname = snapname

        try:
            # Allow enqueuing disabled filesets for cases where periodic
            # backups are not desired or possible.
            fileset = Fileset.objects.get(id=fileset_id)
        except Fileset.DoesNotExist:
            raise PermissionDenied()

        self.enqueue(fileset, custom_snapname=custom_snapname)

        return HttpResponseRedirect(
            # Our URL is /bla/bla/123/enqueue/.
            # Drop the "enqueue/".
            # FIXME: Should use proper reverse() instead!
            self.request.path_info.rsplit('/', 2)[0] + '/')

    def enqueue(self, fileset, custom_snapname):
        if fileset.is_queued or fileset.is_running:
            messages.add_message(
                self.request, messages.ERROR,
                'A backup job for this fileset was already queued/running!')
            return False

        # Spawn a single run.
        Fileset.objects.filter(pk=fileset.pk).update(is_queued=True)
        task_id = schedule_manual_backup_job(
            fileset, custom_snapname=custom_snapname)
        if custom_snapname:
            messages.add_message(
                self.request, messages.INFO,
                'Spawned job %s for PERMANENT archive with name %r.' % (
                    task_id, custom_snapname))
        else:
            messages.add_message(
                self.request, messages.INFO,
                'Spawned job %s as requested.' % (task_id,))

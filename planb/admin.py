from django.conf import settings
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html_join, escape as htmlesc
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from planb.common import human

from .forms import FilesetAdminForm, HostGroupAdminForm
from .models import BOGODATE, BackupRun, HostGroup, Fileset
from .tasks import async_backup_job, async_rename_job


def enqueue_multiple(modeladmin, request, queryset):
    for obj in queryset.filter(is_queued=False, is_enabled=True):
        Fileset.objects.filter(pk=obj.pk).update(is_queued=True)
        async_backup_job(obj)
    modeladmin.message_user(
        request, _('The selection has been queued for immediate backup'))
enqueue_multiple.short_description = _(  # noqa
    'Enqueue selected hosts for immediate backup')


class BackupRunAdmin(admin.ModelAdmin):
    list_display = (
        'started', 'fileset', 'success', 'total_size_mb',
        'snapshot_size_mb')


class HostGroupAdmin(admin.ModelAdmin):
    form = HostGroupAdminForm
    list_display = (
        'name', 'notify_email', 'get_retention', 'get_blacklist_hours',
        'filesets')

    def filesets(self, object):
        return format_html_join(
            ' ', '\u25cf <a href="{}">{}</a>',
            self.fileset_iterator(object))

    def fileset_iterator(self, object):
        for pk, name in (
                object.filesets.values_list('id', 'friendly_name')
                .order_by('friendly_name')):
            yield (reverse('admin:planb_fileset_change', args=(pk,)), name)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change and 'name' in form.changed_data:
            for fileset in form.instance.filesets.iterator():
                async_rename_job(
                    fileset, form.instance.name, fileset.friendly_name)
            self.message_user(
                request, _('A rename task has been queued for all filesets in '
                           'the hostgroup'))


class FilesetAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {'fields': (
            'friendly_name', 'hostgroup', 'tags', 'storage_alias',
            'dataset_name', 'notes', 'is_enabled',
        )}),
        ('Status', {'fields': (
            'first_ok', 'last_ok', 'disk_usage', 'run_time',
            'last_run', 'first_fail', 'is_queued', 'is_running',
            'last_error', 'last_ok_snapshot',
        )}),
        ('Advanced', {'fields': (
            'blacklist_hours', 'retention', 'do_snapshot_size_listing',
        )}),
    )

    readonly_fields = tuple(
        # All status fields are never writable by the admin.
        [dict_ for title, dict_ in fieldsets
         if title == 'Status'][0]['fields']) + ('dataset_name',)
    readonly_change_fields = (
        # Don't allow _direct_ changes to storage_alias and storage_path as
        # they are used for the storage location. Friendly name and hostgroup
        # changes _are_ allowed.
        # These changes are consolidated to the storage_path when the dataset
        # move is successful.
        'storage_alias',)

    list_display = (
        'friendly_name', 'hostgroup', 'note',
        'disk_usage', 'run_time', 'get_retention', 'get_blacklist_hours',
        'last_ok_', 'first_fail_',
        'storage_alias', 'enabled_x', 'queued_q', 'running_r',
    )
    list_filter = ('is_enabled',)
    if len(settings.PLANB_STORAGE_POOLS) != 1:
        list_filter += ('storage_alias',)
    list_filter += ('tags', 'hostgroup', 'is_running', 'first_fail')

    actions = [enqueue_multiple]
    form = FilesetAdminForm
    search_fields = ('friendly_name', 'hostgroup__name', 'notes')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_change_fields + self.readonly_fields
        return self.readonly_fields

    def note(self, object):
        "Take first line of notes"
        ret = object.notes.split('\n', 1)[0].strip()
        if len(ret) > 12:
            return ret[0:12] + '...'
        return ret

    def disk_usage(self, object):
        return human.bytes(object.total_size_mb << 20)
    disk_usage.admin_order_field = 'total_size_mb'
    disk_usage.short_description = _('disk usage')

    def run_time(self, object):
        return human.seconds(object.average_duration)
    run_time.admin_order_field = 'average_duration'
    run_time.short_description = _('run time')  # "last run time"

    def first_ok(self, object):
        try:
            ret = (
                object.backuprun_set.filter(success=True)
                .order_by('started').values_list('started', flat=True)
                .first()).strftime('%Y-%m-%d')
        except BackupRun.DoesNotExist:
            ret = '-'
        return ret
    first_ok.short_description = _('First backup success')

    def last_ok_(self, object):
        if not object.last_ok:
            return '-'
        diff = (timezone.now() - object.last_ok)
        return '-{}'.format(human.seconds(diff.total_seconds()))
    last_ok_.admin_order_field = 'last_ok'
    last_ok_.short_description = _('-ok')

    def last_error(self, object):
        if object.first_fail is None:
            return '-'
        try:
            run = object.backuprun_set.order_by('-id')[0]
        except IndexError:
            return '-'
        return run.error_text or '-'

    def last_ok_snapshot(self, object):
        try:
            run = object.last_successful_backuprun
        except BackupRun.DoesNotExist:
            return '-'

        ret = ['<table>']
        for path, size in run.snapshot_size_listing_as_list():
            ret.append(
                '<tr><td style="padding:0 0.4em;"><code>{}</code></td>'
                '<td style="padding:0 0.4em;text-align:right;">{}</td></tr>'
                .format(htmlesc(path), human.bytes(size)))
        ret.append(
            '<tr><th style="padding:0 0.4em;">TOTAL</th>'
            '<th style="padding:0 0.4em;text-align:right;">{}</th></tr>'
            .format(human.bytes(run.snapshot_size)))
        ret.append('</table>')
        return mark_safe(''.join(ret))
    last_ok_snapshot.short_description = _('Last successful snapshot')

    def first_fail_(self, object):
        if not object.first_fail:
            return '-'
        if object.first_fail == BOGODATE:
            return 'MANUAL'
        diff = (timezone.now() - object.first_fail)
        return '-{}'.format(human.seconds(diff.total_seconds()))
    first_fail_.admin_order_field = 'first_fail'
    first_fail_.short_description = _('-fail')

    def enabled_x(self, object):
        return object.is_enabled
    enabled_x.admin_order_field = 'is_enabled'
    enabled_x.boolean = True
    enabled_x.short_description = 'X'

    def queued_q(self, object):
        return object.is_queued
    queued_q.admin_order_field = 'is_queued'
    queued_q.boolean = True
    queued_q.short_description = 'Q'

    def running_r(self, object):
        return object.is_running
    running_r.admin_order_field = 'is_running'
    running_r.boolean = True
    running_r.short_description = 'R'

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if change and (
                'friendly_name' in form.changed_data
                or 'hostgroup' in form.changed_data):
            async_rename_job(
                form.instance, form.instance.hostgroup.name,
                form.instance.friendly_name)
            self.message_user(
                request, _('A rename task has been queued for the fileset'))


admin.site.register(BackupRun, BackupRunAdmin)
admin.site.register(HostGroup, HostGroupAdmin)
admin.site.register(Fileset, FilesetAdmin)

from zlib import adler32

from django.conf import settings
from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.translation import ugettext as _

from planb.utils import human

from .forms import HostConfigAdminForm
from .models import BackupRun, HostGroup, HostConfig
from .tasks import async_backup_job


def enqueue_multiple(modeladmin, request, queryset):
    for obj in queryset.filter(queued=False, enabled=True):
        HostConfig.objects.filter(pk=obj.pk).update(queued=True)
        async_backup_job(obj)
enqueue_multiple.short_description = _(  # noqa
    'Enqueue selected hosts for immediate backup')


class BackupRunAdmin(admin.ModelAdmin):
    list_display = (
        'started', 'hostconfig', 'success', 'total_size_mb',
        'snapshot_size_mb')


class HostGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'notify_email', 'hosts')

    def hosts(self, object):
        return format_html_join(
            ' ', '\u25cf <a href="{}">{}</a>',
            self.hostconfig_iterator(object))

    def hostconfig_iterator(self, object):
        for pk, name in object.hostconfigs.values_list('id', 'friendly_name'):
            yield (reverse("admin:planb_hostconfig_change", args=(pk,)), name)


class HostConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {'fields': (
            'friendly_name', 'hostgroup', 'dest_pool', 'host',
            'description', 'includes', 'excludes', 'enabled',
        )}),
        ('Status', {'fields': (
            'last_ok', 'disk_usage', 'run_time',
            'last_run', 'first_fail', 'queued', 'running',
            'last_error',
        )}),
        ('Transport options', {'fields': (
            'transport', 'src_dir', 'flags',
        )}),
        ('Additional options for SSH transport', {'fields': (
            'rsync_path', 'user', 'ionice_path', 'use_sudo',
            'use_ionice',
        )}),
        ('Retention', {'fields': (
            'retention', 'keep_weekly', 'weekly_retention',
            'keep_monthly', 'monthly_retention', 'keep_yearly',
            'yearly_retention',
        )}),
    )

    readonly_fields = tuple(
        # All status fields are never writable by the admin.
        [dict_ for title, dict_ in fieldsets
         if title == 'Status'][0]['fields'])
    readonly_change_fields = (
        # friendly_name and hostgroup make up the directory name. Don't
        # touch.
        'friendly_name', 'hostgroup', 'dest_pool')

    list_display = (
        'friendly_name', 'hostgroup', 'notes', 'host',
        'disk_usage', 'run_time', 'options',
        'last_ok_', 'first_fail_',
        'dest_pool', 'enabled_x', 'queued_q', 'running_r',
    )
    list_filter = ('enabled',)
    if len(settings.PLANB_STORAGE_POOLS) != 1:
        list_filter += ('dest_pool',)
    list_filter += ('hostgroup', 'running', 'first_fail')

    actions = [enqueue_multiple]
    form = HostConfigAdminForm
    search_fields = ('friendly_name', 'host', 'hostgroup__name', 'description')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_change_fields + self.readonly_fields
        return self.readonly_fields

    def notes(self, object):
        ret = object.description.split('\n', 1)[0].strip()
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

    def last_ok_(self, object):
        if not object.last_ok:
            return '-'
        days = (timezone.now() - object.last_ok).days
        if days:
            return '{}d'.format(days)
        return 'OK'
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

    def first_fail_(self, object):
        if not object.first_fail:
            return '-'
        days = (timezone.now() - object.first_fail).days
        return '{}d'.format(days)
    first_fail_.admin_order_field = 'first_fail'
    first_fail_.short_description = _('-fail')

    def enabled_x(self, object):
        return object.enabled
    enabled_x.admin_order_field = 'enabled'
    enabled_x.boolean = True
    enabled_x.short_description = 'X'

    def queued_q(self, object):
        return object.queued
    queued_q.admin_order_field = 'queued'
    queued_q.boolean = True
    queued_q.short_description = 'Q'

    def running_r(self, object):
        return object.running
    running_r.admin_order_field = 'running'
    running_r.boolean = True
    running_r.short_description = 'R'

    def options(self, object):
        def crc(data):
            value = adler32(data.encode('utf-8', 'replace'))
            if value < 0:
                value += 0x100000000
            return value

        ret = [self.retentions(object), object.user]
        if object.use_sudo:
            ret.append('sudo')
        if object.use_ionice:
            ret.append('ionice')
        if object.includes:
            ret.append('inc=%d:%x' % (
                len(object.includes.split(' ')), crc(object.includes)))
        if object.excludes:
            ret.append('exc=%d:%x' % (
                len(object.excludes.split(' ')), crc(object.excludes)))
        return ', '.join(ret)

    def retentions(self, object):
        retention = []
        if object.retention:
            retention.append('%dd' % object.retention)
        if object.keep_weekly:
            retention.append('%dw' % object.weekly_retention)
        if object.keep_monthly:
            retention.append('%dm' % object.monthly_retention)
        if object.keep_yearly:
            retention.append('%dy' % object.yearly_retention)
        return '/'.join(retention)


admin.site.register(BackupRun, BackupRunAdmin)
admin.site.register(HostGroup, HostGroupAdmin)
admin.site.register(HostConfig, HostConfigAdmin)

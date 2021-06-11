from django.contrib import admin
from django.forms import modelform_factory
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from planb.forms import FilesetRefForm

from .models import Config


class ConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {'fields': (
            'fileset', 'transport_command',
        )}),
        ('Advanced options', {'fields': (
            'can_create_snapshot', 'can_rotate_snapshot',
        )}),
    )

    form = modelform_factory(Config, form=FilesetRefForm)
    readonly_change_fields = (
        'fileset',
    )

    list_display = (
        'fileset', 'transport_command_',
    )

    search_fields = (
        'transport_command', 'fileset__friendly_name',
        'fileset__hostgroup__name', 'fileset__notes',
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_change_fields + self.readonly_fields
        return self.readonly_fields

    def transport_command_(self, object):
        s = object.transport_command.replace(' \\\n', ' ')
        s = s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        parts = s.split()
        if parts:
            path, command = parts[0].rsplit('/', 1)
            parts[0] = '{}/<strong>{}</strong>'.format(path, command)
        return mark_safe(' '.join(parts))
    transport_command_.admin_order_field = 'transport_command'
    transport_command_.short_description = _('transport command')


admin.site.register(Config, ConfigAdmin)

from django.contrib import admin

from planb.forms import generate_filesetref_form

from .models import Config


class ConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {'fields': (
            'fileset', 'transport_command',
        )}),
    )

    form = generate_filesetref_form(Config)
    readonly_change_fields = (
        'fileset',
    )

    list_display = (
        'fileset', 'transport_command',
    )

    search_fields = (
        'transport_command', 'fileset__friendly_name',
        'fileset__hostgroup__name', 'fileset__notes',
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_change_fields + self.readonly_fields
        return self.readonly_fields


admin.site.register(Config, ConfigAdmin)

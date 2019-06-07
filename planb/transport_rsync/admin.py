from zlib import adler32

from django.contrib import admin

from .models import Config

# from .forms import FilesetAdminForm


class ConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {'fields': (
            'fileset', 'host', 'src_dir', 'includes', 'excludes',
        )}),
        ('Transport options', {'fields': (
            'user', 'use_sudo', 'use_ionice',
        )}),
        ('Advanced options', {'fields': (
            'transport', 'flags', 'rsync_path', 'ionice_path',
        )}),
    )

    readonly_change_fields = (
        'fileset',
    )

    list_display = (
        'fileset', 'host', 'options',
    )

    # form = FilesetAdminForm
    search_fields = (
        'host', 'fileset__friendly_name', 'fileset__hostgroup__name',
        'fileset__notes',
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_change_fields + self.readonly_fields
        return self.readonly_fields

    def options(self, object):
        def crc(data):
            value = adler32(data.encode('utf-8', 'replace'))
            if value < 0:
                value += 0x100000000
            return value

        ret = [object.user]
        if object.use_sudo:
            ret.append('sudo')
        if object.use_ionice:
            ret.append('ionice')
        if object.includes:
            ret.append('incl=%d:%x' % (
                len(object.includes.split(' ')), crc(object.includes)))
        if object.excludes:
            ret.append('excl=%d:%x' % (
                len(object.excludes.split(' ')), crc(object.excludes)))
        return ', '.join(ret)


admin.site.register(Config, ConfigAdmin)

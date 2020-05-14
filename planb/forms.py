from django import forms
from django.apps import apps
from django.conf import settings
from django.utils.translation import gettext as _

from planb.storage import storage_pools

from .models import Fileset, HostGroup


class HostGroupAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['blacklist_hours'].help_text = _(
            'Specify hours during which backups are disabled using notation '
            'h,h-h or none to disable blacklist hours. When left empty the '
            'system blacklist hours {} are used.'
            ).format(settings.PLANB_BLACKLIST_HOURS)
        self.fields['retention'].help_text = _(
            'The backup retention period using notation <n><period> separated '
            'by comma: 1y,6m,3w,15d. When left empty the system retention '
            '{} is used.').format(settings.PLANB_RETENTION)

    class Meta:
        model = HostGroup
        fields = '__all__'


class FilesetAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            blacklist_hours = self.instance.hostgroup.get_blacklist_hours()
            retention = self.instance.hostgroup.get_retention()
        else:
            blacklist_hours = settings.PLANB_BLACKLIST_HOURS
            retention = settings.PLANB_RETENTION
        self.fields['blacklist_hours'].help_text = _(
            'Specify hours during which backups are disabled using notation '
            'h,h-h or none to disable blacklist hours. When left empty the '
            'hostgroup blacklist hours {} are used.'
            ).format(blacklist_hours)
        self.fields['retention'].help_text = _(
            'The backup retention period using notation <n><period> separated '
            'by comma: 1y,6m,3w,15d. When left empty the hostgroup retention '
            '{} is used.').format(retention)

        if 'storage_alias' in self.fields:
            storage_choices = tuple(
                (storage.alias, storage.get_label())
                for storage in storage_pools.values())
            self.fields['storage_alias'] = forms.ChoiceField(
                label=_('Storage'), choices=storage_choices)

        if 'hostgroup' in self.fields:
            self.fields['hostgroup'].queryset = (
                self.fields['hostgroup'].queryset.order_by('name'))

    class Meta:
        model = Fileset
        exclude = (
            'last_ok',
            'last_run',
            'first_fail',
        )


class FilesetRefForm(forms.ModelForm):
    """
    Generate FilesetRefForm tailored to the supplied class; so sorting
    of the Filesets works.

    Use in your admin class. For example:

        from django.forms import modelform_factory
        from planb.forms import FilesetRefForm

        class MyModel(models.Model):
            fileset = models.OneToOneField(Fileset)

        class MyModelAdmin(admin.ModelAdmin):
            form = modelform_factory(MyModel, form=FilesetRefForm)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'fileset' in self.fields:
            # Order.
            self.fields['fileset'].queryset = (
                self.fields['fileset'].queryset.order_by('friendly_name'))

            # Get IDs of used filesets.
            ids = set()
            for transport_class_name in settings.PLANB_TRANSPORTS:
                transport_class = apps.get_model(transport_class_name)
                ids.update(transport_class.objects.values_list(
                    'fileset', flat=True))

            # Don't list used filesets.
            # NOTE: This is not a fool-proof way to avoid
            # MultipleObjectsReturned. But it will provide a better
            # interface.
            self.fields['fileset'].queryset = (
                self.fields['fileset'].queryset.exclude(id__in=ids))

    class Meta:
        fields = '__all__'

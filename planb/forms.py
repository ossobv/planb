from django import forms
from django.apps import apps
from django.conf import settings
from django.utils.translation import ugettext as _

from planb.storage import pools

from .models import Fileset


class FilesetAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'storage_alias' in self.fields:
            storage_choices = tuple(
                (pool.alias, pool.get_label())
                for pool in pools.values())
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

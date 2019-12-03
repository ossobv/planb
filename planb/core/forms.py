from django import forms

from planb.storage import pools

from .models import Fileset


class FilesetAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'storage_alias' in self.fields:
            self.fields['storage_alias'].choices = tuple(
                (pool.alias, pool.get_label())
                for pool in pools.values())

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

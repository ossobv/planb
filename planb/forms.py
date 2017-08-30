from django import forms

from .models import HostConfig, get_pools


class HostConfigAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if 'dest_pool' in self.fields:
            self.fields['dest_pool'] = forms.ChoiceField(
                choices=get_pools())

        if 'hostgroup' in self.fields:
            self.fields['hostgroup'].queryset = (
                self.fields['hostgroup'].queryset.order_by('name'))

    class Meta:
        model = HostConfig
        exclude = (
            'priority',
            'date_complete',
            'failure_datetime',
            'file_to_check',
        )

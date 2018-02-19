from django import forms
from django.db import models

from multi_email_field.forms import MultiEmailField as MultiEmailFormField


class FilelistFormField(forms.CharField):
    widget = forms.widgets.Textarea

    def prepare_value(self, value):
        value = super().prepare_value(value)
        if not value:
            return None
        return '\n'.join(value.split(' ')) + '\n'

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        return ' '.join(sorted(value.split()))


class FilelistField(models.CharField):
    def formfield(self, **kwargs):
        return super().formfield(form_class=FilelistFormField)


class MultiEmailField(models.Field):
    description = "A multi e-mail field stored as a multi-lines text"

    def formfield(self, **kwargs):
        # This is a fairly standard way to set up some defaults
        # while letting the caller override them.
        defaults = {'form_class': MultiEmailFormField}
        defaults.update(kwargs)
        return super().formfield(**defaults)

    def get_prep_value(self, value):
        """Perform preliminary non-db specific value checks and conversions."""
        value = super().get_prep_value(value)
        if isinstance(value, list):
            value = "\n".join(value)
        return value

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, list):
            return value
        return value.splitlines()

    def get_internal_type(self):
        return 'TextField'

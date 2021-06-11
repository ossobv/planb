import os

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

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


class CommandFormField(forms.CharField):
    """
    We expect the value to be shlex.split()
    """
    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value

        lines = value.strip().split('\n')
        lines[0] = ' '.join(lines[0].strip().split())
        lines[1:] = ['    {}'.format(
            ' '.join(line.strip().split())) for line in lines[1:]]

        if len(lines) > 1:
            if any(not line.endswith(' \\') for line in lines[0:-1]):
                raise forms.ValidationError(_(
                    'Multiline commands need to use space-backslash to '
                    'continue on the next line'))
            if lines[-1].endswith(' \\'):
                raise forms.ValidationError(_(
                    'Unexpected backslash on last line'))

        command = lines[0].split()[0]
        if not command or command[0] != '/':
            raise forms.ValidationError(_(
                'Command {!r} needs to start with a /').format(command))

        if not os.access(lines[0][0], os.X_OK, effective_ids=True):
            raise forms.ValidationError(_(
                'Command {!r} not found or no permissions').format(command))

        return '\n'.join(lines)


class CommandField(models.CharField):
    def __init__(self, **kwargs):
        max_length = kwargs.get('max_length', 8000)
        # We used to have 254, we need to accept it so migrations will work.
        assert max_length in (254, 8000), kwargs
        kwargs.update({'max_length': max_length})

        super().__init__(**kwargs)

    def formfield(self, **kwargs):
        return super().formfield(
            form_class=CommandFormField,
            widget=forms.Textarea(attrs={'cols': '100', 'rows': '10'}))


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

from django.db import models
from django.utils.translation import gettext_lazy as _


class AbstractTransport(models.Model):
    fileset = models.OneToOneField(
        'planb.Fileset', on_delete=models.CASCADE, related_name='+')

    can_create_snapshot = models.BooleanField(
        _('Can create snapshot'), default=False, help_text=_(
            'When checked the transport will create the snapshots.'))
    can_rotate_snapshot = models.BooleanField(
        _('Can rotate snapshot'), default=False, help_text=_(
            'When checked the transport will rotate the snapshots.'))

    class Meta:
        abstract = True

    def clone(self, **override):
        # See: https://github.com/django/django/commit/a97ecfdea8
        copy = self.__class__.objects.get(pk=self.pk)
        copy.pk = None
        copy.fileset = None

        # Use the overrides.
        for key, value in override.items():
            setattr(copy, key, value)

        copy.save()
        return copy

    def run_transport(self):
        raise NotImplementedError()

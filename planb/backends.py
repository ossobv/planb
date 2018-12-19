from django.contrib.auth.models import Group, Permission
from django.conf import settings

from kleides_dssoclient.backends import DssoLoginBackend


class PlanbDssoLoginBackend(DssoLoginBackend):
    '''
    DssoLoginBackend that adds users with access to the PlanB user group.
    '''
    def configure_user(self, user, dsso_mapping):
        '''
        We expect username, name, email, username, is_superuser in the
        dsso_mapping.
        '''
        user = super(
            PlanbDssoLoginBackend, self).configure_user(user, dsso_mapping)

        user.email = dsso_mapping.get('email', '')
        user.is_superuser = bool(
            dsso_mapping.get('is_superuser') in ('True', 'true', '1'))
        user.is_staff = user.is_superuser

        name = dsso_mapping.get('name', '').split(' ', 1)
        if len(name) == 1:
            name.append('')
        user.first_name = name[0]
        user.last_name = name[1]
        user.save()
        # Add the user to the default user group.
        user.groups.add(self.get_or_create_default_group())

        return user

    def get_or_create_default_group(self):
        group, created = Group.objects.get_or_create(
            name=settings.PLANB_USER_GROUP)
        if created:
            group.permissions.add(
                *Permission.objects.filter(content_type__app_label='planb'))
        return group

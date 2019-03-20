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
        dsso_mapping (but we ignore username and is_superuser).
        '''
        user = super(
            PlanbDssoLoginBackend, self).configure_user(user, dsso_mapping)

        user.email = dsso_mapping.get('email', '')
        user.is_superuser = False   # not needed, we have "PlanB user" group
        user.is_staff = True        # there is only the staff/admin interface

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
            # That is:
            # - add/change/del groups
            # - add/change/del hosts
            # - add/del runs (add = enqueue job, del = when removing config)
            group.permissions.add(*(
                Permission.objects
                .filter(content_type__app_label='planb')
                .filter(content_type__model__in=(
                    'hostgroup', 'hostconfig', 'backuprun'))
                .exclude(codename='change_backuprun')))
        return group

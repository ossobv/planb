import re

from django.conf import settings
from django.core.checks import Critical
from django.core.exceptions import ValidationError

from .models import validate_blacklist_hours, validate_retention


def check_planb_settings(app_configs, **kwargs):
    errors = []
    if settings.PLANB_BLACKLIST_HOURS:
        try:
            validate_blacklist_hours(settings.PLANB_BLACKLIST_HOURS)
        except ValidationError as e:
            errors.append(Critical(
                'settings.PLANB_BLACKLIST_HOURS is invalid', hint=e.message,
                id='planb.E001'))
    if settings.PLANB_RETENTION:
        try:
            validate_retention(settings.PLANB_RETENTION)
        except ValidationError as e:
            errors.append(Critical(
                'settings.PLANB_RETENTION is invalid', hint=e.message,
                id='planb.E002'))
    if (settings.PLANB_PREFIX
            and not re.match(r'^[a-zA-Z]+$', settings.PLANB_PREFIX)):
        errors.append(Critical(
            'PLANB_PREFIX can only contain ascii letters',
            id='planb.E003'))
    return errors

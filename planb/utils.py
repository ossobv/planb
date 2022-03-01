from django.conf import settings


class lazysetting:
    def __init__(self, name, default=None):
        self.name = name
        self.default = default

    def deconstruct(self):
        kwargs = {
            'name': self.name,
        }
        if self.default is not None:
            kwargs['default'] = self.default
        return (
            '{cls.__module__}.{cls.__name__}'.format(cls=self.__class__),
            [],
            kwargs
        )

    def __call__(self):
        return getattr(settings, self.name, self.default)

    def __repr__(self):
        return 'lazysetting({self.name!r}, {self.default!r})'.format(self=self)


def hour_period_advanced(d1, d2):
    if d1 > d2:
        return False
    if d1.hour != d2.hour:
        return True
    return (d2 - d1).total_seconds() > 3600


def day_period_advanced(d1, d2):
    if d1 > d2:
        return False
    if d1.day != d2.day:
        return True
    return (d2 - d1).total_seconds() > 86400


def week_period_advanced(d1, d2):
    if d1 > d2:
        return False
    w1 = d1.isocalendar()[1]
    w2 = d2.isocalendar()[1]
    if w1 != w2:
        return True
    return (d2 - d1).total_seconds() > 604800  # 7 days


def month_period_advanced(d1, d2):
    if d1 > d2:
        return False
    if d1.month != d2.month:
        return True
    return (d2 - d1).total_seconds() > 2678400  # 31 days


def year_period_advanced(d1, d2):
    if d1 > d2:
        return False
    if d1.year != d2.year:
        return True
    return (d2 - d1).total_seconds() > 31622400  # 366 days


RETENTION_PERIOD_ADVANCED = {
    'h': hour_period_advanced,
    'd': day_period_advanced,
    'w': week_period_advanced,
    'm': month_period_advanced,
    'y': year_period_advanced,
}

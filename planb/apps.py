from django.apps import AppConfig
from django.core import checks


class PlanbAppConfig(AppConfig):
    name = 'planb'
    verbose_name = 'PlanB'

    def ready(self):
        # Monkeypatch the debug view ErrorReporter.
        from django.views import debug
        from .monkeypatch import PlanbExceptionReporter
        debug.ExceptionReporter = PlanbExceptionReporter

        from .checks import check_planb_settings
        checks.register(check_planb_settings)

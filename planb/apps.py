from django.apps import AppConfig


class PlanbAppConfig(AppConfig):
    name = 'planb'
    verbose_name = 'PlanB'

    def ready(self):
        # Monkeypatch the debug view ErrorReporter.
        from django.views import debug
        from .monkeypatch import PlanbExceptionReporter
        debug.ExceptionReporter = PlanbExceptionReporter

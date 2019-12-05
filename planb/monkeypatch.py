from pathlib import Path

from django.views import debug

CURRENT_DIR = Path(__file__).parent


class PlanbExceptionReporter(debug.ExceptionReporter):
    """
    Patched ExceptionReporter to change techical_500.txt to
    technical_500_altered.txt. It is not possible to replace the default
    template as django hardcodes the path to the CURRENT_DIR like we do here.
    """
    def get_traceback_text(self):
        """Return plain text version of debug 500 HTTP error page."""
        with Path(
                # Only the path is changed.
                CURRENT_DIR, 'templates', 'monkeypatch',
                'technical_500_altered.txt').open() as fh:
            t = debug.DEBUG_ENGINE.from_string(fh.read())
        c = debug.Context(
            self.get_traceback_data(), autoescape=False, use_l10n=False)
        return t.render(c)

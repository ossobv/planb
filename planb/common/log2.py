from django.utils import log


class AdminEmailHandler(log.AdminEmailHandler):
    """An exception log handler that emails log entries to site admins.

    Fix so subject cannot be ridiculously long.
    """
    def format_subject(self, subject):
        """
        Escape CR and LF characters.
        """
        subject = super().format_subject(subject)
        if len(subject) > 120:
            subject = '{}...'.format(subject[0:117])
        return subject

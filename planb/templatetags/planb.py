from unicodedata import east_asian_width

from django import template
from django.template.defaultfilters import stringfilter
from django.urls import reverse

from planb.models import BOGODATE, BackupRun, Fileset

register = template.Library()


class GlobalMessagesNode(template.Node):
    def render(self, context):
        show_at_most = 10
        backup_failures = list(
            Fileset.objects
            .filter(is_enabled=True, first_fail__isnull=False)
            .exclude(first_fail=BOGODATE)
            .order_by('first_fail')[0:(show_at_most + 1)])
        if not backup_failures:
            return ''

        warnings = []
        if len(backup_failures) > show_at_most:
            warnings.append(
                '<li class="error">There are lots of failed backups. '
                'Listing only the oldest {}.</li>'.format(show_at_most))
            backup_failures.pop()

        for fileset in backup_failures:
            try:
                error = fileset.last_backuprun.error_text.split('\n', 1)[0]
            except BackupRun.DoesNotExist:
                error = 'Unknown error, BackupRun is gone'
            url = reverse("admin:planb_fileset_change", args=(fileset.pk,))

            warnings.append(
                '<li class="warning">'
                'Backup failure since {o.first_fail} of '
                '<a href="{url}">{o.friendly_name}</a>: {error}'.format(
                    o=fileset, url=url, error=error))
        return '<ul class="messagelist">\n{}\n</ul>'.format(
            '\n'.join(warnings))


@register.tag
def global_messages(parser, token):
    """
    Return global messages (warnings/notices).

    Usage::

        {% global_messages %}
    """
    return GlobalMessagesNode()


def column_width(s):
    """Return total column width of the string s, taking into account that
    Emoji use up twice the space"""
    # > emoji characters were first developed through the use of extensions of
    # > legacy East Asian encodings, such as Shift-JIS, and in such a context
    # > they were treated as wide characters. While these extensions have been
    # > added to Unicode or mapped to standardized variation sequences, their
    # > treatment as wide characters has been retained, and extended for
    # > consistency with emoji characters that lack a legacy encoding.
    return sum(2 if east_asian_width(ch) == 'W' else 1 for ch in s)


@register.filter(is_safe=True)
@stringfilter
def unicode_rjust(value, arg):
    """
    Right-align the value in a field of a given width while being aware that
    some characters are "2 characters wide".
    """
    slen = len(value)
    swidth = column_width(value)
    assert swidth >= slen, (value, slen, swidth)
    return value.rjust(int(arg) - (swidth - slen))

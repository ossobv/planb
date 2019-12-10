from django import template
from django.urls import reverse

from planb.models import BOGODATE, Fileset

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
            error = fileset.last_backuprun.error_text.split('\n', 1)[0]
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

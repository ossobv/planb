from django.template.library import Library

from planb.utils import human

register = Library()


@register.filter(is_safe=False)
def bold(value):
    """
    Add '**' around a string value.
    """
    return '**{}**'.format(value)


@register.filter(is_safe=False)
def block(value):
    """
    Prepend 4 spaces to all non-blank lines. Trim whitespace as EOL.
    """
    if value is None:
        return None

    value = str(value)
    lines = [line.rstrip() for line in value.split('\n')]
    lines = ['    ' + line if line else '' for line in lines]
    return '\n'.join(lines)


@register.filter(is_safe=False)
def replaceany(value, token):
    """
    Replace any/all characters with the supplied token.
    """
    if value is None:
        return None

    value = str(value)
    return str(token) * len(value)


@register.filter(is_safe=False)
def formatseconds(value):
    """
    Format seconds as hours/minutes/seconds.
    """
    if value is None:
        return None

    return human.seconds(value)

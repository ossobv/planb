from django.core.management.base import BaseCommand


class BaseCommandWithZabbix(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument('--zabbix', action='store_true', help=(
            'Create output suitable for zabbix'))

        return super().add_arguments(parser)

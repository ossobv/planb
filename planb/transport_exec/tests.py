import shlex

from django.test import TestCase

from .models import Config


class TransportExecTestCase(TestCase):
    def test_shlex_with_backslash(self):
        self.assertEqual(
            shlex.split(
                '/usr/local/bin/planb-zfssync --qlz1 root@10.1.2.3 \\\n'
                '    tank/mysql/log \\\n    tank/mysql/data'),
            ['/usr/local/bin/planb-zfssync', '--qlz1', 'root@10.1.2.3',
             '\n', 'tank/mysql/log', '\n', 'tank/mysql/data'])

    def test_transport_command_with_backslash(self):
        transport = Config(transport_command=(
            '/usr/local/bin/planb-zfssync --qlz1 root@10.1.2.3 \\\n'
            '    tank/mysql/log \\\n    tank/mysql/data'))
        self.assertEqual(
            transport.generate_cmd(),
            ['/usr/local/bin/planb-zfssync', '--qlz1', 'root@10.1.2.3',
             'tank/mysql/log', 'tank/mysql/data'])

    def test_transport_command_with_backslash_2(self):
        transport = Config(transport_command=(
            '/bin/true\\\nabc \\\ndef'))
        self.assertEqual(
            transport.generate_cmd(), ['/bin/trueabc', 'def'])

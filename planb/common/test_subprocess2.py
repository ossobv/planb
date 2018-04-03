from os import environ
from unittest import TestCase

from .subprocess2 import CalledProcessError, check_call


class Subprocess2Test(TestCase):
    def test_calledprocesserror_stderr_on_first_line(self):
        lc_all_old, lc_lang_old = environ.get('LC_ALL'), environ.get('LC_LANG')
        environ['LC_ALL'] = environ['LC_LANG'] = 'C'  # 'nl_NL.UTF-8'
        try:
            check_call(['/bin/ls', '/surely/this/dir/does/not/exist'])
        except CalledProcessError as e:
            # /bin/ls: "/bin/ls: cannot access
            #   '/surely/this/dir/does/not/exist': No such file or
            #   directory" (exit 2)
            line1 = str(e).split('\n', 1)[0].rstrip()
            self.assertIn('/bin/ls:', line1)
            self.assertIn('No such file or directory', line1)
        else:
            self.assertFalse(True, 'Surely the dir does not exist?')
        finally:
            if lc_all_old is None:
                del environ['LC_ALL']
            else:
                environ['LC_ALL'] = lc_all_old
            if lc_lang_old is None:
                del environ['LC_LANG']
            else:
                environ['LC_LANG'] = lc_lang_old

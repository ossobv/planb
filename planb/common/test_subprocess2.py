from unittest import TestCase

from .subprocess2 import CalledProcessError, check_call, check_output


class Subprocess2Test(TestCase):
    def test_calledprocesserror_stderr_on_first_line(self):
        try:
            check_call(
                ['/bin/ls', '/surely/this/dir/does/not/exist'],
                env={'LC_ALL': 'C'})
        except CalledProcessError as e:
            # /bin/ls: "/bin/ls: cannot access
            #   '/surely/this/dir/does/not/exist': No such file or
            #   directory" (exit 2)
            line1 = str(e).split('\n', 1)[0].rstrip()
            self.assertIn('/bin/ls:', line1)
            self.assertIn('No such file or directory', line1)
        else:
            self.assertFalse(True, 'Surely the dir does not exist?')

    def test_return_stderr(self):
        stderr = []
        stdout = check_output(
            'echo IRstdout; echo IRstderr 1>&2', shell=True,
            env={'LC_ALL': 'C'}, return_stderr=stderr)
        self.assertEqual(stdout, b'IRstdout\n')
        self.assertEqual(stderr, [b'IRstderr\n'])

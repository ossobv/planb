from __future__ import absolute_import
from subprocess import (
    CalledProcessError as OrigCalledProcessError,
    PIPE, Popen)


class CalledProcessError(OrigCalledProcessError):
    """
    Version of subprocess.CalledProcessError that also shows the stdout
    and stderr data if available.
    """
    def __init__(self, returncode, cmd, stdout, stderr):
        super().__init__(returncode=returncode, cmd=cmd, output=stdout)
        self.errput = stderr

    def _quote(self, bintext):
        text = bintext.decode('ascii', 'replace')
        text = text.replace('\r', '')
        if not text:
            return ''

        if text.endswith('\n'):
            text = text[0:-1]
        else:
            text += '[noeol]'

        return '> ' + '\n> '.join(text.split('\n'))

    def __str__(self):
        stdout = self._quote(self.output)
        stderr = self._quote(self.errput)

        # Take entire command if string, or first item if tuple.
        short_cmd = self.cmd if isinstance(self.cmd, str) else self.cmd[0]

        ret = []
        ret.append('Command {!r} returned non-zero exit status {}'.format(
            short_cmd, self.returncode))

        if stderr:
            ret.append('STDERR:\n{}'.format(stderr))
        if stdout:
            ret.append('STDOUT:\n{}'.format(stdout))

        if not isinstance(self.cmd, str):
            ret.append('COMMAND:\n{!r}'.format(self.cmd))

        return '\n\n'.join(ret)


def check_call(cmd, *, shell=False, timeout=None):
    """
    Same as check_output, but discards output.

    Note that stdout/stderr are still captured so we have more
    informative exceptions.
    """
    check_output(cmd, shell=shell, timeout=timeout)


def check_output(cmd, *, shell=False, timeout=None):
    """
    Run command with arguments and return its output.

    Behaves as regular subprocess.check_output but raises the improved
    CalledProcessError on error.

    You'll need to decode stdout from binary encoding yourself.
    """
    assert timeout is None, 'Timeout is not supported for now'

    fp, ret, stdout, stderr = None, -1, '', ''
    try:
        fp = Popen(cmd, stdin=None, stdout=PIPE, stderr=PIPE, shell=shell)
        stdout, stderr = fp.communicate()
        ret = fp.wait()
        fp = None
        if ret != 0:
            raise CalledProcessError(ret, cmd, stdout, stderr)
    finally:
        if fp:
            fp.kill()

    return stdout

from __future__ import absolute_import
from re import compile as re_compile
from shlex import quote as shell_quote
from subprocess import (
    CalledProcessError as OrigCalledProcessError,
    PIPE, Popen)


class CalledProcessError(OrigCalledProcessError):
    """
    Version of subprocess.CalledProcessError that also shows the stdout
    and stderr data if available.
    """
    _anychar_re = re_compile(br'[A-Za-z]')  # bytestring-re

    def __init__(self, returncode, cmd, stdout, stderr):
        super().__init__(returncode=returncode, cmd=cmd, output=stdout)
        self.errput = stderr

    def _quote(self, bintext):
        text = bintext.decode('ascii', 'replace')
        text = text.replace('\r', '')
        if not text:
            return ''

        # You should not rely on this output to be complete. And when
        # you're getting this via e-mail, you don't want big mega-byte
        # blobs. Trim it if it's too large:
        if len(text) >= 256 * 1024:
            text = (
                text[0:(128 * 1024)]
                + '\n[... truncated ...]\n'
                + text[-(128 * 1024):])

        if text.endswith('\n'):
            text = text[0:-1]
        else:
            text += '[noeol]'

        return '> ' + '\n> '.join(text.split('\n'))

    @property
    def _short_stderr(self):
        # Take first non-empty, meaningful line. For example:
        # > @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
        # > @    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
        # > @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
        # Here we'd return the second line.
        #
        # >>> timeit.timeit((lambda: any(
        # ...     i in string for i in (
        # ...         'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        # ...         'abcdefghijklmnopqrstuvwxyz'))))
        # 3.488983060999999
        # >>> timeit.timeit((lambda: anychar.search(string)))
        # 0.49033315299993774
        #
        for line in self.errput.splitlines():  # use iterator instead?
            if self._anychar_re.search(line):
                return line.decode('ascii', 'replace').strip()
        return '?'

    def __str__(self):
        stdout = self._quote(self.output)
        stderr = self._quote(self.errput)

        # Take entire command if string, or first item if tuple.
        short_cmd = self.cmd if isinstance(self.cmd, str) else self.cmd[0]

        # Make a meaningful first line.
        ret = ['{cmd}: "{stderr}" (exit {code})'.format(
            cmd=short_cmd, stderr=self._short_stderr.replace('"', '""'),
            code=self.returncode)]

        if stderr:
            ret.append('STDERR:\n{}'.format(stderr))
        if stdout:
            ret.append('STDOUT:\n{}'.format(stdout))

        if not isinstance(self.cmd, str):
            ret.append('COMMAND: {}'.format(argsjoin(self.cmd)))

        ret.append('')
        return '\n\n'.join(ret)


def check_call(cmd, *, env=None, preexec_fn=None, shell=False, timeout=None):
    """
    Same as check_output, but discards output.

    Note that stdout/stderr are still captured so we have more
    informative exceptions.
    """
    check_output(
        cmd, env=env, preexec_fn=preexec_fn, shell=shell, timeout=timeout)


def check_output(cmd, *, env=None, preexec_fn=None, return_stderr=None,
                 shell=False, timeout=None):
    """
    Run command with arguments and return its output.

    Behaves as regular subprocess.check_output but raises the improved
    CalledProcessError on error.

    You'll need to decode stdout from binary encoding yourself.

    If return_stderr is a list, stderr will be added to it, if it's non-empty.
    """
    assert isinstance(return_stderr, list) or return_stderr is None
    assert timeout is None, 'Timeout is not supported for now'

    fp, ret, stdout, stderr = None, -1, '', ''
    try:
        fp = Popen(
            cmd, stdin=None, stdout=PIPE, stderr=PIPE, env=env,
            preexec_fn=preexec_fn, shell=shell)
        stdout, stderr = fp.communicate()
        ret = fp.wait()
        fp = None
        if ret != 0:
            raise CalledProcessError(ret, cmd, stdout, stderr)
    finally:
        if fp:
            fp.kill()

    if stderr and return_stderr is not None:
        return_stderr.append(stderr)
    return stdout


def argsjoin(cmd):
    """
    Return cmd-tuple as a quoted string, safe to pass to a shell.
    """
    def is_safe(arg):
        return all(i in (
            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
            '09123456789_=-+.,/~') for i in arg)

    args = []
    for arg in cmd:
        if is_safe(arg):
            args.append(arg)
        else:
            args.append(shell_quote(arg))
    return ' '.join(args)

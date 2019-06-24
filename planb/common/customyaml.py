import re


class CustomYaml(object):
    """
    Custom YAML dumper that fits the PlanB config export needs exactly.

    The regular YAML dumper would add lots of tags that we don't need.
    This one is just right for this particular output.

    The ugly backslash (\\b) hack signifies that we prefer the data to
    be on the previous line.
    """
    # No need for double quotes around these:
    _yaml_safe_re = re.compile(r'^[a-z/_.][a-z0-9/_.-]*$')

    def __init__(self, obj):
        self._parsed = self._to_string(obj)

    def __str__(self):
        return '\n'.join(self._parsed)

    def _to_string(self, obj):
        return self._from_dict(obj, root=True)

    def _from_obj(self, obj):
        if isinstance(obj, (dict, list, tuple)):
            if len(obj) == 0:
                if isinstance(obj, (dict,)):
                    return ['\b', '{}']
                else:
                    return ['\b', '[]']
            if isinstance(obj, dict):
                return self._from_dict(obj)
            return self._from_list(obj)

        # |<LF>preformatted string<LF>
        if isinstance(obj, str) and '\n' in obj:
            obj = obj.rstrip()  # no need for trailing LFs here
            return ['\b', '|'] + ['  {}'.format(i) for i in obj.split('\n')]

        return ['\b', self._from_atom(obj)]

    def _from_list(self, list_):
        ret = []
        for item in list_:
            if isinstance(item, (list, tuple)):
                raise NotImplementedError('list in list')
            subret = self._from_obj(item)
            if subret[0] == '\b':
                ret.append('- {}'.format(subret[1]))
                ret.extend(['  {}'.format(i) for i in subret[2:]])
            else:
                assert subret[0].startswith('  ')
                subret[0] = '- {}'.format(subret[0][2:])
                ret.extend(subret)

        return ['  {}'.format(i) for i in ret]

    def _from_dict(self, dict_, root=False):
        ret = []
        for key, value in dict_.items():
            subret = self._from_obj(value)
            if subret[0] == '\b':
                ret.append('{}: {}'.format(
                    self._from_atom(key), subret[1]))
                ret.extend(subret[2:])
            else:
                ret.append('{}:'.format(self._from_atom(key)))
                ret.extend(subret)

        if not root:
            return ['  {}'.format(i) for i in ret]

        return ret

    def _from_atom(self, atom):
        if isinstance(atom, str):
            return self._from_string(atom)
        if atom is None:
            return 'null'  # or '~'
        if atom is True:
            return 'true'
        if atom is False:
            return 'false'
        if isinstance(atom, (int, float)):
            return str(atom)
        return self._from_string(str(atom))

    def _from_string(self, string):
        assert isinstance(string, str), string
        if string.lower() in ('null', 'true', 'false'):
            return '"{}"'.format(string)
        if self._yaml_safe_re.match(string):
            return string
        if '\n' in string:
            raise NotImplementedError('did not expect LF here')
        return '"{}"'.format(
            str(string).replace('\\', '\\\\')
            .replace('"', '\\"'))

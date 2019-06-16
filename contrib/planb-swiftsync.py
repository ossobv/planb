#!/usr/bin/env python3
import argparse
import os
import re
import signal
import sys
import threading
import traceback
import warnings

from collections import OrderedDict
from configparser import RawConfigParser, SectionProxy
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile

try:
    from swiftclient import Connection
except ImportError:
    warnings.warn('No swiftclient? You probably need to {!r}'.format(
        'apt-get install python3-swiftclient --no-install-recommends'))

# TODO: allow all containers to be backed up inside a single tree
# TODO: check timestamp of planb-swiftsync.new, esp. now that we exit(1) on any
# error
# TODO: add timestamps to logs
# BUGS: getting the filelist from swiftclient is done in-memory, which may take
# up to several GBs

SAMPLE_INIFILE = r"""\
[SECTION]
; See which containers there are using: rclone lsd SECTION:
type = swift
user = USER:USER
key = SOMEKEY
auth = https://AUTHSERVER/auth/v1.0
tenant =
region =
storage_url =
; Translate in the 'document' container all paths that are (lowercase)
; GUID-style (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx) to "FU/LL/FULLGUID".
planb_translate = document=^(([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{4}-){4}\
[0-9a-f]{12})$=\2/\3/\1
; Translate in the 'wsdl' container all paths that start with "YYYYMMDD"
; to "YYYY/MM/DD/"
planb_translate = wsdl=^(\d{4})(\d{2})(\d{2})/=\1/\2/\3/
; Translate in all containers all paths (files) that end with a slash to %2F.
; (This will conflict with files actually having a %2F there, but that
; is not likely to happen.)
planb_translate = *=/$=%2F
"""


def _signal_handler(signo, _stack_frame):
    global _MT_ABORT, _MT_HAS_THREADS
    _MT_ABORT = signo
    if not _MT_HAS_THREADS:
        # If we have no threads, we can abort immediately.
        sys.exit(128 + signo)  # raises SystemExit()
_MT_ABORT = 0                   # noqa -- aborting?
_MT_HAS_THREADS = False         # do we have threads at all?

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGQUIT, _signal_handler)


class PathTranslator:
    """
    Translates path from remote_path to local_path.

    Test using:

        planb_storage_destination=$(pwd)/data \
        planb_fileset_friendly_name=planb/contrib \
        planb_fileset_id=123 \
        ./planb-swiftsync -c planb-swiftsync.conf SECTION \
            --test-path-translate wsdl

        (provide remote paths on stdin)
    """
    def __init__(self, data_path, container, translations):
        self.data_path = data_path
        self.replacements = []
        for translation in translations:
            container_match, needle, replacement = translation.split('=')
            if container == container_match or container_match == '*':
                self.replacements.append((
                    re.compile(needle), replacement))

    def __call__(self, remote_path):
        for needle, replacement in self.replacements:
            local_path = needle.sub(replacement, remote_path)
            if local_path != remote_path:
                break
        else:
            local_path = remote_path

        return os.path.join(self.data_path, local_path)


class ConfigParserMultiValues(OrderedDict):
    """
    Accept duplicate keys in the RawConfigParser.
    """
    def __setitem__(self, key, value):
        # The RawConfigParser does a second pass. First lists are passed.
        # Secondly concatenated strings are passed.
        assert isinstance(value, (
            ConfigParserMultiValues, SectionProxy, list, str)), (
                key, value, type(value))

        # For the second pass, we could do an optional split by LF. But that
        # makes it harder to notice when this breaks. Instead, just skip the
        # str-setting.
        if isinstance(value, str):  # and '\n' in value:
            # super().__setitem__(key, value.split('\n'))
            return

        if key in self and isinstance(value, list):
            self[key].extend(value)
        else:
            super().__setitem__(key, value)


class SwiftSyncConfig:
    def __init__(self, inifile, section):
        self.read_inifile(inifile, section)
        self.read_environment()

    def read_inifile(self, inifile, section):
        config = RawConfigParser(
            strict=False, empty_lines_in_values=False,
            dict_type=ConfigParserMultiValues)
        config.read([inifile])
        type_ = config.get(section, 'type')
        assert type_ == ['swift'], type_
        self.swift_user = config.get(section, 'user')[-1]
        self.swift_key = config.get(section, 'key')[-1]
        self.swift_auth = config.get(section, 'auth')[-1]
        self.swift_user = config.get(section, 'user')[-1]
        self.swift_container = None
        self.swift_containers = []
        self.planb_translations = config.get(section, 'planb_translate')

    def read_environment(self):
        # /tank/customer-friendly_name/data
        storage = os.environ['planb_storage_destination']
        # friendly_name = os.environ['planb_fileset_friendly_name']
        # fileset_id = os.environ['planb_fileset_id']

        if not storage.endswith('/data'):
            raise ValueError(
                'expected storage path to end in /data, got {!r}'.format(
                    storage))
        if not os.path.exists(storage):
            raise ValueError(
                'data_path does not exist: {!r}'.format(storage))

        self.data_path = storage
        self.metadata_path = storage.rsplit('/', 1)[0]
        assert self.metadata_path.startswith('/'), self.metadata_path

    def set_container(self, container):
        self.swift_container = container

    def get_container(self):
        return self.swift_container

    def get_swift(self):
        conn = Connection(
            authurl=self.swift_auth,
            user=self.swift_user,
            key=self.swift_key,
            tenant_name='UNUSED',
            auth_version='1')

        resp_headers, containers = conn.get_account()
        # containers = [
        #   {'count': 350182, 'bytes': 78285833087, 'name': 'document'}]
        self.swift_containers = [i['name'] for i in containers]
        self.swift_containers.sort()
        if self.swift_container:
            assert self.swift_container in self.swift_containers, (
                self.swift_container, containers)

        return conn

    def get_translator(self):
        return PathTranslator(
            self.data_path, self.get_container(), self.planb_translations)


class SwiftLine:
    def __init__(self, obj):
        # {'bytes': 107713,
        #  'last_modified': '2018-05-25T15:11:14.501890',
        #  'hash': '89602749f508fc9820ef575a52cbfaba',
        #  'name': '20170101/mr/administrative',
        #  'content_type': 'text/xml'}]
        self.obj = obj
        self.size = obj['bytes']
        # FIXME: round last_modified upwards, like rclone does?
        self.modified = '{} {}'.format(
            obj['last_modified'][0:10], obj['last_modified'][11:19])
        self.path = obj['name']
        assert not self.path.startswith(('\\', '/', '.')), self.path


class ListLine:
    def __init__(self, line):
        if '||' in line:
            raise NotImplementedError('FIXME, escapes not implemented')
        self.line = line
        self.path, self._size, self._modified = line.split('|')

    @property
    def size(self):
        return int(self._size)

    @property
    def modified(self):
        # The time is zone agnostic, so let's assume UTC.
        if not hasattr(self, '_modified_cache'):
            self._modified_cache = int(
                # modified has trailing LF, drop it here..
                datetime.strptime(self._modified[0:-1], '%Y-%m-%d %H:%M:%S')
                .replace(tzinfo=timezone.utc).timestamp())
        return self._modified_cache


class SwiftSync:
    def __init__(self, config):
        self.config = config

        # Get data path. Chdir into it so no unmounting can take place.
        data_path = config.data_path
        os.chdir(data_path)

        # Get metadata path where we store listings.
        metadata_path = config.metadata_path
        self._filelock = os.path.join(metadata_path, 'planb-swiftsync.lock')
        self._path_cur = os.path.join(metadata_path, 'planb-swiftsync.cur')
        # ^-- this contains the local truth
        self._path_new = os.path.join(metadata_path, 'planb-swiftsync.new')
        # ^-- the unreached goal
        self._path_del = os.path.join(metadata_path, 'planb-swiftsync.del')
        self._path_add = os.path.join(metadata_path, 'planb-swiftsync.add')

    def sync(self):
        lock_fd = None
        try:
            # Get lock.
            lock_fd = os.open(
                self._filelock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Failed to get lock.
            sys.stderr.write('ERROR: Failed to get lock: {!r}\n'.format(
                self._filelock))
            sys.exit(1)
        else:
            # Do work.
            self.make_lists()
            self.delete_from_list()
            self.add_from_list()
            self.clean_lists()
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
                os.unlink(self._filelock)

    def make_lists(self):
        """
        Build planb-swiftsync.add, planb-swiftsync.del.
        """
        sys.stderr.write('INFO: Building list\n')

        # Only create new list if it didn't exist yet. We'll move it
        # aside when we're done. Or perhaps we should check the date as
        # well (FIXME).
        if not os.path.exists(self._path_new):
            self._make_new_list()

        self._make_diff_lists()

    def delete_from_list(self):
        """
        Delete from planb-swiftsync.del.
        """
        if os.path.getsize(self._path_del):
            sys.stderr.write('INFO: Removing old\n')
            deleter = SwiftSyncDeleter(self, self._path_del)
            deleter.work()

    def add_from_list(self):
        """
        Add from planb-swiftsync.del.
        """
        if os.path.getsize(self._path_add):
            sys.stderr.write('INFO: Adding new\n')
            adder = SwiftSyncAdder(self, self._path_add)
            adder.work()

    def clean_lists(self):
        """
        Remove planb-swiftsync.new so we'll fetch a fresh one on the next run.
        """
        sys.stderr.write('INFO: Done\n')
        os.unlink(self._path_new)

    def _make_new_list(self):
        """
        Create planb-swiftsync.new with the files we want to have.

        This can be slow as we may need to fetch many lines from swift.
        """
        # full_listing:
        #     if True, return a full listing, else returns a max of
        #     10000 listings
        resp_headers, lines = self.config.get_swift().get_container(
            self.config.get_container(), full_listing=True)
        path_tmp = '{}.tmp'.format(self._path_new)
        with open(path_tmp, 'w') as dest:
            for line in lines:
                record = SwiftLine(line)
                dest.write('{}|{}|{}\n'.format(
                    record.path.replace('|', '||'),
                    record.size,
                    record.modified))
        os.rename(path_tmp, self._path_new)

    def _make_diff_lists(self):
        """
        Create planb-swiftsync.add and planb-swiftsync.del based on
        planb-swiftsync.new and planb-swiftsync.cur.
        """
        try:
            cur_fp = open(self._path_cur, 'r')
        except FileNotFoundError:
            with open(self._path_cur, 'w'):
                pass
            cur_fp = open(self._path_cur, 'r')

        try:
            with open(self._path_new, 'r') as new_fp:
                with open(self._path_del, 'w') as del_fp:
                    with open(self._path_add, 'w') as add_fp:
                        _comm(
                            left_fp=cur_fp, right_fp=new_fp,
                            # We already have it if in both:
                            do_both=(lambda e: None),
                            # Remove when only in cur_fp:
                            do_leftonly=(lambda d: del_fp.write(d)),
                            # Add when only in new_fp:
                            do_rightonly=(lambda a: add_fp.write(a)))
        finally:
            cur_fp.close()

    def update_cur_list_from_added(self, added_fp):
        """
        Update planb-swiftsync.cur by adding all from added_fp.
        """
        path_tmp = '{}.tmp'.format(self._path_cur)
        with open(self._path_cur, 'r') as cur_fp:
            with open(path_tmp, 'w') as tmp_fp:
                _comm(
                    left_fp=cur_fp, right_fp=added_fp,
                    # Keep it if in both:
                    do_both=(lambda e: tmp_fp.write(e)),
                    # Keep it if we already had it:
                    do_leftonly=(lambda d: tmp_fp.write(d)),
                    # Keep it if we added it now:
                    do_rightonly=(lambda a: tmp_fp.write(a)))
        os.rename(path_tmp, self._path_cur)

    def update_cur_list_from_deleted(self, deleted_fp):
        """
        Update planb-swiftsync.cur by removing all from deleted_fp.
        """
        path_tmp = '{}.tmp'.format(self._path_cur)
        with open(self._path_cur, 'r') as cur_fp:
            with open(path_tmp, 'w') as tmp_fp:
                _comm(
                    left_fp=cur_fp, right_fp=deleted_fp,
                    # Drop it if in both (we deleted it now):
                    do_both=(lambda e: None),
                    # Keep it if we didn't touch it:
                    do_leftonly=(lambda d: tmp_fp.write(d)),
                    # This should not happen:
                    do_rightonly=None)
        os.rename(path_tmp, self._path_cur)


class SwiftSyncDeleter:
    def __init__(self, swiftsync, source):
        self._swiftsync = swiftsync
        self._source = source

    def work(self):
        with NamedTemporaryFile(delete=True, mode='w+') as success_fp:
            try:
                self._delete_old(success_fp)
            finally:
                success_fp.flush()
                success_fp.seek(0)
                self._swiftsync.update_cur_list_from_deleted(success_fp)

    def _delete_old(self, success_fp):
        """
        Delete old files (from planb-swiftsync.del) and store which files we
        deleted in the success_fp.
        """
        translator = self._swiftsync.config.get_translator()
        with open(self._source, 'r') as del_fp:
            for record in _comm_lineiter(del_fp):
                path = translator(record.path)
                os.unlink(path)
                success_fp.write(record.line)


class SwiftSyncAdder:
    def __init__(self, swiftsync, source):
        self._swiftsync = swiftsync
        self._source = source
        self._thread_count = 7

    def work(self):
        global _MT_ABORT, _MT_HAS_THREADS

        sys.stderr.write('INFO: Starting {} threads\n'.format(
            self._thread_count))

        thread_lock = threading.Lock()
        threads = [
            SwiftSyncMultiAdder(
                swiftsync=self._swiftsync, source=self._source,
                offset=idx, threads=self._thread_count, lock=thread_lock)
            for idx in range(self._thread_count)]

        _MT_HAS_THREADS = True
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        _MT_HAS_THREADS = False

        if _MT_ABORT:
            raise SystemExit(_MT_ABORT)
        if not all(thread.run_success for thread in threads):
            # One or more threads had a problem. Abort to signify that not
            # everything is fine, even though we did our best.
            raise SystemExit(1)


class SwiftSyncMultiAdder(threading.Thread):
    """
    Multithreaded SwiftSyncAdder.
    """
    def __init__(self, swiftsync, source, offset=0, threads=0, lock=None):
        super().__init__()
        self._swiftsync = swiftsync
        self._source = source
        self._offset = offset
        self._threads = threads
        self._lock = lock
        self.run_success = False

    def run(self):
        with NamedTemporaryFile(delete=True, mode='w+') as success_fp:
            try:
                self._add_new(success_fp)
            finally:
                success_fp.flush()
                success_fp.seek(0)
                sys.stderr.write(
                    'WARNING: Shutting down {}, please be '
                    'patient...\n'.format(self._offset))
                self._lock.acquire()
                try:
                    self._swiftsync.update_cur_list_from_added(success_fp)
                finally:
                    self._lock.release()
                    sys.stderr.write(
                        'WARNING: Shut down {}\n'.format(self._offset))

        self.run_success = True

    def _add_new(self, success_fp):
        """
        Add new files (from planb-swiftsync.add) and store which files we added
        in the success_fp.
        """
        # Create this swift connection first in this thread on purpose. That
        # should minimise swiftclient library MT issues.
        swiftconn = self._swiftsync.config.get_swift()
        container = self._swiftsync.config.get_container()
        translator = self._swiftsync.config.get_translator()
        offset = self._offset
        threads = self._threads
        failures = 0

        # Loop over the planb-swiftsync.add file, but only do our own files.
        with open(self._source, 'r') as add_fp:
            for idx, record in enumerate(_comm_lineiter(add_fp)):
                # When running with multiple threads, we don't use a
                # queue, but simply divide the files over all threads
                # fairly.
                if (idx % threads) != offset:
                    continue

                # Make multi-thread ready.
                if _MT_ABORT:
                    raise ValueError('early abort')

                # Download the file into the appropriate directory.
                failures += self._add_new_record(
                    swiftconn, container, translator, record, success_fp)

        # If there were one or more failures, finish with an exception.
        if failures:
            sys.stderr.write(
                'WARNING: Raising error at EOF to report {} failures\n'.format(
                    failures))
            raise ValueError('abort at EOF with {} failures'.format(failures))

    def _add_new_record(self, swiftconn, container, translator, record,
                        success_fp):
        """
        Download record, add to success_fp if success, return 1 if failed.
        """
        path = translator(record.path)
        if path.endswith('/'):
            sys.stderr.write(
                'WARNING: Skipping {!r} => {!r}; '
                'because of trailing slash\n'.format(record.path, path))
            return 1

        try:
            os.makedirs(os.path.dirname(path), 0o700)
        except FileExistsError:
            pass

        try:
            with open(path, 'wb') as out_fp:
                # resp_chunk_size - if defined, chunk size of data to read.
                # NOTE: If you specify a resp_chunk_size you must fully
                # read the object's contents before making another request.
                resp_headers, obj = swiftconn.get_object(
                    container, record.path, resp_chunk_size=(16 * 1024 * 1024))
                for data in obj:
                    if _MT_ABORT:
                        raise ValueError(
                            'early abort during {}'.format(record.path))
                    out_fp.write(data)
        except Exception as e:
            sys.stderr.write(
                'WARNING: Failure {!r} => {!r}; {}\n'.format(
                    record.path, path, e))
            try:
                # FIXME: also remove directories we just created?
                os.unlink(path)
            except FileNotFoundError:
                pass
            return 1

        os.utime(path, (record.modified, record.modified))
        local_size = os.stat(path).st_size
        if local_size != record.size:
            sys.stderr.write(
                'WARNING: Filesize mismatch {} => {}; {} != {}\n'.format(
                    record.path, path, local_size, record.size))
            try:
                # FIXME: also remove directories we just created?
                os.unlink(path)
            except FileNotFoundError:
                pass
            return 1

        success_fp.write(record.line)
        return 0


def _comm_lineiter(fp):
    """
    Line iterator for _comm. Yields ListLine instances.
    """
    it = iter(fp)

    # Do one manually, so we get prev_path.
    line = next(it)
    record = ListLine(line)
    yield record
    prev_path = record.path

    # Do the rest through normal iteration.
    for line in it:
        record = ListLine(line)
        if prev_path >= record.path:
            raise ValueError('data (sorting?) error: {!r} vs. {!r}'.format(
                prev_path, record.path))
        yield record
        prev_path = record.path


def _comm(left_fp, right_fp, do_both, do_leftonly, do_rightonly):
    """
    Like comm(1) - compare two sorted files line by line - using the
    listing_iter iterator.
    """
    left_iter = _comm_lineiter(left_fp)
    new_iter = _comm_lineiter(right_fp)

    try:
        left = next(left_iter)
    except StopIteration:
        left = left_iter = None
    try:
        right = next(new_iter)
    except StopIteration:
        right = new_iter = None

    while left_iter and new_iter:
        if left.path < right.path:
            # Current is lower, remove and seek current.
            do_leftonly(left.line)
            try:
                left = next(left_iter)
            except StopIteration:
                left = left_iter = None
        elif right.path < left.path:
            # New is lower, add and seek right.
            do_rightonly(right.line)
            try:
                right = next(new_iter)
            except StopIteration:
                right = new_iter = None
        else:
            # They must be equal, remove/add if line is different and seek
            # both.
            if left.line == right.line:
                do_both(right.line)
            else:
                do_leftonly(left.line)
                do_rightonly(right.line)
            try:
                left = next(left_iter)
            except StopIteration:
                left = left_iter = None
            try:
                right = next(new_iter)
            except StopIteration:
                right = new_iter = None

    if left_iter:
        do_leftonly(left.line)
        for left in left_iter:
            do_leftonly(left.line)
    if new_iter:
        do_rightonly(right.line)
        for right in new_iter:
            do_rightonly(right.line)


class Cli:
    def __init__(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            '-c', '--config', metavar='configpath', default='~/.rclone.conf',
            help='inifile location')
        parser.add_argument('inisection')
        parser.add_argument(
            '--test-path-translate', metavar='testcontainer',
            help='test path translation with paths from stdin')
        parser.add_argument('container', nargs='?')
        self.args = parser.parse_args()

    @property
    def config(self):
        if not hasattr(self, '_config'):
            self._config = SwiftSyncConfig(
                os.path.expanduser(self.args.config),
                self.args.inisection)
        return self._config

    def execute(self):
        if self.args.test_path_translate:
            self.test_path_translate(self.args.test_path_translate)
        elif self.args.container:
            self.sync_container(self.args.container)
        else:
            self.sync_all_containers()

    def sync_container(self, container_name):
        self.config.set_container(container_name)
        swiftsync = SwiftSync(self.config)
        swiftsync.sync()

    def sync_all_containers(self):
        raise NotImplementedError()

    def test_path_translate(self, container):
        translator = self.config.get_translator()
        try:
            while True:
                rpath = input()
                lpath = translator(rpath)
                print('{!r} => {!r}'.format(rpath, lpath))
        except EOFError:
            pass


if __name__ == '__main__':
    cli = Cli()
    try:
        cli.execute()
    except SystemExit as e:
        # When it is not handled, the Python interpreter exits; no stack
        # traceback is printed. Print it ourselves.
        if e.code != 0:
            traceback.print_exc()
        sys.exit(e.code)

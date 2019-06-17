#!/usr/bin/env python3
import logging
import os
import re
import signal
import sys
import threading
import traceback
import warnings

from argparse import ArgumentParser
from collections import OrderedDict
from configparser import NoOptionError, RawConfigParser, SectionProxy
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from time import time

try:
    from swiftclient import Connection
except ImportError:
    warnings.warn('No swiftclient? You probably need to {!r}'.format(
        'apt-get install python3-swiftclient --no-install-recommends'))

# TODO: when stopping mid-add, we get lots of "ValueError: early abort"
# backtraces polluting the log; should do without error
# TODO: merging the 7 theaded succcess_fp's into the .cur list is inefficient
# (but perhaps we should just use fewer threads when there are only a handful
# changes)

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


logging.basicConfig(
    level=logging.INFO,
    format=(
        '%(asctime)s [planb-swiftsync:%(threadName)-10.10s] '
        '[%(levelname)-3.3s] %(message)s'),
    handlers=[logging.StreamHandler()])
log = logging.getLogger()


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
        ./planb-swiftsync -c planb-swiftsync.conf SECTION \
            --test-path-translate wsdl

        (provide remote paths on stdin)
    """
    def __init__(self, data_path, container, translations, single_container):
        self.data_path = data_path
        self.container = container
        assert '/' not in container, container
        self.single_container = single_container
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

        if self.single_container:
            return os.path.join(self.data_path, local_path)
        return os.path.join(self.data_path, self.container, local_path)


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
        self.swift_containers = []
        try:
            self.planb_translations = config.get(section, 'planb_translate')
        except NoOptionError:
            self.planb_translations = []

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

    def get_swift(self):
        return Connection(
            authurl=self.swift_auth,
            user=self.swift_user,
            key=self.swift_key,
            tenant_name='UNUSED',
            auth_version='1')

    def get_translator(self, container, single_container):
        return PathTranslator(
            self.data_path, container, self.planb_translations,
            single_container)


class SwiftLine:
    def __init__(self, obj):
        # {'bytes': 107713,
        #  'last_modified': '2018-05-25T15:11:14.501890',
        #  'hash': '89602749f508fc9820ef575a52cbfaba',
        #  'name': '20170101/mr/administrative',
        #  'content_type': 'text/xml'}]
        self.obj = obj
        self.size = obj['bytes']
        assert len(obj['last_modified']) == 26, obj
        assert obj['last_modified'][10] == 'T', obj
        self.modified = obj['last_modified']
        self.path = obj['name']
        assert not self.path.startswith(('\\', '/', '.')), self.path


class ListLine:
    def __init__(self, line):
        if '||' in line:
            raise NotImplementedError('FIXME, escapes not implemented')
        self.line = line
        # Path may include 'container|'.
        self.path, self._modified, self._size = line.rsplit('|', 2)
        if '|' in self.path:
            self.container, self.path = self.path.split('|')
        else:
            self.container = None
        self.container_path = (self.container, self.path)

    @property
    def size(self):
        # NOTE: _size has a trailing LF, but int() silently eats it for us.
        return int(self._size)

    @property
    def modified(self):
        # The time is zone agnostic, so let's assume UTC.
        if not hasattr(self, '_modified_cache'):
            dates, us = self._modified.split('.', 1)
            dates = int(
                datetime.strptime(dates, '%Y-%m-%dT%H:%M:%S')
                .replace(tzinfo=timezone.utc).timestamp())
            assert len(us) == 6
            self._modified_cache = 1000000000 * dates + 1000 * int(us)
        return self._modified_cache


class SwiftSync:
    def __init__(self, config, container=None):
        self.config = config
        self.container = container

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

    def get_containers(self):
        if not hasattr(self, '_get_containers'):
            if self.container:
                self._get_containers = [self.container]
            else:
                resp_headers, containers = (
                    self.config.get_swift().get_account())
                # containers = [
                #   {'count': 350182, 'bytes': 78285833087,
                #    'name': 'document'}]
                self._get_containers = [i['name'] for i in containers]
                self._get_containers.sort()
        return self._get_containers

    def get_translators(self):
        if self.container:
            translators = {None: self.config.get_translator(
                self.container, single_container=True)}
        else:
            translators = dict(
                (container, self.config.get_translator(
                    container, single_container=False))
                for container in self.get_containers())
        return translators

    def sync(self):
        lock_fd = None
        try:
            # Get lock.
            lock_fd = os.open(
                self._filelock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Failed to get lock.
            log.error('Failed to get %r lock', self._filelock)
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
        log.info('Building lists')

        # Only create new list if it didn't exist yet (because we completed
        # successfully the last time) or if it's rather old.
        try:
            last_modified = os.path.getmtime(self._path_new)
        except FileNotFoundError:
            last_modified = 0
        if not last_modified or (time() - last_modified) > (18 * 3600.0):
            self._make_new_list()

        # Make the add/del lists based off cur/new.
        self._make_diff_lists()

    def delete_from_list(self):
        """
        Delete from planb-swiftsync.del.
        """
        if os.path.getsize(self._path_del):
            log.info('Removing old files')
            deleter = SwiftSyncDeleter(self, self._path_del)
            deleter.work()

    def add_from_list(self):
        """
        Add from planb-swiftsync.del.
        """
        if os.path.getsize(self._path_add):
            log.info('Adding new files')
            adder = SwiftSyncAdder(self, self._path_add)
            adder.work()

    def clean_lists(self):
        """
        Remove planb-swiftsync.new so we'll fetch a fresh one on the next run.
        """
        os.unlink(self._path_new)
        # Also remove add/del files; we don't need them anymore, and they take
        # up space.
        os.unlink(self._path_add)
        os.unlink(self._path_del)
        log.info('Sync done')

    def _make_new_list(self):
        """
        Create planb-swiftsync.new with the files we want to have.

        This can be slow as we may need to fetch many lines from swift.
        """
        path_tmp = '{}.tmp'.format(self._path_new)
        swiftconn = self.config.get_swift()
        with open(path_tmp, 'w') as dest:
            os.chmod(path_tmp, 0o600)
            for container in self.get_containers():
                assert '|' not in container, container
                assert '{' not in container, container
                fmt = '{}|{}|{}\n'
                if not self.container:  # multiple containers
                    fmt = '{}|{}'.format(container, fmt)

                log.info('Fetching new list for %r', container)
                # full_listing:
                #     if True, return a full listing, else returns a max of
                #     10000 listings; but that will eat memory, which we don't
                #     want.
                marker = ''  # "start _after_ marker"
                limit = 10000
                while True:
                    resp_headers, lines = swiftconn.get_container(
                        container, full_listing=False, limit=limit,
                        marker=marker)
                    for idx, line in enumerate(lines):
                        record = SwiftLine(line)
                        dest.write(fmt.format(
                            record.path.replace('|', '||'),
                            record.modified,
                            record.size))
                        marker = line['name']
                    if idx + 1 < limit:
                        break
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
                os.chmod(self._path_cur, 0o600)
            cur_fp = open(self._path_cur, 'r')

        try:
            with open(self._path_new, 'r') as new_fp:
                with open(self._path_del, 'w') as del_fp:
                    os.chmod(self._path_del, 0o600)
                    with open(self._path_add, 'w') as add_fp:
                        os.chmod(self._path_add, 0o600)
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
                os.chmod(path_tmp, 0o600)
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
                os.chmod(path_tmp, 0o600)
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
        translators = self._swiftsync.get_translators()
        with open(self._source, 'r') as del_fp:
            for record in _comm_lineiter(del_fp):
                # FIXME: should also try to delete unused directories?
                path = translators[record.container](record.path)
                os.unlink(path)
                success_fp.write(record.line)


class SwiftSyncAdder:
    def __init__(self, swiftsync, source):
        self._swiftsync = swiftsync
        self._source = source
        self._thread_count = 7

    def work(self):
        global _MT_ABORT, _MT_HAS_THREADS

        log.info('Starting %d downloader threads', self._thread_count)

        thread_lock = threading.Lock()
        threads = [
            SwiftSyncMultiAdder(
                swiftsync=self._swiftsync, source=self._source,
                offset=idx, threads=self._thread_count, lock=thread_lock)
            for idx in range(self._thread_count)]

        if self._thread_count == 1:
            threads[0].run()
        else:
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
                log.info('Stopping thread, updating lists')
                self._lock.acquire()
                try:
                    self._swiftsync.update_cur_list_from_added(success_fp)
                finally:
                    self._lock.release()
                    log.info('Stopped thread, job done')

        self.run_success = True

    def _add_new(self, success_fp):
        """
        Add new files (from planb-swiftsync.add) and store which files we added
        in the success_fp.
        """
        # Create this swift connection first in this thread on purpose. That
        # should minimise swiftclient library MT issues.
        swiftconn = self._swiftsync.config.get_swift()
        only_container = self._swiftsync.container
        translators = self._swiftsync.get_translators()
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
                    swiftconn, record.container or only_container,
                    translators[record.container],
                    record, success_fp)

        # If there were one or more failures, finish with an exception.
        if failures:
            log.warning('Raising error at end to report %d failures', failures)
            raise ValueError('abort at EOF with {} failures'.format(failures))

    def _add_new_record(self, swiftconn, container, translator, record,
                        success_fp):
        """
        Download record, add to success_fp if success, return 1 if failed.
        """
        path = translator(record.path)
        if path.endswith('/'):
            log.warning(
                'Skipping record %r (from %r) because of trailing slash',
                path, record.container_path)
            return 1

        try:
            os.makedirs(os.path.dirname(path), 0o700)
        except FileExistsError:
            pass

        try:
            with open(path, 'wb') as out_fp:
                # resp_chunk_size - if defined, chunk size of data to read.
                # > If you specify a resp_chunk_size you must fully read
                # > the object's contents before making another request.
                resp_headers, obj = swiftconn.get_object(
                    container, record.path, resp_chunk_size=(16 * 1024 * 1024))
                for data in obj:
                    if _MT_ABORT:
                        raise ValueError('early abort during {}'.format(
                            record.container_path))
                    out_fp.write(data)
        except Exception as e:
            log.warning(
                'Download failure for %r (from %r): %s',
                path, record.container_path, e)
            try:
                # FIXME: also remove directories we just created?
                os.unlink(path)
            except FileNotFoundError:
                pass
            return 1

        os.utime(path, ns=(record.modified, record.modified))
        local_size = os.stat(path).st_size
        if local_size != record.size:
            log.error(
                'Filesize mismatch for %r (from %r): %d != %d',
                path, record.container_path, record.size, local_size)
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
    prev_record = record

    # Do the rest through normal iteration.
    for line in it:
        record = ListLine(line)
        if prev_record.container_path >= record.container_path:
            raise ValueError('data (sorting?) error: {!r} vs. {!r}'.format(
                prev_record.container_path, record.container_path))
        yield record
        prev_record = record


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
        if left.container_path < right.container_path:
            # Current is lower, remove and seek current.
            do_leftonly(left.line)
            try:
                left = next(left_iter)
            except StopIteration:
                left = left_iter = None
        elif right.container_path < left.container_path:
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
        parser = ArgumentParser()
        parser.add_argument(
            '-c', '--config', metavar='configpath', default='~/.rclone.conf',
            help='inifile location')
        parser.add_argument('inisection')
        parser.add_argument(
            '--test-path-translate', metavar='testcontainer',
            help='test path translation with paths from stdin')
        parser.add_argument('container', nargs='?')
        parser.add_argument('--all-containers', action='store_true')
        self.args = parser.parse_args()

        if not self.args.test_path_translate:
            if not (bool(self.args.container) ^
                    bool(self.args.all_containers)):
                parser.error('either specify a container or --all-containers')

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
        elif self.args.all_containers:
            self.sync_all_containers()
        else:
            raise NotImplementedError()

    def sync_container(self, container_name):
        swiftsync = SwiftSync(self.config, container_name)
        swiftsync.sync()

    def sync_all_containers(self):
        swiftsync = SwiftSync(self.config)
        swiftsync.sync()

    def test_path_translate(self, container):
        translator = self.config.get_translator(container)
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

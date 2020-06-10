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
from configparser import RawConfigParser, SectionProxy
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from time import time
from unittest import TestCase

try:
    from swiftclient import Connection
except ImportError:
    warnings.warn('No swiftclient? You probably need to {!r}'.format(
        'apt-get install python3-swiftclient --no-install-recommends'))
else:
    from swiftclient.exceptions import ClientException

# TODO: when stopping mid-add, we get lots of "ValueError: early abort"
# backtraces polluting the log; should do without error

SAMPLE_INIFILE = r"""\
[acme_swift_v1_config]

; Use rclonse(1) to see the containers: `rclone lsd acme_swift_v1_config:`
type = swift
user = NAMESPACE:USER
key = KEY
auth = https://AUTHSERVER/auth/v1.0

; You can use one or more 'planb_translate' or use 'planb_translate_<N>'
; to define filename translation rules. They may be needed to circumvent
; local filesystem limits (like not allowing trailing / in a filename).

; GUID-style (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx) to "ID/GU/FULLGUID".
planb_translate_0 = document=
    ^([0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{8}([0-9a-f]{2})([0-9a-f]{2}))$=
    \4/\3/\1
; BEWARE ^^ remove excess linefeeds and indentation from this example  ^^

; Translate in the 'wsdl' container all paths that start with "YYYYMMDD"
; to "YYYY/MM/DD/"
planb_translate_1 = wsdl=^(\d{4})(\d{2})(\d{2})/=\1/\2/\3/

; Translate in all containers all paths (files) that end with a slash to %2F.
; (This will conflict with files actually having a %2F there, but that
; is not likely to happen.)
planb_translate_2 = *=/$=%2F


[acme_swift_v3_config]

type = swift
domain = USER_DOMAIN
user = USER
key = KEY
auth = https://AUTHSERVER/v3/
tenant = PROJECT
tenant_domain = PROJECT_DOMAIN
auth_version = 3

; Set this to always to skip autodetection of DLO segment support: not all DLO
; segments are stored in a separate <container>_segments container.
; (You may need to clear the planb-swiftsync.new cache after setting this.)
planb_container_has_segments = always
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

    When single_container=True, the container name is not added into the
    local_path.

    Test using:

        planb_storage_destination=$(pwd)/data \
        ./planb-swiftsync -c planb-swiftsync.conf SECTION \
            --test-path-translate CONTAINERNAME

        (provide remote paths on stdin)
    """
    def __init__(self, data_path, container, translations, single_container):
        assert '/' not in container, container
        assert isinstance(single_container, bool)
        self.data_path = data_path
        self.container = container
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

        # Single container: LOCAL_BASE + TRANSLATED_REMOTE_PATH
        if self.single_container:
            return os.path.join(self.data_path, local_path)

        # Multiple containers: LOCAL_BASE + CONTAINER + TRANSLATED_REMOTE_PATH
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
        configparser = RawConfigParser(
            strict=False, empty_lines_in_values=False,
            dict_type=ConfigParserMultiValues)
        configparser.read([inifile])
        try:
            config = configparser[section]
        except KeyError:
            raise ValueError(
                'no section {!r} found in {!r}'.format(section, inifile))

        type_ = config.get('type', None)
        assert type_ == ['swift'], type_
        self.swift_authver = config.get('auth_version', ['1'])[-1]
        self.swift_auth = config['auth'][-1]   # authurl
        self.swift_user = config['user'][-1]
        self.swift_key = config['key'][-1]
        # auth_version v3:
        self.swift_project = config.get('tenant', [None])[-1]  # project
        self.swift_pdomain = (
            config.get('tenant_domain', [None])[-1])    # project-domain
        self.swift_udomain = (
            config.get('domain', [None])[-1])           # user-domain
        self.swift_containers = []

        # Accept multiple planb_translate keys. But also accept
        # planb_translate_<id> keys. If you use the rclone config tool,
        # rewriting the file would destroy duplicate keys, so using a
        # suffix is preferred.
        self.planb_translations = []
        for key in sorted(config.keys()):
            if key == 'planb_translate' or key.startswith('planb_translate_'):
                self.planb_translations.extend(config[key])

        # Sometimes segment autodetection can fail, resulting in:
        # > Filesize mismatch for '...': 0 != 9515
        # This is probably because the object has X-Object-Manifest and is a
        # Dynamic Large Object (DLO).
        self.all_containers_have_segments = (
            config.get('planb_container_has_segments', [''])[-1] == 'always')

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
        if self.swift_authver == '3':
            os_options = {
                'project_name': self.swift_project,
                'project_domain_name': self.swift_pdomain,
                'user_domain_name': self.swift_udomain,
            }
            connection = Connection(
                auth_version='3', authurl=self.swift_auth,
                user=self.swift_user, key=self.swift_key,
                os_options=os_options)

        elif self.swift_authver == '1':
            connection = Connection(
                auth_version='1', authurl=self.swift_auth,
                user=self.swift_user, key=self.swift_key,
                tenant_name='UNUSED')

        else:
            raise NotImplementedError(
                'auth_version? {!r}'.format(self.swift_authver))

        return connection

    def get_translator(self, container, single_container):
        return PathTranslator(
            self.data_path, container, self.planb_translations,
            single_container)


class SwiftSyncConfigPathTranslators(dict):
    def __init__(self, config, single_container):
        assert isinstance(single_container, bool)
        super().__init__()
        self._config = config
        self._single_container = single_container

    def get(self, *args, **kwargs):
        raise NotImplementedError()

    def __getitem__(self, container):
        try:
            translator = super().__getitem__(container)
        except KeyError:
            translator = self._config.get_translator(
                container, single_container=self._single_container)
            super().__setitem__(container, translator)
        return translator


class SwiftContainer(str):
    # The OpenStack Swift canonical method for handling large objects,
    # is using Dynamic Large Objects (DLO) or Static Large Objects
    # (SLO).
    #
    # In both (DLO and SLO) cases, the CONTAINER file segments are
    # uploaded to a separate container called CONTAINER_segments.
    # When doing a listing over CONTAINER, the segmented files are
    # reported as having 0 size. When that happens, we have to do a HEAD
    # on those files to retreive the actual concatenated file size.
    #
    # This boolean allows us to skip those expensive lookups for all
    # containers X that do not have an X_segments helper container.
    has_segments = False


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

    def __eq__(self, other):
        return (self.size == other.size
                and self.container == other.container
                and self.container_path == other.container_path
                and self.path == other.path)


class SwiftSync:
    def __init__(self, config, container=None):
        self.config = config
        self.container = container

        # Init translators. They're done lazily, so we don't need to know which
        # containers exist yet.
        self._translators = SwiftSyncConfigPathTranslators(
            self.config, single_container=bool(container))

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
            resp_headers, containers = (
                self.config.get_swift().get_account())
            # containers == [
            #   {'count': 350182, 'bytes': 78285833087,
            #    'name': 'containerA'}]
            container_names = set(i['name'] for i in containers)

            force_segments = self.config.all_containers_have_segments

            # Translate container set into containers with and without
            # segments. For example:
            # - containerA (has_segments=False)
            # - containerB (has_segments=True)
            # - containerB_segments (skipped, belongs with containerB)
            # - containerC_segments (has_segments=False)
            selected_containers = []
            for name in sorted(container_names):
                # We're looking for a specific container. Only check whether a
                # X_segments exists. (Because of DLO/SLO we must do the
                # get_accounts() lookup even though we already know
                # which container to process.)
                if self.container:
                    if self.container == name:
                        new = SwiftContainer(name)
                        if force_segments or (
                                '{}_segments'.format(name) in container_names):
                            new.has_segments = True
                        selected_containers.append(new)
                        break

                # We're getting all containers. Check if X_segments exists for
                # it. And only add X_segments containers if there is no X
                # container.
                else:
                    if (name.endswith('_segments')
                            and name.rsplit('_', 1)[0] in container_names):
                        # Don't add X_segments, because X exists.
                        pass
                    else:
                        new = SwiftContainer(name)
                        if force_segments or (
                                '{}_segments'.format(name) in container_names):
                            new.has_segments = True
                        selected_containers.append(new)

            # It's already sorted because we sort the container_names
            # before inserting.
            self._get_containers = selected_containers
        return self._get_containers

    def get_translators(self):
        return self._translators

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
            failures = 0
            failures += self.delete_from_list()
            failures += self.add_from_list()
            # If we bailed out with failures, but without an exception, we'll
            # still clear out the list. Perhaps the list was bad and we simply
            # need to fetch a clean new one (on the next run, that is).
            self.clean_lists()

            if failures:
                raise SystemExit(1)
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

        return 0  # no (recoverable) failures

    def add_from_list(self):
        """
        Add from planb-swiftsync.del.
        """
        if os.path.getsize(self._path_add):
            log.info('Adding new files')
            adder = SwiftSyncAdder(self, self._path_add)
            adder.work()
            return adder.failures  # possibly (recoverable) failures

        return 0  # no (recoverable) failures

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
                        self._make_new_list_add_line(
                            dest, fmt, swiftconn, container, line)
                        marker = line['name']
                    if idx + 1 < limit:
                        break
        os.rename(path_tmp, self._path_new)

    def _make_new_list_add_line(self, dest, fmt, swiftconn, container, line):
        record = SwiftLine(line)

        if record.size == 0 and container.has_segments:
            # Do a head to get DLO/SLO stats. This is
            # only needed if this container has segments,
            # and if the apparent file size is 0.
            try:
                obj_stat = swiftconn.head_object(
                    container, line['name'])
                # If this is still 0, then it's an empty file
                # anyway.
                record.size = int(obj_stat['content-length'])
            except ClientException as e:
                # 404?
                log.warning(
                    'File %r %r disappeared from under us '
                    'when doing a HEAD (%s)',
                    container.name, line['name'], e)
                # Skip record.
                return

        dest.write(fmt.format(
            record.path.replace('|', '||'),
            record.modified,
            record.size))

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
                        llc = _ListLineComm(cur_fp, new_fp)
                        llc.act(
                            # We already have it if in both:
                            both=(lambda e: None),
                            # Remove when only in cur_fp:
                            leftonly=(lambda d: del_fp.write(d)),
                            # Add when only in new_fp:
                            rightonly=(lambda a: add_fp.write(a)))
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
                llc = _ListLineComm(cur_fp, added_fp)
                llc.act(
                    # Keep it if in both:
                    both=(lambda e: tmp_fp.write(e)),
                    # Keep it if we already had it:
                    leftonly=(lambda d: tmp_fp.write(d)),
                    # Keep it if we added it now:
                    rightonly=(lambda a: tmp_fp.write(a)))
        os.rename(path_tmp, self._path_cur)

    def update_cur_list_from_deleted(self, deleted_fp):
        """
        Update planb-swiftsync.cur by removing all from deleted_fp.
        """
        path_tmp = '{}.tmp'.format(self._path_cur)
        with open(self._path_cur, 'r') as cur_fp:
            with open(path_tmp, 'w') as tmp_fp:
                os.chmod(path_tmp, 0o600)
                llc = _ListLineComm(cur_fp, deleted_fp)
                llc.act(
                    # Drop it if in both (we deleted it now):
                    both=(lambda e: None),
                    # Keep it if we didn't touch it:
                    leftonly=(lambda d: tmp_fp.write(d)),
                    # This should not happen:
                    rightonly=None)
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
        only_container = self._swiftsync.container

        with open(self._source, 'r') as del_fp:
            for record in _comm_lineiter(del_fp):
                # record.container is None for single_container syncs.
                container = record.container or only_container

                # Locate local path and remove.
                path = translators[container](record.path)
                os.unlink(path)
                # FIXME: should also try to delete unused directories?

                success_fp.write(record.line)


class SwiftSyncAdder:
    def __init__(self, swiftsync, source):
        self._swiftsync = swiftsync
        self._source = source
        self._thread_count = 7

    def work(self):
        global _MT_ABORT, _MT_HAS_THREADS

        log.info('Starting %d downloader threads', self._thread_count)

        threads = [
            SwiftSyncMultiAdder(
                swiftsync=self._swiftsync, source=self._source,
                offset=idx, threads=self._thread_count)
            for idx in range(self._thread_count)]

        if self._thread_count == 1:
            try:
                threads[0].run()
            finally:
                self._merge_success(threads[0].take_success_file())
        else:
            _MT_HAS_THREADS = True
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            _MT_HAS_THREADS = False

            success_fps = [th.take_success_file() for th in threads]
            success_fps = [fp for fp in success_fps if fp is not None]
            self._merge_multi_success(success_fps)
            del success_fps

        if _MT_ABORT:
            raise SystemExit(_MT_ABORT)

        # Collect and sum failure count to signify when not everything is fine,
        # even though we did our best. If we're here, all threads ended
        # succesfully, so they all have a valid failures count.
        self.failures = sum(th.failures for th in threads)

    def _create_combined_success(self, success_fps):
        """
        Merge all success_fps into a single success_fp.

        Returns a new success_fp.
        """
        combined_fp = prev_fp = None
        combined_fp = NamedTemporaryFile(delete=True, mode='w+')
        try:
            prev_fp = NamedTemporaryFile(delete=True, mode='w+')  # start blank

            # Add all success_fps into combined_fp. Update prev_fp to
            # hold combined_fp.
            for added_fp in success_fps:
                if added_fp is None:
                    continue

                added_size = added_fp.tell()
                added_fp.seek(0)
                if added_size:
                    prev_size = prev_fp.tell()
                    prev_fp.seek(0)
                    log.info(
                        'Merging success lists (%d into %d)',
                        added_size, prev_size)
                    llc = _ListLineComm(prev_fp, added_fp)
                    llc.act(
                        # Keep it if in both:
                        both=(lambda e: combined_fp.write(e)),
                        # Keep it if we already had it:
                        leftonly=(lambda d: combined_fp.write(d)),
                        # Keep it if we added it now:
                        rightonly=(lambda a: combined_fp.write(a)))
                    combined_fp.flush()

                    # We don't need left anymore. Make combined the new left.
                    # Create new combined where we merge the next success_fp.
                    prev_fp.close()
                    prev_fp, combined_fp = combined_fp, None
                    combined_fp = NamedTemporaryFile(delete=True, mode='w+')

            # We want combined_fp at this point, but it's currently in
            # prev_fp. Note that the new combined_fp is at EOF (unseeked).
            combined_fp.close()
            combined_fp, prev_fp = prev_fp, None
        except Exception:
            if prev_fp:
                prev_fp.close()
            if combined_fp:
                combined_fp.close()
            raise

        return combined_fp

    def _merge_multi_success(self, success_fps):
        """
        Merge all success_fps into cur.

        This is useful because we oftentimes download only a handful of files.
        First merge those, before we merge them into the big .cur list.

        NOTE: _merge_multi_success will close all success_fps.
        """
        try:
            success_fp = self._create_combined_success(success_fps)
            self._merge_success(success_fp)
        finally:
            for fp in success_fps:
                fp.close()

    def _merge_success(self, success_fp):
        """
        Merge success_fp into (the big) .cur list.

        NOTE: _merge_success will close success_fp.
        """
        if success_fp is None:
            return
        try:
            size = success_fp.tell()
            success_fp.seek(0)
            if size:
                log.info('Merging %d bytes of added files into current', size)
                success_fp.seek(0)
                self._swiftsync.update_cur_list_from_added(success_fp)
        finally:
            success_fp.close()


class SwiftSyncMultiAdder(threading.Thread):
    """
    Multithreaded SwiftSyncAdder.
    """
    def __init__(self, swiftsync, source, offset=0, threads=0):
        super().__init__()
        self._swiftsync = swiftsync
        self._source = source
        self._offset = offset
        self._threads = threads

        self._success_fp = None

    def take_success_file(self):
        """
        You're allowed to take ownership of the file... once.
        """
        ret, self._success_fp = self._success_fp, None
        return ret

    def run(self):
        log.info('Started thread')
        self._success_fp = NamedTemporaryFile(delete=True, mode='w+')
        try:
            self._add_new()
        finally:
            self._success_fp.flush()
            log.info('Stopping thread')

    def _add_new(self):
        """
        Add new files (from planb-swiftsync.add) and call _set_success.
        """
        # Create this swift connection first in this thread on purpose. That
        # should minimise swiftclient library MT issues.
        self._swiftconn = self._swiftsync.config.get_swift()

        translators = self._swiftsync.get_translators()
        only_container = self._swiftsync.container
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

                # record.container is None for single_container syncs.
                container = record.container or only_container

                # Download the file into the appropriate directory.
                failures += self._add_new_record(
                    record, container, translators[container])

        # If there were one or more failures, store them so they can be used by
        # the caller.
        if failures:
            log.warning('At list EOF, got %d failures', failures)
        self.failures = failures

    def _add_new_record(self, record, container, translator):
        """
        Download record, call _set_success() or return 1 if failed.
        """
        path = translator(record.path)
        if path.endswith('/'):
            log.warning(
                'Skipping record %r (from %r) because of trailing slash',
                path, record.container_path)
            return 1

        self._add_new_record_dir(path)
        if not self._add_new_record_download(record, container, path):
            return 1
        if not self._add_new_record_valid(record, path):
            return 1

        self._set_success(record)
        return 0

    def _add_new_record_dir(self, path):
        try:
            os.makedirs(os.path.dirname(path), 0o700)
        except FileExistsError:
            pass

    def _add_new_record_download(self, record, container, path):
        try:
            with open(path, 'wb') as out_fp:
                # resp_chunk_size - if defined, chunk size of data to read.
                # > If you specify a resp_chunk_size you must fully read
                # > the object's contents before making another request.
                resp_headers, obj = self._swiftconn.get_object(
                    container, record.path, resp_chunk_size=(16 * 1024 * 1024))
                if record.size == 0 and 'X-Object-Manifest' in resp_headers:
                    raise NotImplementedError(
                        '0-sized files with X-Object-Manifest? '
                        'cont={!r}, path={!r}, size={!r}, hdrs={!r}'.format(
                            container, record.path, record.size, resp_headers))

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
            return False
        return True

    def _add_new_record_valid(self, record, path):
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
            return False
        return True

    def _set_success(self, record):
        self._success_fp.write(record.line)


def _comm_lineiter(fp):
    """
    Line iterator for _comm. Yields ListLine instances.
    """
    it = iter(fp)

    # Do one manually, so we get prev_path.
    try:
        line = next(it)
    except StopIteration:
        return
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


class _ListLineComm:
    """
    Like comm(1) - compare two sorted files line by line - using the
    _comm_lineiter iterator.

    Usage::

        llc = _ListLineComm(cur_fp, new_fp)
        llc.act(
            # We already have it if in both:
            both=(lambda e: None),
            # Remove when only in cur_fp:
            leftonly=(lambda d: del_fp.write(d)),
            # Add when only in new_fp:
            rightonly=(lambda a: add_fp.write(a)))
    """
    def __init__(self, left, right):
        self._left_src = left
        self._right_src = right

    def act(self, both, leftonly, rightonly):
        self._act_both = both
        self._act_leftonly = leftonly
        self._act_rightonly = rightonly

        self._process_main()
        self._process_tail()

    def _setup(self, source):
        it = _comm_lineiter(source)
        try:
            elem = next(it)
        except StopIteration:
            elem = it = None
        return it, elem

    def _process_main(self):
        # Make local
        act_both, act_leftonly, act_rightonly = (
            self._act_both, self._act_leftonly, self._act_rightonly)

        left_iter, left = self._setup(self._left_src)
        right_iter, right = self._setup(self._right_src)

        while left_iter and right_iter:
            if left.container_path < right.container_path:
                # Current is lower, remove and seek current.
                act_leftonly(left.line)
                try:
                    left = next(left_iter)
                except StopIteration:
                    left = left_iter = None
            elif right.container_path < left.container_path:
                # New is lower, add and seek right.
                act_rightonly(right.line)
                try:
                    right = next(right_iter)
                except StopIteration:
                    right = right_iter = None
            else:
                # They must be equal, remove/add if line is different and seek
                # both.
                if left.line == right.line:
                    act_both(right.line)
                else:
                    act_leftonly(left.line)
                    act_rightonly(right.line)
                try:
                    left = next(left_iter)
                except StopIteration:
                    left = left_iter = None
                try:
                    right = next(right_iter)
                except StopIteration:
                    right = right_iter = None

        # Store
        self._left_iter, self._left = left_iter, left
        self._right_iter, self._right = right_iter, right

    def _process_tail(self):
        if self._left_iter:
            act_leftonly = self._act_leftonly
            act_leftonly(self._left.line)
            for left in self._left_iter:
                act_leftonly(left.line)

        if self._right_iter:
            act_rightonly = self._act_rightonly
            act_rightonly(self._right.line)
            for right in self._right_iter:
                act_rightonly(right.line)


class _ListLineTest(TestCase):
    def test_pipe_in_path(self):
        self.assertRaises(
            NotImplementedError,
            ListLine,
            'containerx|path/to||esc|2021-02-03T12:34:56.654321|1234')

    def test_with_container(self):
        ll = ListLine(
            'contx|path/to/somewhere|2021-02-03T12:34:56.654321|1234')
        self.assertEqual(ll.container, 'contx')
        self.assertEqual(ll.container_path, ('contx', 'path/to/somewhere'))
        self.assertEqual(ll.path, 'path/to/somewhere')
        self.assertEqual(ll.modified, 1612355696654321000)
        self.assertEqual(ll.size, 1234)

    def test_no_container(self):
        ll = ListLine(
            'nocontainer/to/somewhere|2021-02-03T12:34:57.654321|12345')
        self.assertEqual(ll.container, None)
        self.assertEqual(ll.container_path, (None, 'nocontainer/to/somewhere'))
        self.assertEqual(ll.path, 'nocontainer/to/somewhere')
        self.assertEqual(ll.modified, 1612355697654321000)
        self.assertEqual(ll.size, 12345)

    def test_comm_lineiter_good(self):
        a = '''\
contx|a|2021-02-03T12:34:56.654321|1234
contx|ab|2021-02-03T12:34:56.654321|1234
contx|b|2021-02-03T12:34:56.654321|1234
conty|a|2021-02-03T12:34:56.654321|1234'''.split('\n')
        it = _comm_lineiter(a)
        values = [i for i in it]
        self.assertEqual(values, [
            ListLine('contx|a|2021-02-03T12:34:56.654321|1234'),
            ListLine('contx|ab|2021-02-03T12:34:56.654321|1234'),
            ListLine('contx|b|2021-02-03T12:34:56.654321|1234'),
            ListLine('conty|a|2021-02-03T12:34:56.654321|1234')])

    def test_comm_lineiter_error(self):
        a = '''\
contx|a|2021-02-03T12:34:56.654321|1234
contx|c|2021-02-03T12:34:56.654321|1234
contx|b|2021-02-03T12:34:56.654321|1234'''.split('\n')
        it = _comm_lineiter(a)
        self.assertRaises(ValueError, list, it)


class _ListLineCommTest(TestCase):
    def test_mixed(self):
        a = '''\
a|2021-02-03T12:34:56.654321|1234
b|2021-02-03T12:34:56.654321|1234
c|2021-02-03T12:34:56.654321|1234'''.split('\n')
        b = '''\
a|2021-02-03T12:34:56.654321|1234
b2|2021-02-03T12:34:56.654321|1234
b3|2021-02-03T12:34:56.654321|1234
c|2021-02-03T12:34:56.654321|1234
c2|2021-02-03T12:34:56.654321|1234'''.split('\n')
        act_both, act_left, act_right = [], [], []
        llc = _ListLineComm(a, b)
        llc.act(
            both=(lambda e: act_both.append(e)),
            leftonly=(lambda d: act_left.append(d)),
            rightonly=(lambda a: act_right.append(a)))
        self.assertEqual(act_both, [
            'a|2021-02-03T12:34:56.654321|1234',
            'c|2021-02-03T12:34:56.654321|1234'])
        self.assertEqual(act_left, [
            'b|2021-02-03T12:34:56.654321|1234'])
        self.assertEqual(act_right, [
            'b2|2021-02-03T12:34:56.654321|1234',
            'b3|2021-02-03T12:34:56.654321|1234',
            'c2|2021-02-03T12:34:56.654321|1234'])

    def test_oneonly(self):
        a = '''\
a|2021-02-03T12:34:56.654321|1234
b|2021-02-03T12:34:56.654321|1234
c|2021-02-03T12:34:56.654321|1234'''.split('\n')

        act_both, act_left, act_right = [], [], []
        llc = _ListLineComm(a, [])
        llc.act(
            both=(lambda e: act_both.append(e)),
            leftonly=(lambda d: act_left.append(d)),
            rightonly=(lambda a: act_right.append(a)))
        self.assertEqual((act_both, act_left, act_right), ([], a, []))

        act_both, act_left, act_right = [], [], []
        llc = _ListLineComm([], a)
        llc.act(
            both=(lambda e: act_both.append(e)),
            leftonly=(lambda d: act_left.append(d)),
            rightonly=(lambda a: act_right.append(a)))
        self.assertEqual((act_both, act_left, act_right), ([], [], a))


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
            if not (bool(self.args.container)
                    ^ bool(self.args.all_containers)):
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
    # Test using: python3 -m unittest contrib/planb-swiftsync.py
    cli = Cli()
    try:
        cli.execute()
    except SystemExit as e:
        # When it is not handled, the Python interpreter exits; no stack
        # traceback is printed. Print it ourselves.
        if e.code != 0:
            traceback.print_exc()
        sys.exit(e.code)

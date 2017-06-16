#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import errno
import os
import sys
from binascii import crc32
from collections import namedtuple, defaultdict

from . import unrar

V = namedtuple('Version', 'major minor patch')

version = V(0, 1, 0)
RARDLL_VERSION = unrar.RARDllVersion
iswindows = hasattr(sys, 'getwindowsversion')
isosx = 'darwin' in sys.platform.lower()

# local_open() opens a file that wont be inherited by child processes  {{{
if sys.version_info.major < 3:
    if iswindows:
        def local_open(name, mode='r', bufsize=-1):
            mode += 'N'
            return open(name, mode, bufsize)
    elif isosx:
        import fcntl
        FIOCLEX = 0x20006601

        def local_open(name, mode='r', bufsize=-1):
            ans = open(name, mode, bufsize)
            try:
                fcntl.ioctl(ans.fileno(), FIOCLEX)
            except EnvironmentError:
                fcntl.fcntl(ans, fcntl.F_SETFD, fcntl.fcntl(ans, fcntl.F_GETFD) | fcntl.FD_CLOEXEC)
            return ans
    else:
        import fcntl
        try:
            cloexec_flag = fcntl.FD_CLOEXEC
        except AttributeError:
            cloexec_flag = 1
        supports_mode_e = False

        def local_open(name, mode='r', bufsize=-1):
            global supports_mode_e
            mode += 'e'
            ans = open(name, mode, bufsize)
            if supports_mode_e:
                return ans
            old = fcntl.fcntl(ans, fcntl.F_GETFD)
            if not (old & cloexec_flag):
                fcntl.fcntl(ans, fcntl.F_SETFD, old | cloexec_flag)
            else:
                supports_mode_e = True
            return ans
else:
    local_open = open
# }}}


def is_useful(h):
    return not (h['is_dir'] or h['is_symlink'])


class Callback(object):

    def __init__(self, pw=None):
        self.pw = type('')(pw) if pw is not None else None
        self.password_requested = False

    def _get_password(self):
        self.password_requested = True
        return self.pw

    def _process_data(self, data):
        pass

    def reset(self):
        self.password_requested = False


def safe_path(base, relpath):
    base = os.path.abspath(base)
    path = os.path.abspath(os.path.join(base, relpath))
    if (
        os.path.normcase(path) == os.path.normcase(base) or
        not os.path.normcase(path).startswith(os.path.normcase(base))
    ):
        return None
    return path


def is_safe_symlink(base, x):
    base = os.path.normcase(base)
    tgt = os.path.abspath(os.path.join(base, x))
    ntgt = os.path.normcase(tgt)
    extra = ntgt[len(base):]
    return ntgt.startswith(base) and (not extra or extra[0] in (os.sep, '/'))


def ensure_dir(path):
    try:
        os.makedirs(path)
    except EnvironmentError as err:
        if err.errno != errno.EEXIST:
            raise


class PasswordError(ValueError):
    pass


class PasswordRequired(PasswordError):

    def __init__(self, archive_path):
        ValueError.__init__(self, 'A password is required for: %r' % archive_path)


class BadPassword(PasswordError):

    def __init__(self, archive_path):
        ValueError.__init__(self, 'The specified password is incorrect for: %r' % archive_path)


def do_func(func, archive_path, f, c, *args):
    try:
        return func(f, *args)
    except unrar.UNRARError as e:
        m = e.args[0]
        if m == 'ERAR_MISSING_PASSWORD':
            raise PasswordRequired(archive_path)
        if m == 'ERAR_BAD_DATA' and c.password_requested:
            raise (BadPassword if c.pw else PasswordRequired)(archive_path)
        raise


def headers(archive_path, password=None, mode=unrar.RAR_OM_LIST):
    c = Callback(pw=password)
    archive_path = type('')(archive_path)
    f = unrar.open_archive(archive_path, c, mode)
    while True:
        h = do_func(unrar.read_next_header, archive_path, f, c)
        if h is None:
            break
        yield h
        do_func(unrar.process_file, archive_path, f, c)
        c.reset()


def names(archive_path, only_useful=False, password=None):
    for h in headers(archive_path, password=password):
        if not only_useful or is_useful(h):
            yield h['filename']


def comment(archive_path):
    c = Callback()
    archive_path = type('')(archive_path)
    f = unrar.open_archive(archive_path, c)
    return do_func(unrar.get_comment, archive_path, f, c)


class ExtractCallback(Callback):

    def __init__(self, pw=None, verify_data=False):
        self.verify_data = verify_data
        Callback.__init__(self, pw=pw)
        self.crc = 0

    def _process_data(self, data):
        self.write(data)
        self.written += len(data)
        if self.verify_data:
            self.crc = crc32(data, self.crc) & 0xffffffff
        return True

    def reset(self, write=None, crc=0):
        Callback.reset(self)
        self.written = 0
        self.write = write
        self.crc = crc


class FileCorrupt(ValueError):
    pass


def verify(archive_path, crc_map, password=None):
    # Verify CRCs
    crcs = {}
    for h in headers(archive_path, password=password, mode=unrar.RAR_OM_LIST_INCSPLIT):
        crcs[h['filename']] = h['file_crc']
    for k in crc_map:
        got = crc_map[k] & 0xffffffff
        nominal = crcs.get(k, 0) & 0xffffffff
        if nominal != got:
            raise FileCorrupt('The CRC for %r does not match. Expected: %d Got %d' % (
                k, nominal, got))


def extract(archive_path, location, password=None, verify_data=False):
    c = ExtractCallback(pw=password, verify_data=verify_data)
    archive_path = type('')(archive_path)
    f = unrar.open_archive(archive_path, c, unrar.RAR_OM_EXTRACT)
    seen = set()
    crc_map = defaultdict(lambda: 0)
    while True:
        h = do_func(unrar.read_next_header, archive_path, f, c)
        if h is None:
            break
        filename = h['filename']
        if not filename:
            continue
        dest = safe_path(location, filename)
        c.reset(crc=crc_map[filename])
        extracted = False
        if h['is_dir']:
            try:
                os.makedirs(safe_path(location, filename))
            except Exception:
                pass
                # We ignore create directory errors since we dont
                # care about missing empty dirs
            crc_map.pop(filename)
        elif h['is_symlink']:
            syn = h.get('redir_name')
            if syn and not iswindows:
                # Only RAR 5 archives have a redir_name
                syn_base = os.path.dirname(dest)
                if is_safe_symlink(location, os.path.join(syn_base, syn)):
                    ensure_dir(syn_base)
                    os.symlink(syn, dest)
            crc_map.pop(filename)
        else:
            ensure_dir(os.path.dirname(dest))
            c.reset(write=local_open(dest, 'ab' if dest in seen else 'wb').write, crc=crc_map[filename])
            extracted = True
        do_func(unrar.process_file, archive_path, f, c)
        seen.add(dest)
        if extracted:
            crc_map[filename] = c.crc
            c.reset()  # so that file is closed
            os.utime(dest, (h['file_time'], h['file_time']))
    del f
    if verify_data:
        verify(archive_path, crc_map, password=password)


def extract_member(archive_path, predicate, password=None, verify_data=False):
    c = ExtractCallback(pw=password, verify_data=verify_data)
    archive_path = type('')(archive_path)
    f = unrar.open_archive(archive_path, c, unrar.RAR_OM_EXTRACT)
    while True:
        h = do_func(unrar.read_next_header, archive_path, f, c)
        if h is None:
            return
        if h['is_dir'] or h['is_symlink'] or not predicate(h):
            do_func(unrar.process_file, archive_path, f, c, unrar.RAR_SKIP)
        else:
            buf = []
            c.reset(write=buf.append)
            do_func(unrar.process_file, archive_path, f, c)
            break
    del f
    crc_map = {h['filename']: c.crc}
    if verify_data:
        verify(archive_path, crc_map, password=password)
    return b''.join(buf)

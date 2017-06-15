#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import errno
import os
import sys
from collections import namedtuple

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


def process_file(archive_path, f, c):
    try:
        unrar.process_file(f)
    except unrar.UNRARError as e:
        if e.message == 'ERAR_MISSING_PASSWORD':
            raise PasswordRequired(archive_path)
        if e.message == 'ERAR_BAD_DATA' and c.password_requested:
            raise (BadPassword if c.pw else PasswordRequired)(archive_path)
        raise


def headers(archive_path, password=None):
    c = Callback(pw=password)
    f = unrar.open_archive(archive_path, c, False)
    while True:
        h = unrar.read_next_header(f)
        if h is None:
            break
        yield h
        process_file(archive_path, f, c)
        c.reset()


def names(archive_path, only_useful=False, password=None):
    for h in headers(archive_path, password=password):
        if not only_useful or is_useful(h):
            yield h['filename']


def comment(archive_path):
    c = Callback()
    f = unrar.open_archive(archive_path, c, False)
    return unrar.get_comment(f)


class ExtractCallback(Callback):

    def _process_data(self, data):
        self.write(data)
        self.written += len(data)
        return True

    def reset(self, write=None):
        Callback.reset(self)
        self.written = 0
        self.write = write


def extract(archive_path, location, password=None):
    c = ExtractCallback(pw=password)
    f = unrar.open_archive(archive_path, c, True)
    seen = set()
    while True:
        h = unrar.read_next_header(f)
        if h is None:
            break
        if not h['filename']:
            continue
        dest = safe_path(location, h['filename'])
        c.reset(None)
        extracted = False
        if h['is_dir']:
            try:
                os.makedirs(safe_path(location, h['filename']))
            except Exception:
                pass
                # We ignore create directory errors since we dont
                # care about missing empty dirs
        elif h['is_symlink']:
            syn = h.get('redir_name')
            if syn and not iswindows:
                # Only RAR 5 archives have a redir_name
                syn_base = os.path.dirname(dest)
                if is_safe_symlink(location, os.path.join(syn_base, syn)):
                    ensure_dir(syn_base)
                    os.symlink(syn, dest)
        else:
            ensure_dir(os.path.dirname(dest))
            c.reset(local_open(dest, 'ab' if dest in seen else 'wb').write)
            extracted = True
        process_file(archive_path, f, c)
        seen.add(dest)
        if extracted:
            c.reset(None)  # so that file is closed
            os.utime(dest, (h['file_time'], h['file_time']))

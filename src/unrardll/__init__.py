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

# local_open() opens a file that wont be inherited by child processes
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


def is_useful(h):
    return not (h['is_dir'] or h['is_symlink'])


class Callback(object):

    def _get_password(self):
        return None

    def _process_data(self, data):
        pass


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


def names(archive_path, only_useful=False):
    c = Callback()
    f = unrar.open_archive(archive_path, c, False)
    while True:
        h = unrar.read_next_header(f)
        if h is None:
            break
        if not only_useful or is_useful(h):
            yield h['filename']
        unrar.process_file(f)


def comment(archive_path):
    c = Callback()
    f = unrar.open_archive(archive_path, c, False)
    return unrar.get_comment(f)


class ExtractCallback(Callback):

    def _process_data(self, data):
        self.write(data)
        return True

    def reset(self, write=None):
        self.write = write


def extract(archive_path, location):
    c = ExtractCallback()
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
                    os.symlink(h.get('redir_name'), dest)
        else:
            ensure_dir(os.path.dirname(dest))
            c.reset(local_open(dest, 'ab' if dest in seen else 'wb').write)
        unrar.process_file(f)
        seen.add(dest)

#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

from collections import namedtuple

from . import unrar

V = namedtuple('Version', 'major minor patch')

version = V(0, 1, 0)
RARDLL_VERSION = unrar.RARDllVersion


def is_useful(h):
    return not (h['is_dir'] or h['is_symlink'])


class Callback(object):

    def _get_password(self):
        return None

    def _process_data(self, data):
        pass


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

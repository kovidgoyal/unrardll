#!/usr/bin/env python2
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import os

from unrardll import names

from . import TestCase, base

simple_rar = os.path.join(base, 'simple.rar')
sr_data = {
    '1': b'',
    '1/sub-one': b'sub-one\n',
    '2': b'',
    '2/sub-two.txt': b'sub-two\n',
    'F\xfc\xdfe.txt': b'unicode\n',
    'max-compressed': b'max\n',
    'one.txt': b'one\n',
    'symlink': b'2/sub-two.txt',
    'uncompressed': b'uncompressed\n',
    '\u8bf6\u6bd4\u5c41.txt': b'chinese unicode\n'}


class BasicTests(TestCase):

    def test_names(self):
        all_names = [
            '1/sub-one', 'one.txt', '诶比屁.txt', 'Füße.txt', '2/sub-two.txt',
            'symlink', '1', '2', 'uncompressed', 'max-compressed']
        self.ae(all_names, list(names(simple_rar)))
        all_names.remove('symlink'), all_names.remove('1'), all_names.remove('2')
        self.ae(all_names, list(names(simple_rar, only_useful=True)))

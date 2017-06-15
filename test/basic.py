#!/usr/bin/env python2
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import os
from binascii import crc32

from unrardll import names, comment, extract, headers, PasswordRequired, BadPassword

from . import TestCase, base, TempDir

simple_rar = os.path.join(base, 'simple.rar')
sr_data = {
    '1': b'',
    '1/sub-one': b'sub-one\n',
    '2': b'',
    '2/sub-two.txt': b'sub-two\n',
    'Füße.txt': b'unicode\n',
    'max-compressed': b'max\n',
    'one.txt': b'one\n',
    'symlink': b'sub-two',
    'uncompressed': b'uncompressed\n',
    '诶比屁.txt': b'chinese unicode\n'}


def get_memory():
    'Return memory usage in bytes'
    # See https://pythonhosted.org/psutil/#psutil.Process.memory_info
    import psutil
    return psutil.Process(os.getpid()).memory_info().rss


def memory(since=0.0):
    'Return memory used in MB. The value of since is subtracted from the used memory'
    ans = get_memory()
    ans /= float(1024**2)
    return ans - since


class BasicTests(TestCase):

    def test_names(self):
        all_names = [
            '1/sub-one', 'one.txt', '诶比屁.txt', 'Füße.txt', '2/sub-two.txt',
            'symlink', '1', '2', 'uncompressed', 'max-compressed']
        self.ae(all_names, list(names(simple_rar)))
        all_names.remove('symlink'), all_names.remove('1'), all_names.remove('2')
        self.ae(all_names, list(names(simple_rar, only_useful=True)))

    def test_comment(self):
        self.ae(comment(simple_rar), 'some comment\n')

    def test_share_open(self):
        with open(simple_rar, 'rb') as f:
            self.ae(comment(simple_rar), 'some comment\n')
            f.close()

    def test_extract(self):
        with TempDir() as tdir:
            extract(simple_rar, tdir)
            h = {
                os.path.abspath(os.path.join(tdir, h['filename'])): h
                for h in headers(simple_rar)}
            data = {}
            for dirpath, dirnames, filenames in os.walk(tdir):
                for f in filenames:
                    path = os.path.join(dirpath, f)
                    data[os.path.relpath(path, tdir).replace(os.sep, '/')
                         ] = d = open(path, 'rb').read()
                    if f == 'one.txt':
                        self.ae(os.path.getmtime(path), 1098472879)
                    self.ae(h[path]['unpack_size'], len(d))
                    self.ae(h[path]['file_crc'] & 0xffffffff, crc32(d) & 0xffffffff)
        q = {k: v for k, v in sr_data.items() if v}
        del q['symlink']
        self.ae(data, q)

    def test_password(self):
        pr = os.path.join(base, 'example_password_protected.rar')
        with TempDir() as tdir:
            self.assertRaises(PasswordRequired, extract, pr, tdir)
            self.assertRaises(BadPassword, extract, pr, tdir, password='sfasgsfdg')
            extract(pr, tdir, password='example')

    def test_memory_leaks(self):
        import gc

        def collect():
            for i in range(6):
                gc.collect()
            gc.collect()

        with TempDir() as tdir:

            def get_mem_use(num):
                collect()
                start = memory()
                for i in range(num):
                    extract(simple_rar, tdir)
                collect()
                return max(0, memory(start))

            get_mem_use(5)  # ensure no memory used by get_mem_use itself is counted
            a, b = get_mem_use(10), get_mem_use(100)
        self.assertTrue(a == 0 or b/a < 3, '10 times usage: {} 100 times usage: {}'.format(a, b))

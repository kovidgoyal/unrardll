#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import glob
import os
import re
import shutil
import subprocess
import tarfile
import time
from io import BytesIO

try:
    from urllib.request import urlopen
except ImportError:
    from urllib import urlopen


def download(url):
    i = 5
    while i > 0:
        i -= 1
        try:
            return urlopen(url).read()
        except Exception:
            if i <= 0:
                raise
            print('Download failed, retrying...')
            time.sleep(1)


def download_unrar():
    html = download('http://www.rarlab.com/rar_add.htm')
    href = re.search(r'<a\s+.*?href="([^"]+)".*?>UnRAR source</a>', html).group(1)
    return download(href)


def download_and_extract():
    raw = download_unrar()
    with tarfile.open(fileobj=BytesIO(raw), mode='r:*') as tf:
        tf.extractall()


def build_unix():
    flags = '-fPIC ' + os.environ.get('CXXFLAGS', '')
    subprocess.check_call(['make', '-j4', 'lib', 'CXXFLAGS="%s"' % flags.strip()])


def build_unrar():
    os.makedirs('sw/build'), os.mkdir('sw/include'), os.mkdir('sw/lib')
    os.chdir('sw/build')
    download_and_extract()
    os.chdir('unrar')
    build_unix()
    shutil.copy2('libunrar.so', '../../lib')
    for f in glob.glob('*.hpp'):
        shutil.copy2(f, '../../include')


if __name__ == '__main__':
    build_unrar()

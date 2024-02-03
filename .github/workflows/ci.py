#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import glob
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from io import BytesIO

try:
    from urllib.request import urlopen
except ImportError:
    from urllib import urlopen

ismacos = 'darwin' in sys.platform.lower()
iswindows = hasattr(sys, 'getwindowsversion')
is64bit = sys.maxsize > (1 << 32)
plat = 'amd64' if is64bit else 'x86'
lib_dirs = tuple(map(os.path.abspath, os.environ['UNRAR_LIBDIRS'].split(os.pathsep)))
lib_dir = lib_dirs[0]
os.environ['UNRAR_LIBDIRS'] = os.pathsep.join(lib_dirs)
if ismacos:
    os.environ['DYLD_LIBRARY_PATH'] = os.environ['UNRAR_LIBDIRS']
if iswindows:
    os.environ['UNRAR_DLL_DIR'] = os.path.dirname(os.path.dirname(lib_dir))


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
            sys.stdout.flush()
            time.sleep(1)


def download_unrar():
    html = download('https://www.rarlab.com/rar_add.htm').decode('utf-8', 'replace')
    href = re.search(r'<a\s+.*?href="([^"]+)".*?>UnRAR release source</a>', html).group(1)
    href = 'https://www.rarlab.com/' + href
    print('Downloading unrar', href)
    sys.stdout.flush()
    return download(href)


def download_and_extract():
    raw = download_unrar()
    with tarfile.open(fileobj=BytesIO(raw), mode='r:*') as tf:
        tf.extractall()


def replace_in_file(path, old, new, missing_ok=False):
    if isinstance(old, type('')):
        old = old.encode('utf-8')
    if isinstance(new, type('')):
        new = new.encode('utf-8')
    with open(path, 'r+b') as f:
        raw = f.read()
        if isinstance(old, bytes):
            nraw = raw.replace(old, new)
        else:
            nraw = old.sub(new, raw)
        if raw == nraw and not missing_ok:
            raise ValueError('Failed (pattern not found) to patch: ' + path)
        f.seek(0), f.truncate()
        f.write(nraw)


def build_unix():
    if ismacos:
        with open('makefile', 'r+b') as m:
            raw = m.read().decode('utf-8')
            raw = raw.replace('libunrar.so', 'libunrar.dylib')
            m.seek(0), m.truncate()
            m.write(raw.encode('utf-8'))
    flags = '-fPIC ' + os.environ.get('CXXFLAGS', '')
    if ismacos:
        flags = '-std=c++11 ' + flags
    subprocess.check_call(['make', '-j4', 'lib', 'CXXFLAGS=%s' % flags.strip()])
    lib = 'libunrar.' + ('dylib' if ismacos else 'so')
    os.rename(lib, os.path.join(lib_dir, lib))
    print('Files in', lib_dir, os.listdir(lib_dir))


def find_msbuild():
    raw = download('https://github.com/kovidgoyal/bypy/raw/master/bypy/vcvars.py')
    open('vcvars.py', 'wb').write(raw)
    sys.path.insert(0, os.getcwd())
    from vcvars import find_msbuild as ans, query_vcvarsall
    vcvars_env = query_vcvarsall(True)
    del sys.path[0]
    vctools_ver = 'v' + vcvars_env['VCTOOLSVERSION'].replace('.', '')[:3]
    return ans(), vctools_ver, vcvars_env['WINDOWSSDKVERSION'].strip('\\')


def build_windows():
    PL = 'x64' if is64bit else 'Win32'
    msbuild, vctools_ver, sdk = find_msbuild()
    print('Using MSBuild:', msbuild)
    subprocess.check_call([
        msbuild, 'UnRARDll.vcxproj', '/t:Build', '/p:Platform=' + PL,
        '/p:Configuration=Release',
        '/p:PlatformToolset=' + vctools_ver,
        '/p:WindowsTargetPlatformVersion=' + sdk,
    ])
    lib = glob.glob('./build/*/Release/UnRAR.lib')[0]
    dll = glob.glob('./build/*/Release/UnRAR.dll')[0]
    shutil.copy2(lib, '../../lib')
    shutil.copy2(dll, '../../..')
    # check if unrar.dll loads
    import ctypes
    print(ctypes.CDLL(os.path.join(os.environ['UNRAR_DLL_DIR'], 'UnRAR.dll')))


def distutils_vcvars():
    from distutils.msvc9compiler import find_vcvarsall, get_build_version
    return find_vcvarsall(get_build_version())


def remove_dups(variable):
    old_list = variable.split(os.pathsep)
    new_list = []
    for i in old_list:
        if i not in new_list:
            new_list.append(i)
    return os.pathsep.join(new_list)


def query_process(cmd):
    if plat == 'amd64' and 'PROGRAMFILES(x86)' not in os.environ:
        os.environ['PROGRAMFILES(x86)'] = os.environ['PROGRAMFILES'] + ' (x86)'
    result = {}
    popen = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        stdout, stderr = popen.communicate()
        if popen.wait() != 0:
            raise RuntimeError(stderr.decode("mbcs"))

        stdout = stdout.decode("mbcs")
        for line in stdout.splitlines():
            if '=' not in line:
                continue
            line = line.strip()
            key, value = line.split('=', 1)
            key = key.lower()
            if key == 'path':
                if value.endswith(os.pathsep):
                    value = value[:-1]
                value = remove_dups(value)
            result[key] = value

    finally:
        popen.stdout.close()
        popen.stderr.close()
    return result


def query_vcvarsall():
    vcvarsall = distutils_vcvars()
    return query_process('"%s" %s & set' % (vcvarsall, plat))


def build_unrar():
    os.makedirs('sw/build'), os.makedirs('sw/include/unrar'), os.mkdir('sw/lib')
    os.chdir('sw/build')
    download_and_extract()
    os.chdir('unrar')
    replace_in_file('dll.cpp', 'WideToChar', 'WideToUtf')
    (build_windows if iswindows else build_unix)()
    for f in glob.glob('*.hpp'):
        shutil.copy2(f, '../../include/unrar')


def main():
    which = sys.argv[-1]
    if which == 'build':
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'psutil'])
        build_unrar()
    else:
        if iswindows:
            os.environ.update(query_vcvarsall())
        subprocess.check_call([sys.executable, 'setup.py', 'test'])


if __name__ == '__main__':
    main()

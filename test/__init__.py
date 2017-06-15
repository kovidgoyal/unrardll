#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: BSD Copyright: 2017, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import unittest

base = os.path.dirname(os.path.abspath(__file__))


class TestCase(unittest.TestCase):

    ae = unittest.TestCase.assertEqual
    longMessage = True
    tb_locals = True
    maxDiff = None

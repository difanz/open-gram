#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the CC-CEDICT importer."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from importers import cedict
from pipeline.lexicon_build import build_dict_utf8, default_dict_head


HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "testdata", "cedict.txt")


class CedictTests(unittest.TestCase):
    def test_fixture_writes_dict_full_lines(self):
        rows = cedict.load([FIXTURE])
        self.assertEqual(rows, [
            ("中国", ["zhong'guo"]),
            ("了", ["le", "liao"]),
            ("女", ["nv"]),
            ("略", ["lve"]),
            ("花儿", ["hua'er"]),
            ("儿", ["er"]),
            ("X光", ["x'guang"]),
        ])

    def test_bad_line_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not an entry\n")
            with self.assertRaises(ValueError):
                cedict.load([path])

    def test_dict_stage_keeps_the_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = os.path.join(tmp, "dict.full")
            numbered = os.path.join(tmp, "dict.utf8")
            count = cedict.convert([FIXTURE], full)
            self.assertEqual(count, 7)
            build_dict_utf8(full, numbered, default_dict_head())
            with open(numbered, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("中国 100 zhong'guo\n", text)
            self.assertIn("了 101 le liao\n", text)
            self.assertIn("女 102 nv\n", text)
            self.assertIn("略 103 lve\n", text)
            self.assertIn("花儿 104 hua'er\n", text)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local CC-CEDICT merge: new simplified surfaces only, no search frequency."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import merge_cedict  # noqa: E402
from fetch_cedict import parse_header, verify_text  # noqa: E402


CEDICT = textwrap.dedent("""\
    # CC-CEDICT
    #! entries=6
    #! license=https://creativecommons.org/licenses/by-sa/4.0/
    #! publisher=MDBG
    #! date=2026-09-25T08:54:32Z
    北京 北京 [Bei3 jing1] /Beijing/
    中國 中国 [Zhong1 guo2] /China/
    愛 爱 [ai4] /to love/
    行 行 [Xing2] /behavior/
    行 行 [Hang2] /row/
    行 行 [Hang2] /again, same reading/
    3D打印 3D打印 [san1 D da3 yin4] /3D print/
    㐀 㐀 [qiu1] /surname Qiu/
    """)


class MergeCedictTests(unittest.TestCase):
    def test_keeps_existing_rows_and_appends_new_simplified(self):
        existing = "北京 bei'jing\n㐀 qiu\n行 xing\n"
        merged, stats, samples = merge_cedict.merge_text(existing, CEDICT)
        self.assertTrue(merged.startswith(existing))
        self.assertEqual(
            merged,
            existing + "中国 zhong'guo\n爱 ai\n",
        )
        self.assertEqual(stats["converted"], 7)
        self.assertEqual(stats["skipped_normalize"], 1)
        self.assertEqual(stats["skipped_present_rows"], 5)
        self.assertEqual(stats["skipped_present_surfaces"], 3)
        self.assertEqual(stats["new_surfaces"], 2)
        self.assertEqual(stats["extra_readings"], 0)
        self.assertEqual(stats["repeated_new_readings"], 0)
        words = [word for word, _pinyin in samples]
        self.assertEqual(words, ["中国", "爱"])
        self.assertNotIn("愛", merged)
        self.assertNotIn("中國", merged)

    def test_joins_multiple_readings_of_a_new_surface(self):
        existing = "北京 bei'jing\n"
        merged, stats, _samples = merge_cedict.merge_text(existing, CEDICT)
        self.assertIn("行 xing hang\n", merged)
        self.assertEqual(stats["extra_readings"], 1)
        self.assertEqual(stats["multi_reading_surfaces"], 1)
        self.assertNotIn("freq", merged)
        # Android row is unchanged, so the extra CEDICT reading of 北京 is absent.
        self.assertEqual(merged.splitlines()[0], "北京 bei'jing")

    def test_second_merge_adds_nothing(self):
        existing = "北京 bei'jing\n"
        once, _stats, _samples = merge_cedict.merge_text(existing, CEDICT)
        twice, stats, samples = merge_cedict.merge_text(once, CEDICT)
        self.assertEqual(twice, once)
        self.assertEqual(stats["new_surfaces"], 0)
        self.assertEqual(samples, [])

    def test_matches_cedict_py_pinyin(self):
        cedict_py = os.path.join(HERE, "cedict.py")
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "cedict.txt")
            with open(src, "w", encoding="utf-8") as handle:
                handle.write(CEDICT)
            proc = subprocess.run(
                [sys.executable, cedict_py, "-d", src, "-o", "-"],
                check=True,
                capture_output=True,
                text=True,
            )
        merged, _stats, _samples = merge_cedict.merge_text("", CEDICT)
        from_cli = {}
        for line in proc.stdout.splitlines():
            word, pinyin = line.split(" ", 1)
            from_cli.setdefault(word, [])
            if pinyin not in from_cli[word]:
                from_cli[word].append(pinyin)
        from_merge = {}
        for line in merged.splitlines():
            word, pinyin = line.split(" ", 1)
            from_merge[word] = pinyin.split(" ")
        self.assertEqual(from_merge, from_cli)

    def test_numeric_syllable_is_not_a_pinyin(self):
        line = "雙11 双11 [Shuang1 11] /Singles' Day sale/\n"
        self.assertIsNone(merge_cedict.convert_line(line))

    def test_header_verification_accepts_a_consistent_dump(self):
        text = textwrap.dedent("""\
            # CC-CEDICT
            #! entries=1
            #! publisher=MDBG
            #! license=https://creativecommons.org/licenses/by-sa/4.0/
            #! date=2026-09-25T08:54:32Z
            你 你 [ni3] /you/
            """)
        meta = verify_text(text)
        self.assertEqual(meta["entries"], "1")
        self.assertEqual(parse_header(text)["date"], "2026-09-25T08:54:32Z")

    def test_header_verification_rejects_a_count_mismatch(self):
        text = textwrap.dedent("""\
            # CC-CEDICT
            #! entries=2
            #! publisher=MDBG
            #! license=https://creativecommons.org/licenses/by-sa/4.0/
            #! date=2026-09-25T08:54:32Z
            你 你 [ni3] /you/
            """)
        with self.assertRaises(ValueError):
            verify_text(text)

    def test_license_attributes_apache_and_cc_by_sa(self):
        path = os.path.join(ROOT, "data", "dict_license")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("Apache License", text)
        self.assertIn("CC-CEDICT", text)
        self.assertIn("CC BY-SA 4.0", text)
        self.assertIn("https://creativecommons.org/licenses/by-sa/4.0/", text)
        self.assertIn("MDBG", text)


if __name__ == "__main__":
    unittest.main()

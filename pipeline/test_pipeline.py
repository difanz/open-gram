#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the dict.utf8 and lm_sc.3gm.arpa frontend."""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline import arpa
from pipeline import ngram_count
from pipeline import segment
from pipeline import sources
from pipeline import wiki_text
from pipeline.lexicon_build import build_dict_utf8, default_dict_head
from pipeline.lexicon_io import Lexicon, load_dict_utf8
from pipeline.run import main


def _read(path, errors=None):
    with open(path, encoding="utf-8", errors=errors) as handle:
        return handle.read()


HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_XML = os.path.join(HERE, "testdata", "wiki.xml")
FIXTURE_DICT = os.path.join(HERE, "testdata", "dict.full")
DICT_HEAD = default_dict_head()
DATA_DICT = os.path.join(ROOT, "data", "dict.full")
SORT_ARPA = os.path.join(ROOT, "tools", "utils", "sort-arpa.py")


class FetchTests(unittest.TestCase):
    def test_local_name_is_the_url_basename(self):
        url = "https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles.xml.bz2"
        self.assertEqual(sources.local_name(url), "zhwiki-latest-pages-articles.xml.bz2")
        with self.assertRaises(ValueError):
            sources.local_name("https://dumps.wikimedia.org/zhwiki/latest/")


class WikiTextTests(unittest.TestCase):
    def test_fixture_keeps_article_sentences_only(self):
        sentences = list(wiki_text.iter_article_sentences(FIXTURE_XML))
        self.assertEqual(sentences, [
            "北京是中国的首都。",
            "我爱北京。",
            "电话是12。",
            "你好@北京。",
            "京城欢迎你。",
        ])
        blob = "\n".join(sentences)
        for marker in ("[[", "]]", "{{", "}}", "Category", "注", "不应", "REDIRECT", "参见"):
            self.assertNotIn(marker, blob)

    def test_traditional_without_opencc_is_refused(self):
        if shutil.which("opencc"):
            self.skipTest("opencc is installed; identity refusal is not the active path")
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "trad.txt")
            dst = os.path.join(tmp, "out.txt")
            with open(src, "w", encoding="utf-8") as handle:
                handle.write("他們。\n")
            with self.assertRaises(RuntimeError):
                wiki_text.simplify_file(src, dst)


class LexiconTests(unittest.TestCase):
    def test_fixture_ids_follow_add_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "dict.utf8")
            build_dict_utf8(FIXTURE_DICT, out, DICT_HEAD)
            lexicon = load_dict_utf8(out)
            self.assertEqual(lexicon.word_to_id["<unknown>"], 0)
            self.assertEqual(lexicon.word_to_id["<Digit>"], 20)
            self.assertEqual(lexicon.word_to_id["，"], 70)
            self.assertEqual(lexicon.word_to_id["北京"], 100)
            self.assertEqual(lexicon.word_to_id["欢迎"], 110)
            text = _read(out)
            self.assertIn("北京 100 bei'jing", text)
            self.assertNotIn("cedict", text.lower())

    def test_android_dict_full_matches_add_id(self):
        import importlib.util
        path = os.path.join(ROOT, "lexicon", "add_id.py")
        spec = importlib.util.spec_from_file_location("opengram_add_id", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            via_stage = os.path.join(tmp, "stage.utf8")
            via_add = os.path.join(tmp, "add.utf8")
            build_dict_utf8(DATA_DICT, via_stage, DICT_HEAD)
            module.main(DATA_DICT, via_add, DICT_HEAD)
            with open(via_stage, encoding="utf-8") as left, open(via_add, encoding="utf-8") as right:
                self.assertEqual(left.read(), right.read())


class SegmentTests(unittest.TestCase):
    def test_forward_maximum_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            dict_path = os.path.join(tmp, "dict.utf8")
            src = os.path.join(tmp, "sent.txt")
            dst = os.path.join(tmp, "seg.txt")
            build_dict_utf8(FIXTURE_DICT, dict_path, DICT_HEAD)
            with open(src, "w", encoding="utf-8") as handle:
                handle.write("北京是中国的首都。\n电话是12。\n你好@北京。\n京城欢迎你。\n")
            segment.segment_file(src, dst, dict_path, segmenter="match")
            with open(dst, encoding="utf-8") as handle:
                lines = [line.rstrip("\n") for line in handle]
            self.assertEqual(lines, [
                "北京/是/中国/的/首都/。",
                "电话/是/<Digit>/。",
                "你/好/<Simbol>/北京/。",
                "<unknown>/欢迎/你/。",
            ])

    def test_presegmented_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "pre.txt")
            dst = os.path.join(tmp, "seg.txt")
            with open(src, "w", encoding="utf-8") as handle:
                handle.write("我 爱 北京\n# comment\n你/好\n")
            segment.segment_file(src, dst, dict_utf8=None, segmenter="preseg")
            with open(dst, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "我/爱/北京\n你/好\n")

    def test_crf_hook_requires_a_model_and_does_not_vendor_crf(self):
        command = segment.crf_command("model.crf", "in.txt")
        self.assertIn(os.path.join("segment", "tagging", "baseseg.py"), command[1])
        self.assertIn("model.crf", command)
        with self.assertRaises(RuntimeError):
            segment.segment_with_crf("in.txt", "out.txt", None)
        with self.assertRaises(RuntimeError):
            segment.segment_with_crf("in.txt", "out.txt", os.path.join(HERE, "missing.model"))


class CountAndArpaTests(unittest.TestCase):
    def _lexicon(self):
        lexicon = Lexicon()
        for word, wid in (("<unknown>", 0), ("我", 100), ("爱", 101), ("北京", 102), ("中国", 103)):
            lexicon.add(word, wid)
        return lexicon

    def test_absolute_discount_matches_hand_count(self):
        lexicon = self._lexicon()
        wo, ai, bj, zg = (lexicon.require(w) for w in ("我", "爱", "北京", "中国"))
        counts = [dict() for _ in range(4)]
        counts[1] = {(wo,): 2, (ai,): 2, (bj,): 1, (zg,): 1}
        counts[2] = {(wo, ai): 2, (ai, bj): 1, (ai, zg): 1}
        counts[3] = {(wo, ai, bj): 1, (wo, ai, zg): 1}
        probs, bows = arpa.estimate_tables(counts, discount=0.5)
        self.assertTrue(math.isclose(probs[(wo,)], 2.0 / 6.0))
        self.assertTrue(math.isclose(probs[(wo, ai)], 0.75))
        self.assertTrue(math.isclose(bows[(wo,)], 0.375))
        self.assertTrue(math.isclose(bows[(ai,)], 0.75))
        self.assertTrue(math.isclose(bows[(bj,)], 1.0))
        self.assertTrue(math.isclose(probs[(wo, ai, bj)], 0.25))
        self.assertTrue(math.isclose(bows[(wo, ai)], 1.0))
        vocab = (wo, ai, bj, zg)
        total = sum(arpa.backoff_prob((wo, wid), probs, bows) for wid in vocab)
        self.assertTrue(math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-12))
        self.assertTrue(math.isclose(
            arpa.backoff_prob((wo, bj), probs, bows), 0.375 * (1.0 / 6.0)))

    def test_row_longer_than_slmpack_getline_is_rejected(self):
        word = "词" * 400
        with self.assertRaises(ValueError):
            arpa.format_entry((word, word, word), 0.5, None)

    def test_sharded_merge_matches_one_shard(self):
        ids = [(100, 101, 102), (100, 101, 103), (102,)]
        with tempfile.TemporaryDirectory() as tmp:
            wide = os.path.join(tmp, "wide")
            tiny = os.path.join(tmp, "tiny")
            for path, budget in ((wide, 1000), (tiny, 1)):
                counter = ngram_count.NgramCounter(os.path.join(path, "shards"), max_keys=budget)
                for sentence in ids:
                    counter.add_sentence(sentence)
                counter.close()
                ngram_count.merge_shards(os.path.join(path, "shards"), os.path.join(path, "merged"), 3)
            wide_counts = ngram_count.load_counts(os.path.join(wide, "merged"), 3)
            tiny_counts = ngram_count.load_counts(os.path.join(tiny, "merged"), 3)
            self.assertGreater(counter.shard_id, 1)
            self.assertEqual(wide_counts, tiny_counts)
            self.assertEqual(tiny_counts[2][(100, 101)], 2)
            self.assertNotIn((102, 100), tiny_counts[2])

    def test_streaming_arpa_matches_tables(self):
        lexicon = self._lexicon()
        wo, ai, bj, zg = (lexicon.require(w) for w in ("我", "爱", "北京", "中国"))
        with tempfile.TemporaryDirectory() as tmp:
            counter = ngram_count.NgramCounter(os.path.join(tmp, "shards"), max_keys=2)
            counter.add_sentence((wo, ai, bj))
            counter.add_sentence((wo, ai, zg))
            counter.close()
            merged = os.path.join(tmp, "merged")
            ngram_count.merge_shards(os.path.join(tmp, "shards"), merged, 3)
            counts = ngram_count.load_counts(merged, 3)
            probs, bows = arpa.estimate_tables(counts, discount=0.5)
            expected = arpa.parse_arpa(arpa.render_model(probs, bows, lexicon))
            # dict.utf8 for write_arpa.
            dict_path = os.path.join(tmp, "dict.utf8")
            with open(dict_path, "w", encoding="utf-8") as handle:
                for word, wid in (("我", wo), ("爱", ai), ("北京", bj), ("中国", zg), ("<unknown>", 0)):
                    handle.write("%s %d\n" % (word, wid))
            arpa_path = os.path.join(tmp, "lm_sc.3gm.arpa")
            arpa.write_arpa(merged, dict_path, arpa_path, discount=0.5)
            got = arpa.parse_arpa(_read(arpa_path))
            self.assertEqual(
                [entry[0] for entry in got["levels"][1]],
                [entry[0] for entry in expected["levels"][1]],
            )
            for level in (1, 2, 3):
                for left, right in zip(got["levels"][level], expected["levels"][level]):
                    self.assertEqual(left[0], right[0])
                    self.assertTrue(math.isclose(left[1], right[1], rel_tol=1e-12, abs_tol=1e-15))
                    if left[2] is None:
                        self.assertIsNone(right[2])
                    else:
                        self.assertTrue(math.isclose(left[2], right[2], rel_tol=1e-12, abs_tol=1e-15))


class PipelineTests(unittest.TestCase):
    def test_fixture_publishes_dict_and_arpa(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "all",
                "--xml", FIXTURE_XML,
                "--dict-full", FIXTURE_DICT,
                "--dict-head", DICT_HEAD,
                "--work", tmp,
                "--max-keys", "3",
                "--surface",
            ])
            self.assertEqual(code, 0)
            dict_path = os.path.join(tmp, "dict.utf8")
            arpa_path = os.path.join(tmp, "lm_sc.3gm.arpa")
            sentences = _read(os.path.join(tmp, "sentences.txt"))
            segmented = _read(os.path.join(tmp, "segmented.txt"))
            self.assertIn("北京/是/中国/的/首都/。", segmented)
            self.assertNotIn("[[", sentences)
            lexicon = load_dict_utf8(dict_path)
            self.assertEqual(lexicon.word_to_id["北京"], 100)
            parsed = arpa.parse_arpa(_read(arpa_path))
            self.assertGreater(parsed["root"][1], 0.0)
            for level in (1, 2, 3):
                self.assertGreater(len(parsed["levels"][level]), 0)
                ids = [tuple(lexicon.word_to_id[w] for w in entry[0])
                       for entry in parsed["levels"][level]]
                self.assertEqual(ids, sorted(ids))
                for words, pr, bow in parsed["levels"][level]:
                    self.assertEqual(len(words), level)
                    self.assertGreater(pr, 0.0)
                    if level < 3:
                        self.assertIsNotNone(bow)
                    else:
                        self.assertIsNone(bow)
            self.assertIn("<Digit>", segmented)
            self.assertEqual(main([
                "all", "--xml", FIXTURE_XML, "--dict-full", FIXTURE_DICT,
                "--work", tmp, "--segmenter", "preseg",
            ]), 1)
            self.assertIn("<unknown>", segmented)
            surface = _read(os.path.join(tmp, "counts.tsv"))
            self.assertIn("北京", surface)
            shards = os.listdir(os.path.join(tmp, "counts", "shards"))
            self.assertGreater(len(shards), 1)

            sorted_proc = subprocess.run(
                [sys.executable, SORT_ARPA, dict_path],
                input=_read(arpa_path),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(sorted_proc.returncode, 0, sorted_proc.stderr)
            resorted = arpa.parse_arpa(sorted_proc.stdout)
            self.assertEqual(
                [entry[0] for entry in parsed["levels"][2]],
                [entry[0] for entry in resorted["levels"][2]],
            )

    def test_arpa_headers_are_the_slmpack_dialect(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main([
                "all", "--xml", FIXTURE_XML, "--dict-full", FIXTURE_DICT,
                "--dict-head", DICT_HEAD, "--work", tmp, "--max-keys", "8",
            ]), 0)
            text = _read(os.path.join(tmp, "lm_sc.3gm.arpa"))
            self.assertIn("\\0-gram\\1\n", text)
            self.assertNotIn("\\data\\", text)
            self.assertNotIn("-grams:", text)
            self.assertNotIn("\\end\\", text)


def _without_broken_last_trigram(text):
    """Drop the last trigram row of an ``slminfo -v -p`` dump.

    That row's history is empty because the bigram sentinel child is 0.
    Returns ``(parseable_text, broken_line)``.
    """
    lines = text.splitlines()
    header_at = None
    for index, line in enumerate(lines):
        if line.startswith("\\3-gram\\"):
            header_at = index
            break
    if header_at is None:
        raise AssertionError("slminfo dump has no \\3-gram section")
    size = int(lines[header_at].split("\\")[-1])
    if size < 1:
        raise AssertionError("\\3-gram section is empty")
    last = header_at + size
    if last >= len(lines):
        raise AssertionError("\\3-gram\\%d overruns the dump" % size)
    broken = lines[last]
    if not broken[:1].isspace():
        raise AssertionError("expected an empty-history last trigram, got %r" % (broken,))
    lines[header_at] = "\\3-gram\\%d" % (size - 1)
    del lines[last]
    if text.endswith("\n"):
        return "\n".join(lines) + "\n", broken
    return "\n".join(lines), broken


@unittest.skipUnless(shutil.which("slmpack") and shutil.which("slminfo"),
                     "sunpinyin-utils not installed")
class SlmpackTests(unittest.TestCase):
    def test_fixture_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main([
                "all", "--xml", FIXTURE_XML, "--dict-full", FIXTURE_DICT,
                "--dict-head", DICT_HEAD, "--work", tmp, "--max-keys", "4",
            ]), 0)
            arpa_path = os.path.join(tmp, "lm_sc.3gm.arpa")
            dict_path = os.path.join(tmp, "dict.utf8")
            slm_path = os.path.join(tmp, "lm.slm")
            original = arpa.parse_arpa(_read(arpa_path))
            packed = subprocess.run(
                ["slmpack", arpa_path, dict_path, slm_path],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(packed.returncode, 0, packed.stderr)
            self.assertNotIn("not found in lexicon", packed.stderr)
            info = subprocess.run(
                ["slminfo", "-v", "-p", "-l", dict_path, slm_path],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(info.returncode, 0, info.stderr)
            # slmpack 3.0.0~rc2 sets the non-leaf sentinel child from
            # getLevel(lvl+1).size(). Trigrams are stored in m_lastLevel, so
            # getLevel(3).size() is 0 and slminfo prints the last leaf with
            # an empty history. Drop that row before parsing. Its probability
            # is still the leaf probability.
            parseable, broken = _without_broken_last_trigram(info.stdout)
            dumped = arpa.parse_arpa(parseable)
            self.assertTrue(math.isclose(
                dumped["root"][1], original["root"][1], rel_tol=1e-5, abs_tol=1e-12))
            self.assertTrue(math.isclose(
                dumped["root"][2], original["root"][2], rel_tol=1e-5, abs_tol=1e-12))
            for level in (1, 2, 3):
                got_rows = dumped["levels"][level]
                exp_rows = original["levels"][level]
                if level == 3:
                    exp_rows = exp_rows[:-1]
                self.assertEqual([row[0] for row in got_rows], [row[0] for row in exp_rows])
                for got, exp in zip(got_rows, exp_rows):
                    self.assertTrue(math.isclose(got[1], exp[1], rel_tol=1e-5, abs_tol=1e-6))
                    if got[2] is None:
                        self.assertIsNone(exp[2])
                    else:
                        self.assertTrue(math.isclose(got[2], exp[2], rel_tol=1e-5, abs_tol=1e-6))
            last = original["levels"][3][-1]
            self.assertGreater(len(original["levels"][3]), 1)
            self.assertEqual(broken.split()[0], last[0][-1])
            self.assertTrue(math.isclose(
                float(broken.split()[-1]), last[1], rel_tol=1e-5, abs_tol=1e-6))
            if shutil.which("slmthread") and shutil.which("tslmendian"):
                threaded = os.path.join(tmp, "lm.thread")
                t3g = os.path.join(tmp, "lm.t3g")
                self.assertEqual(subprocess.run(
                    ["slmthread", slm_path, threaded],
                    capture_output=True, text=True, check=False,
                ).returncode, 0)
                self.assertEqual(subprocess.run(
                    ["tslmendian", "-i", threaded, "-o", t3g, "-e", "le"],
                    capture_output=True, text=True, check=False,
                ).returncode, 0)
                self.assertGreater(os.path.getsize(t3g), 0)
                if shutil.which("genpyt"):
                    pydict = os.path.join(tmp, "pydict.bin")
                    log = os.path.join(tmp, "genpyt.log")
                    env = os.environ.copy()
                    env["LC_ALL"] = "zh_CN.UTF-8"
                    env["LANG"] = "zh_CN.UTF-8"
                    packed_py = subprocess.run(
                        ["genpyt", "-i", dict_path, "-o", pydict, "-l", log,
                         "-s", t3g, "-e", "le"],
                        capture_output=True, text=True, check=False, env=env,
                    )
                    detail = packed_py.stderr
                    if os.path.exists(log):
                        detail += _read(log, errors="replace")
                    self.assertEqual(packed_py.returncode, 0, detail)
                    self.assertGreater(os.path.getsize(pydict), 0)


if __name__ == "__main__":
    unittest.main()

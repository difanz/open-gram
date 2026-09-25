#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the Wikimedia pages-articles importer."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from importers import wikimedia


HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_XML = os.path.join(HERE, "testdata", "wiki.xml")


class WikimediaTests(unittest.TestCase):
    def test_fixture_keeps_namespace_zero_articles(self):
        sentences = list(wikimedia.iter_article_sentences(FIXTURE_XML))
        self.assertEqual(sentences, [
            "北京是中国的首都。",
            "我爱北京。",
            "电话是12。",
            "你好@北京。",
            "== 参见 ==",
            "[[北京|京城]]欢迎你。",
            "{{note|忽略}}",
            "[[Category:地理]]",
            "注",
        ])

    def test_opencc_simplifies_traditional_han(self):
        xml = (
            "<mediawiki><page><ns>0</ns><revision>"
            "<text>他們愛北京。</text>"
            "</revision></page></mediawiki>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "t.xml")
            with open(src, "w", encoding="utf-8") as handle:
                handle.write(xml)
            self.assertEqual(
                list(wikimedia.iter_article_sentences(src)),
                ["他们爱北京。"],
            )


if __name__ == "__main__":
    unittest.main()

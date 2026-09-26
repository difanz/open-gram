#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the Wikimedia pages-articles importer."""

import os
import sys
import tempfile
import unittest
import xml.sax.saxutils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from importers import wikimedia
from importers.wikitext import remove_wiki_tables


HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_XML = os.path.join(HERE, "testdata", "wiki.xml")


def _page(body):
    # Dumps store wikitext as escaped character data, not child elements.
    escaped = xml.sax.saxutils.escape(body)
    return (
        "<mediawiki><page><ns>0</ns><revision><text>%s</text>"
        "</revision></page></mediawiki>" % escaped
    )


def _sentences(body):
    xml = _page(body)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "t.xml")
        with open(src, "w", encoding="utf-8") as handle:
            handle.write(xml)
        return list(wikimedia.iter_article_sentences(src))


class WikimediaTests(unittest.TestCase):
    def test_fixture_keeps_namespace_zero_articles(self):
        sentences = list(wikimedia.iter_article_sentences(FIXTURE_XML))
        self.assertEqual(sentences, [
            "北京是中国的首都。",
            "我爱北京。",
            "电话是12。",
            "你好@北京。",
            "参见",
            "京城欢迎你。",
        ])

    def test_opencc_simplifies_traditional_han(self):
        self.assertEqual(_sentences("他們愛北京。"), ["他们爱北京。"])

    def test_nested_template_and_ref_drop_their_insides(self):
        body = "前文{{note|外{{inner|噪声}}}}。<ref>注釋</ref>他們愛北京。"
        self.assertEqual(_sentences(body), ["前文。", "他们爱北京。"])

    def test_wikilink_keeps_the_label_and_drops_file_links(self):
        body = "[[北京]]与[[北京|京城]]欢迎你。见[[File:a.jpg|thumb|图注]]此。[[Category:地理]]"
        self.assertEqual(_sentences(body), ["北京与京城欢迎你。", "见此。"])

    def test_external_link_keeps_the_label(self):
        body = "见[https://example.com/a 例子]完。裸 https://example.com/b 址。"
        self.assertEqual(_sentences(body), ["见例子完。", "裸 址。"])

    def test_heading_and_bold_keep_the_words(self):
        body = "== 参见 ==\n'''数学'''是一门学科。"
        self.assertEqual(_sentences(body), ["参见", "数学是一门学科。"])

    def test_lang_template_keeps_han_and_drops_foreign(self):
        body = "称为{{lang|zh-cn|简体字}}。希腊词{{lang|grc|μάθημα}}在此。"
        self.assertEqual(_sentences(body), ["称为简体字。", "希腊词在此。"])

    def test_table_is_removed_and_unclosed_table_is_not(self):
        self.assertEqual(
            remove_wiki_tables("前{| class=\"x\"\n|a||b\n|}后"),
            "前后",
        )
        self.assertIn("后文", remove_wiki_tables("前{|\n|未闭合\n后文"))
        self.assertEqual(_sentences("表前。{| class=\"wikitable\"\n|甲||乙\n|}表后。"), ["表前。", "表后。"])

    def test_language_converter_keeps_the_simplified_variant(self):
        body = "探讨于-{zh-cn:域;zh-tw:体}-。计-{}-算机。-{于}-尔班。"
        self.assertEqual(_sentences(body), ["探讨于域。", "计算机。", "于尔班。"])

    def test_english_only_line_is_dropped(self):
        self.assertEqual(_sentences("See also.\n北京。"), ["北京。"])

    def test_pipe_in_prose_stays_and_a_bare_pipe_line_does_not(self):
        kept = _sentences("竖杠|用来分隔，这是说明。")
        self.assertEqual(kept, ["竖杠|用来分隔，这是说明。"])
        self.assertEqual(_sentences("经济学 | 4THINK\n下一句是北京。"), ["下一句是北京。"])


if __name__ == "__main__":
    unittest.main()

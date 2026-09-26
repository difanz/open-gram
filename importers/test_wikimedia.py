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
            "你好@北京。",
            "京城欢迎你。",
        ])

    def test_opencc_simplifies_traditional_han(self):
        self.assertEqual(_sentences("他們愛北京。"), ["他们爱北京。"])

    def test_nested_template_and_ref_drop_their_insides(self):
        body = "前文介绍已经写完{{note|外{{inner|噪声}}}}。<ref>注釋</ref>他們愛北京。"
        self.assertEqual(_sentences(body), ["前文介绍已经写完。", "他们爱北京。"])

    def test_wikilink_keeps_plain_chinese_and_drops_the_rest(self):
        body = "[[北京]]与[[北京|京城]]欢迎你。见[[File:a.jpg|thumb|图注]]此页说明。[[Math]]不会留下。"
        self.assertEqual(_sentences(body), ["北京与京城欢迎你。", "见此页说明。", "不会留下。"])

    def test_external_link_keeps_only_a_chinese_label(self):
        body = "见[https://example.com/a 例子]完成。另见[https://example.com/b English]此处说明。"
        self.assertEqual(_sentences(body), ["见例子完成。", "另见此处说明。"])

    def test_heading_without_a_stop_is_dropped(self):
        body = "== 参见 ==\n'''数学'''是一门学科。"
        self.assertEqual(_sentences(body), ["数学是一门学科。"])

    def test_templates_are_dropped_wholesale(self):
        body = "称为{{lang|zh-cn|简体字}}这个概念。希腊词{{lang|grc|μάθημα}}在这句话里面。"
        self.assertEqual(_sentences(body), ["称为这个概念。", "希腊词在这句话里面。"])

    def test_table_is_removed_and_unclosed_table_is_not(self):
        self.assertEqual(
            remove_wiki_tables("前{| class=\"x\"\n|a||b\n|}后"),
            "前后",
        )
        self.assertIn("后文", remove_wiki_tables("前{|\n|未闭合\n后文"))
        self.assertEqual(
            _sentences("表格前面的文字。{| class=\"wikitable\"\n|甲||乙\n|}表格后面的文字。"),
            ["表格前面的文字。", "表格后面的文字。"],
        )

    def test_converter_rules_are_deleted(self):
        body = "这是在探讨-{zh-cn:域;zh-tw:体}-这个概念。计算机-{}-科学是一门重要学科。"
        self.assertEqual(_sentences(body), ["这是在探讨这个概念。", "计算机科学是一门重要学科。"])

    def test_english_only_line_is_dropped(self):
        self.assertEqual(_sentences("See also.\n这里只留下中文。"), ["这里只留下中文。"])

    def test_pipe_sentence_is_dropped(self):
        body = "竖杠|用来分隔，这是说明。下一句是北京的说明文字。"
        self.assertEqual(_sentences(body), ["下一句是北京的说明文字。"])


if __name__ == "__main__":
    unittest.main()

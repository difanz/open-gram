#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the lawdb.cncourt.org scraper and normalizer."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from importers import lawdb


HERE = os.path.dirname(os.path.abspath(__file__))
SHOW = os.path.join(HERE, "testdata", "lawdb_show.html")
LISTING = os.path.join(HERE, "testdata", "lawdb_list.html")


class LawdbTests(unittest.TestCase):
    def test_document_becomes_sentences(self):
        with open(SHOW, encoding="utf-8") as handle:
            page = handle.read()
        self.assertEqual(lawdb.document_sentences(page), [
            "【发布单位】全国人民代表大会",
            "【发布文号】-----------",
            "测试法",
            "第一章 总 则",
            "第一条 为了测试。",
            "第二条 本法所称测试，包括：",
            "（一）甲；",
            "（二）乙。",
            "内层条文。",
            "第二章 附 则",
            "第三条 本法自公布之日起施行。",
        ])
        self.assertNotIn("本库所有资料", lawdb.normalize(page))

    def test_listing_fids_and_page_count(self):
        with open(LISTING, encoding="utf-8") as handle:
            page = handle.read()
        fids, pages = lawdb.parse_listing(page)
        self.assertEqual(fids, [10, 11])
        self.assertEqual(pages, 2)

    def test_short_keyword_is_an_error(self):
        with self.assertRaises(ValueError):
            lawdb.parse_listing("关键词太短，请输入至少两个汉字。")

    def test_search_walks_pages_once(self):
        with open(LISTING, encoding="utf-8") as handle:
            first = handle.read()
        pages = {
            1: first,
            2: "符合条件纪录： 3条，共2页。<a href='show.php?fid=12'>丙</a>",
        }

        def fetch(url):
            query = url.split("?", 1)[1]
            page = 1
            for part in query.split("&"):
                if part.startswith("page="):
                    page = int(part.split("=", 1)[1])
            return pages[page]

        found = list(lawdb.iter_search_fids(
            fetch, lawdb.DEFAULT_BASE, "1", "测试", "title", 1949, 2026, 0, [0],
        ))
        self.assertEqual(found, [10, 11, 12])

    def test_keywords_are_gb2312(self):
        url = lawdb.listing_url(
            lawdb.DEFAULT_BASE, "1", "人民", "title", 1949, 2026, 1,
        )
        self.assertIn("keywords=%C8%CB%C3%F1", url)

    def test_gb18030_page_decodes(self):
        self.assertEqual(lawdb.decode_page("测试".encode("gb2312")), "测试")

    def test_newline_inside_a_character_is_joined(self):
        # 、 is the two bytes A1 A2. show.php sometimes wraps between them.
        raw = "前文".encode("gb2312") + b"\xa1\n\xa2" + "后文。".encode("gb2312")
        self.assertIn("前文、后文。", lawdb.decode_page(raw))

    def test_one_bad_byte_keeps_the_rest(self):
        raw = "甲".encode("gb2312") + b"\xff" + "乙。".encode("gb2312")
        text = lawdb.decode_page(raw)
        self.assertIn("甲", text)
        self.assertIn("乙。", text)

    def test_scrape_writes_local_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "laws.txt")
            count = lawdb.scrape(dest, html_paths=[SHOW], delay=0)
            self.assertEqual(count, 11)
            with open(dest, encoding="utf-8") as handle:
                self.assertTrue(handle.readline().startswith("【发布单位】"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checks for the zhwiki sample fetch. No network."""

import bz2
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from importers.fetch_zhwiki_sample import (
    decompress_members,
    limit_ns0,
    member_bounds,
    parse_index,
)


def _page(ns, text, redirect=False):
    redir = "<redirect title='X' />" if redirect else ""
    return (
        "<page><ns>%d</ns>%s<revision><text>%s</text></revision></page>"
        % (ns, redir, text)
    ).encode("utf-8")


class FetchZhwikiSampleTests(unittest.TestCase):
    def test_parse_index_keeps_colons_in_the_title(self):
        rows = parse_index("606:13:数学\n1430116:26:Wikipedia:繁简分歧词表\n")
        self.assertEqual(rows, [
            (606, 13, "数学"),
            (1430116, 26, "Wikipedia:繁简分歧词表"),
        ])

    def test_member_bounds_include_the_header_and_file_end(self):
        rows = parse_index("100:1:甲\n100:2:乙\n250:3:丙\n")
        self.assertEqual(member_bounds(rows, 400), [(0, 100), (100, 250), (250, 400)])

    def test_limit_keeps_earlier_non_articles_and_drops_a_partial_page(self):
        xml = b"".join([
            b"<mediawiki>\n<siteinfo><sitename>Wikipedia</sitename></siteinfo>\n",
            _page(4, "项目页。"),
            _page(0, "#重定向 [[甲]]", redirect=True),
            _page(0, "他们爱北京。"),
            _page(0, "第二条。"),
            "<page><ns>0</ns><revision><text>截断".encode("utf-8"),
        ])
        kept, stats = limit_ns0(xml, 1)
        self.assertEqual(stats, {"pages": 3, "ns0": 1})
        self.assertTrue(kept.endswith(b"</mediawiki>\n"))
        self.assertIn("他们爱北京".encode("utf-8"), kept)
        self.assertNotIn("第二条".encode("utf-8"), kept)
        self.assertNotIn("截断".encode("utf-8"), kept)
        self.assertIn(b"<ns>4</ns>", kept)

    def test_page_cap_stops_before_enough_articles(self):
        xml = b"<mediawiki>\n" + _page(0, "一。") + _page(0, "二。")
        kept, stats = limit_ns0(xml, 5, max_pages=1)
        self.assertEqual(stats, {"pages": 1, "ns0": 1})
        self.assertNotIn("二".encode("utf-8"), kept)

    def test_decompress_members_accepts_a_concatenation(self):
        parts = [b"<mediawiki>\n", _page(0, "甲。")]
        chunks = [bz2.compress(part) for part in parts]
        blob = b"".join(chunks)
        bounds = []
        start = 0
        for chunk in chunks:
            bounds.append((start, start + len(chunk)))
            start += len(chunk)
        self.assertEqual(decompress_members(blob, bounds), b"".join(parts))


if __name__ == "__main__":
    unittest.main()

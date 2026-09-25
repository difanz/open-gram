# -*- coding: utf-8 -*-
"""Wikimedia pages-articles XML to a sentence corpus.

The n-gram driver reads that corpus and does not parse XML. HTML in the
article text is stripped with bleach. Traditional Han is converted with
opencc (t2s). The input is uncompressed XML. Another source gets its own
script in this directory.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET

import bleach
import opencc

_TERMINATORS = set("。！？")
_OPENCC = opencc.OpenCC("t2s")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _child_text(elem, name):
    for child in list(elem):
        if _local(child.tag) == name:
            return child.text or ""
    return ""


def _revision_text(page):
    for child in list(page):
        if _local(child.tag) != "revision":
            continue
        return _child_text(child, "text")
    return ""


def _is_redirect(page, text):
    for child in list(page):
        if _local(child.tag) == "redirect":
            return True
    head = text.lstrip()[:16].lower()
    return head.startswith("#redirect") or head.startswith("#重定向")


def normalize(text):
    """Strip HTML, then convert traditional Han to simplified Han."""
    plain = bleach.clean(text, tags=[], strip=True)
    return _OPENCC.convert(plain)


def iter_sentences(text):
    """One sentence per yield. 。！？ and a paragraph break end a sentence."""
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        buf = []
        for ch in paragraph:
            buf.append(ch)
            if ch in _TERMINATORS:
                sentence = "".join(buf).strip()
                buf = []
                if sentence:
                    yield sentence
        tail = "".join(buf).strip()
        if tail:
            yield tail


def iter_article_sentences(path):
    """Namespace 0 pages, redirects omitted."""
    handle = open(path, "rb")
    try:
        root = None
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if root is None and event == "start":
                root = elem
                continue
            if event != "end" or _local(elem.tag) != "page":
                continue
            ns = _child_text(elem, "ns").strip()
            text = _revision_text(elem)
            keep = ns == "0" and text and not _is_redirect(elem, text)
            if keep:
                for sentence in iter_sentences(normalize(text)):
                    yield sentence
            elem.clear()
            if root is not None:
                root.clear()
    finally:
        handle.close()


def convert(paths, output):
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    count = 0
    with open(output, "w", encoding="utf-8") as out:
        for path in paths:
            for sentence in iter_article_sentences(path):
                out.write(sentence)
                out.write("\n")
                count += 1
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="importers.wikimedia")
    parser.add_argument("--xml", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        count = convert(args.xml, args.output)
    except (OSError, ValueError) as exc:
        sys.stderr.write("open-gram: %s\n" % exc)
        return 1
    sys.stderr.write("sentences %d\n%s\n" % (count, args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())

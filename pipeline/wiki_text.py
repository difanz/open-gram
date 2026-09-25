# -*- coding: utf-8 -*-
"""Pages-articles XML to one sentence per line.

Namespace 0 only; redirects are dropped. Markup is stripped far enough
for the segmenter to see prose: comments, refs, templates, tables,
categories, and file links are removed, and ordinary links keep their
visible label. This is not a MediaWiki renderer.

A sentence ends at 。！？ or at a paragraph break. Ideographic comma stays
in the sentence; the trigram model treats ， as a word. The caller supplies
text that is already in the lexicon's script.
"""

from __future__ import annotations

import bz2
import gzip
import os
import re
import xml.etree.ElementTree as ET

_TERMINATORS = set("。！？")
_LINK_DROP_PREFIX = frozenset((
    "file", "image", "category", "wikipedia", "template", "media",
    "文件", "档案", "檔案", "分类", "分類", "模板", "维基", "維基",
))


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _open_binary(path):
    if path.endswith(".bz2"):
        return bz2.open(path, "rb")
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


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


def _strip_balanced(text, open_mark, close_mark):
    out = []
    i = 0
    n = len(text)
    olen = len(open_mark)
    clen = len(close_mark)
    while i < n:
        if text.startswith(open_mark, i):
            depth = 0
            j = i
            while j < n:
                if text.startswith(open_mark, j):
                    depth += 1
                    j += olen
                elif text.startswith(close_mark, j):
                    depth -= 1
                    j += clen
                    if depth == 0:
                        break
                else:
                    j += 1
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _replace_links(text):
    def repl(match):
        inner = match.group(1).strip()
        target = inner.split("|", 1)[0].strip()
        prefix = target.split(":", 1)[0].strip().lower()
        if ":" in target and prefix in _LINK_DROP_PREFIX:
            return ""
        if "|" in inner:
            return inner.rsplit("|", 1)[-1]
        return target.split("#", 1)[0]
    return re.sub(r"\[\[(.*?)\]\]", repl, text, flags=re.DOTALL)


def strip_markup(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref\b[^>]*/>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = _strip_balanced(text, "{{", "}}")
    text = _strip_balanced(text, "{|", "|}")
    text = _replace_links(text)
    text = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\[https?://[^\]]+\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^(=+)\s*(.*?)\s*\1\s*$", "", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"(?m)^[*#:;]+\s*", "", text)
    text = text.replace("__TOC__", "").replace("__NOTOC__", "")
    return text


def iter_sentences(text):
    for paragraph in strip_markup(text).splitlines():
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
    handle = _open_binary(path)
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
                for sentence in iter_sentences(text):
                    yield sentence
            elem.clear()
            if root is not None:
                root.clear()
    finally:
        handle.close()


def extract_dumps(paths, output):
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


def dumps_to_sentences(paths, output):
    return extract_dumps(paths, output)

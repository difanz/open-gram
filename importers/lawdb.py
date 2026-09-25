# -*- coding: utf-8 -*-
"""lawdb.cncourt.org statutes to a sentence corpus.

The site is the Supreme People's Court legal library. Statute text is
fetched from show.php and listing pages from index.php. Pages are
gb2312; they are decoded as gb18030. HTML in the document table is
stripped with bleach, then traditional Han is converted with opencc
(t2s). 。！？ and a paragraph break end a sentence.

The script does not download the whole library unless a fid range or a
search is given. An empty fid still returns the document table and
yields no sentences.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import bleach
import opencc


DEFAULT_BASE = "http://lawdb.cncourt.org"
_TERMINATORS = set("。！？")
_OPENCC = opencc.OpenCC("t2s")
_USER_AGENT = "open-gram/lawdb"
_FID = re.compile(r"show\.php\?fid=(\d+)", re.IGNORECASE)
_PAGES = re.compile(r"符合条件纪录：\s*(\d+)条，共(\d+)页")
_TABLE = re.compile(r"<table\b[^>]*\bwidth\s*=\s*[\"']?760", re.IGNORECASE)
_TOO_SHORT = "关键词太短"


def decode_page(data):
    """gb2312 pages, decoded with the gb18030 superset."""
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError as exc:
        raise ValueError("page is not gb18030") from exc


def _table_slice(page):
    match = _TABLE.search(page)
    if match is None:
        raise ValueError("not a lawdb document page")
    low = page.lower()
    start = match.start()
    i = start
    depth = 0
    while True:
        nxt_open = low.find("<table", i)
        nxt_close = low.find("</table>", i)
        if nxt_close < 0:
            raise ValueError("unclosed document table")
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + len("<table")
            continue
        depth -= 1
        i = nxt_close + len("</table>")
        if depth == 0:
            return page[start:i]


def _plain_lines(fragment):
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:div|p|tr|li|h\d|font)>", "\n", fragment)
    plain = html.unescape(bleach.clean(fragment, tags=[], strip=True))
    plain = _OPENCC.convert(plain)
    lines = []
    for line in plain.splitlines():
        line = line.replace("\u3000", " ").replace("\xa0", " ").replace("\u2003", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize(page):
    """Document table to plain text, one paragraph per line."""
    return _plain_lines(_table_slice(page))


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


def document_sentences(page):
    return list(iter_sentences(normalize(page)))


def parse_listing(page):
    """Return ``(fids, page_count)``. page_count is None when the page is not a search."""
    if _TOO_SHORT in page:
        raise ValueError("keyword must be at least two Han characters")
    fids = []
    seen = set()
    for match in _FID.finditer(page):
        fid = int(match.group(1))
        if fid not in seen:
            seen.add(fid)
            fids.append(fid)
    found = _PAGES.search(page)
    pages = int(found.group(2)) if found else None
    return fids, pages


def listing_url(base, type_, keywords, sobj, start_year, end_year, page):
    params = {
        "page": str(page),
        "type": type_,
        "sobj": sobj,
        "start_year": str(start_year),
        "end_year": str(end_year),
        "keywords": keywords,
    }
    try:
        query = urllib.parse.urlencode(params, encoding="gb2312")
    except UnicodeEncodeError as exc:
        raise ValueError("keywords are not gb2312: %s" % keywords) from exc
    return base.rstrip("/") + "/index.php?" + query


def show_url(base, fid):
    return base.rstrip("/") + "/show.php?fid=%d" % int(fid)


def latest_url(base, type_):
    try:
        query = urllib.parse.urlencode({"type": type_}, encoding="gb2312")
    except UnicodeEncodeError as exc:
        raise ValueError("type is not gb2312: %s" % type_) from exc
    return base.rstrip("/") + "/index.php?" + query


def fetch_text(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise OSError("%s: HTTP %s" % (url, exc.code)) from exc
    except urllib.error.URLError as exc:
        raise OSError("%s: %s" % (url, exc.reason)) from exc
    return decode_page(data)


def _pause(calls, delay):
    if calls[0] and delay:
        time.sleep(delay)
    calls[0] += 1


def iter_search_fids(fetch, base, type_, keywords, sobj, start_year, end_year, delay, calls):
    seen = set()
    page = 1
    while True:
        _pause(calls, delay)
        url = listing_url(base, type_, keywords, sobj, start_year, end_year, page)
        fids, pages = parse_listing(fetch(url))
        fresh = [fid for fid in fids if fid not in seen]
        if not fresh:
            break
        for fid in fresh:
            seen.add(fid)
            yield fid
        if pages is not None and page >= pages:
            break
        page += 1


def _iter_fids(fetch, base, fids, fid_from, fid_to, keywords, latest, type_,
               sobj, start_year, end_year, delay, calls):
    seen = set()

    def emit(fid):
        fid = int(fid)
        if fid in seen:
            return False
        seen.add(fid)
        return True

    for fid in fids:
        if emit(fid):
            yield fid
    if fid_from is not None:
        for fid in range(fid_from, fid_to + 1):
            if emit(fid):
                yield fid
    if latest:
        _pause(calls, delay)
        listed, _pages = parse_listing(fetch(latest_url(base, type_)))
        for fid in listed:
            if emit(fid):
                yield fid
    if keywords:
        for fid in iter_search_fids(
            fetch, base, type_, keywords, sobj, start_year, end_year, delay, calls
        ):
            if emit(fid):
                yield fid


def scrape(output, html_paths=(), fids=(), fid_from=None, fid_to=None,
           keywords=None, latest=False, type_="1", sobj="title",
           start_year=1949, end_year=2026, delay=0.2, base=DEFAULT_BASE,
           timeout=30, fetch=None):
    """Write sentences. Local HTML is read as UTF-8. HTTP pages are gb18030."""
    if (fid_from is None) != (fid_to is None):
        raise ValueError("fid-from and fid-to are both required")
    if fid_from is not None and fid_from > fid_to:
        raise ValueError("fid-from is after fid-to")
    sources = html_paths or fids or fid_from is not None or keywords or latest
    if not sources:
        raise ValueError("pass --html, --fid, --fid-from/--fid-to, --keywords, or --latest")
    if fetch is None:
        fetch = lambda url: fetch_text(url, timeout)

    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    count = 0
    calls = [0]
    with open(output, "w", encoding="utf-8") as out:
        for path in html_paths:
            with open(path, "r", encoding="utf-8") as handle:
                page = handle.read()
            n = 0
            for sentence in document_sentences(page):
                out.write(sentence)
                out.write("\n")
                n += 1
            count += n
            sys.stderr.write("%s sentences %d\n" % (path, n))
        for fid in _iter_fids(
            fetch, base, fids, fid_from, fid_to, keywords, latest, type_,
            sobj, start_year, end_year, delay, calls,
        ):
            _pause(calls, delay)
            url = show_url(base, fid)
            try:
                page = fetch(url)
                sentences = document_sentences(page)
            except (OSError, ValueError) as exc:
                sys.stderr.write("open-gram: fid %s: %s\n" % (fid, exc))
                continue
            for sentence in sentences:
                out.write(sentence)
                out.write("\n")
            count += len(sentences)
            sys.stderr.write("fid %s sentences %d\n" % (fid, len(sentences)))
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(prog="importers.lawdb")
    parser.add_argument("--output", required=True)
    parser.add_argument("--html", action="append", default=[],
                        help="local show.php page, UTF-8")
    parser.add_argument("--fid", action="append", type=int, default=[])
    parser.add_argument("--fid-from", type=int)
    parser.add_argument("--fid-to", type=int)
    parser.add_argument("--keywords", help="search; at least two Han characters")
    parser.add_argument("--latest", action="store_true",
                        help="documents linked from the category front page")
    parser.add_argument("--type", default="1", help="1, 10, 30, 50, or %%")
    parser.add_argument("--sobj", choices=("title", "content"), default="title")
    parser.add_argument("--start-year", type=int, default=1949)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args(argv)
    try:
        count = scrape(
            args.output,
            html_paths=args.html,
            fids=args.fid,
            fid_from=args.fid_from,
            fid_to=args.fid_to,
            keywords=args.keywords,
            latest=args.latest,
            type_=args.type,
            sobj=args.sobj,
            start_year=args.start_year,
            end_year=args.end_year,
            delay=args.delay,
            base=args.base,
            timeout=args.timeout,
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write("open-gram: %s\n" % exc)
        return 1
    sys.stderr.write("sentences %d\n%s\n" % (count, args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())

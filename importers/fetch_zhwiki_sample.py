#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download a small zhwiki pages-articles XML sample.

The full dump is ``zhwiki-latest-pages-articles.xml.bz2`` (about 3.4 GiB
compressed on the 2026-09-01 dump). This script does not fetch it. It
reads the multistream index of the first split, range-downloads only the
bzip2 members that contain the first ``--max-ns0`` namespace-0 articles,
and writes well-formed XML for ``importers.wikimedia``.

Defaults (the ``latest`` symlinks; the 2026-09-01 dump when this was added):

https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles-multistream1.xml-p1p187712.bz2
https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles-multistream-index1.txt-p1p187712.bz2
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import os
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

DUMP_BASE = "https://dumps.wikimedia.org/zhwiki/latest"
MULTISTREAM_URL = (
    DUMP_BASE + "/zhwiki-latest-pages-articles-multistream1.xml-p1p187712.bz2"
)
INDEX_URL = (
    DUMP_BASE
    + "/zhwiki-latest-pages-articles-multistream-index1.txt-p1p187712.bz2"
)
FULL_DUMP_URL = DUMP_BASE + "/zhwiki-latest-pages-articles.xml.bz2"
USER_AGENT = "open-gram-zhwiki-sample/1.0 (+https://github.com/difanz/open-gram)"
_PAGE_CLOSE = b"</page>"


def default_output():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "build", "zhwiki-sample", "sample.xml"))


def parse_index(text):
    """Return ``(offset, page_id, title)`` rows from a multistream index."""
    rows = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            raise ValueError("index line %d is not offset:id:title" % line_no)
        try:
            offset = int(parts[0])
            page_id = int(parts[1])
        except ValueError as exc:
            raise ValueError(
                "index line %d has a non-integer offset or id" % line_no
            ) from exc
        if offset < 0 or page_id < 0:
            raise ValueError("index line %d has a negative offset or id" % line_no)
        rows.append((offset, page_id, parts[2]))
    if not rows:
        raise ValueError("multistream index is empty")
    return rows


def member_bounds(rows, file_size):
    """``(start, end)`` byte ranges of each bzip2 member, including the header."""
    if file_size < 1:
        raise ValueError("multistream file is empty")
    starts = [0]
    for offset, _page_id, _title in rows:
        if offset < starts[-1]:
            raise ValueError("index offsets went backwards at %d" % offset)
        if offset > file_size:
            raise ValueError(
                "index offset %d is past the end of the multistream file" % offset
            )
        if offset != starts[-1]:
            starts.append(offset)
    if starts[-1] > file_size:
        raise ValueError("index offset is past the end of the multistream file")
    if starts[-1] < file_size:
        starts.append(file_size)
    return [(start, end) for start, end in zip(starts, starts[1:]) if end > start]


def decompress_members(blob, bounds):
    """Decompress complete bzip2 members. ``bounds`` are offsets into ``blob``."""
    parts = []
    for start, end in bounds:
        if end <= start or end > len(blob):
            raise ValueError("bzip2 member bounds %d:%d do not fit the download" % (start, end))
        try:
            parts.append(bz2.decompress(blob[start:end]))
        except OSError as exc:
            raise ValueError(
                "bzip2 member %d:%d is incomplete or corrupt" % (start, end)
            ) from exc
    return b"".join(parts)


def iter_page_blobs(xml_bytes):
    """Yield each complete ``<page>...</page>`` blob. A trailing partial page is omitted."""
    start = xml_bytes.find(b"<page>")
    if start < 0:
        return
    rest = xml_bytes[start:]
    close_len = len(_PAGE_CLOSE)
    while True:
        end = rest.find(_PAGE_CLOSE)
        if end < 0:
            break
        yield rest[:end + close_len]
        rest = rest[end + close_len:]


def article_page(page_bytes):
    """True for a non-redirect namespace-0 page, using the importer's rules."""
    from importers.wikimedia import _child_text, _is_redirect, _revision_text

    try:
        page = ET.fromstring(page_bytes)
    except ET.ParseError as exc:
        raise ValueError("page XML is not well-formed") from exc
    ns = _child_text(page, "ns").strip()
    text = _revision_text(page)
    return ns == "0" and bool(text) and not _is_redirect(page, text)


def limit_ns0(xml_bytes, max_ns0, max_pages=None):
    """Keep pages through the ``max_ns0``-th article, or through ``max_pages``.

    Earlier non-articles stay, so the importer still sees other namespaces
    and redirects. The caller closes a truncated dump by appending
    ``</mediawiki>``. A trailing partial page is dropped.
    """
    if max_ns0 < 1:
        raise ValueError("max_ns0 must be >= 1")
    if max_pages is None:
        max_pages = 10 ** 9
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    start = xml_bytes.find(b"<page>")
    if start < 0:
        raise ValueError("downloaded XML has no <page>")
    if b"<mediawiki" not in xml_bytes[:start]:
        raise ValueError("downloaded XML has no <mediawiki> header")
    header = xml_bytes[:start]
    kept = []
    pages = 0
    ns0 = 0
    for page in iter_page_blobs(xml_bytes):
        pages += 1
        kept.append(page)
        if article_page(page):
            ns0 += 1
        if ns0 >= max_ns0 or pages >= max_pages:
            break
    if ns0 < 1:
        raise ValueError("no namespace-0 article in the downloaded prefix")
    xml = header + b"".join(kept) + b"\n</mediawiki>\n"
    return xml, {"pages": pages, "ns0": ns0}


def _request(url, byte_range=None):
    headers = {"User-Agent": USER_AGENT}
    if byte_range is not None:
        start, end_inclusive = byte_range
        headers["Range"] = "bytes=%d-%d" % (start, end_inclusive)
    return urllib.request.Request(url, headers=headers)


def head_info(url):
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        length = response.headers.get("Content-Length")
        if not length:
            raise ValueError("no Content-Length for %s" % url)
        return {
            "url": response.geturl(),
            "length": int(length),
            "last_modified": response.headers.get("Last-Modified", ""),
        }


def fetch_range(url, start, end):
    """Download ``[start, end)``. Requires an HTTP 206 response."""
    if end <= start:
        raise ValueError("empty byte range %d:%d" % (start, end))
    request = _request(url, (start, end - 1))
    with urllib.request.urlopen(request, timeout=180) as response:
        status = getattr(response, "status", None)
        if status != 206:
            raise ValueError("expected HTTP 206 for a byte range, got %s" % status)
        data = response.read()
    expected = end - start
    if len(data) != expected:
        raise ValueError(
            "short read at %d:%d (%d bytes, wanted %d)" % (start, end, len(data), expected)
        )
    return data


def fetch_bytes(url):
    request = _request(url)
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
    if not data:
        raise ValueError("empty download from %s" % url)
    return data


def atomic_write_bytes(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".zhwiki-sample.", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _enough(xml_bytes, max_ns0, max_pages):
    """True once the prefix has enough articles or has hit the page cap."""
    if b"</page>" not in xml_bytes:
        return False
    try:
        _xml, stats = limit_ns0(xml_bytes, max_ns0, max_pages)
    except ValueError:
        return False
    return stats["ns0"] >= max_ns0 or stats["pages"] >= max_pages


def download_sample(multistream_url, index_url, output, max_ns0, max_pages):
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    index_bz2 = fetch_bytes(index_url)
    try:
        index_text = bz2.decompress(index_bz2).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("index is not bzip2-compressed UTF-8") from exc
    rows = parse_index(index_text)
    info = head_info(multistream_url)
    bounds = member_bounds(rows, info["length"])
    parts = []
    downloaded = 0
    members = 0
    for start, end in bounds:
        blob = fetch_range(multistream_url, start, end)
        downloaded += len(blob)
        parts.append(decompress_members(blob, [(0, len(blob))]))
        members += 1
        if _enough(b"".join(parts), max_ns0, max_pages):
            break
    xml, stats = limit_ns0(b"".join(parts), max_ns0, max_pages)
    atomic_write_bytes(output, xml)
    return {
        "multistream_url": multistream_url,
        "resolved_url": info["url"],
        "index_url": index_url,
        "last_modified": info["last_modified"],
        "multistream_bytes": info["length"],
        "bytes_downloaded": downloaded,
        "members": members,
        "output": output,
        "output_bytes": len(xml),
        "sha256": hashlib.sha256(xml).hexdigest(),
        "pages": stats["pages"],
        "ns0": stats["ns0"],
        "max_ns0": max_ns0,
        "full_dump_url": FULL_DUMP_URL,
    }


def format_report(info):
    lines = [
        "multistream_url: %s" % info["multistream_url"],
        "resolved_url: %s" % info["resolved_url"],
        "index_url: %s" % info["index_url"],
        "last_modified: %s" % info["last_modified"],
        "multistream_bytes: %d" % info["multistream_bytes"],
        "bytes_downloaded: %d" % info["bytes_downloaded"],
        "members: %d" % info["members"],
        "output: %s" % info["output"],
        "output_bytes: %d" % info["output_bytes"],
        "sha256: %s" % info["sha256"],
        "pages: %d" % info["pages"],
        "ns0: %d" % info["ns0"],
        "max_ns0: %d" % info["max_ns0"],
        "full_dump_url: %s" % info["full_dump_url"],
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--multistream-url", default=MULTISTREAM_URL)
    parser.add_argument("--index-url", default=INDEX_URL)
    parser.add_argument("--output", default=default_output())
    parser.add_argument("--max-ns0", type=int, default=80,
                        help="namespace-0 articles to keep (default 80)")
    parser.add_argument("--max-pages", type=int, default=2000,
                        help="stop even if fewer namespace-0 articles were found")
    args = parser.parse_args(argv)
    try:
        info = download_sample(
            args.multistream_url, args.index_url, args.output,
            args.max_ns0, args.max_pages,
        )
    except Exception as exc:
        print("fetch_zhwiki_sample: %s" % exc, file=sys.stderr)
        return 1
    sys.stdout.write(format_report(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())

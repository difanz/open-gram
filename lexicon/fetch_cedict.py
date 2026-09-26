#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download the latest MDBG CC-CEDICT UTF-8 release.

The dump is regenerable, so it is gitignored. lexicon/cedict.py already
looks for ``../data/cedict_1_0_ts_utf-8_mdbg.txt`` when run from lexicon/.

Page (date and entry count):
https://www.mdbg.net/chinese/dictionary?page=cedict

Gzip used here:
https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz

The header comments (``#! date=``, ``#! entries=``, ``#! license=``) are
checked against the number of non-comment lines. There is no separate
checksum file on the MDBG page; the script prints SHA-256 of the gzip
and of the UTF-8 text.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sys
import tempfile
import urllib.request

PAGE_URL = "https://www.mdbg.net/chinese/dictionary?page=cedict"
GZIP_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
USER_AGENT = "open-gram-cedict-fetch/1.0 (+https://github.com/difanz/open-gram)"


def default_output():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "data", "cedict_1_0_ts_utf-8_mdbg.txt"))


def parse_header(text):
    """Return ``#! key=value`` fields from the leading comment block."""
    meta = {}
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        if not line.startswith("#!"):
            continue
        body = line[2:].strip()
        if "=" not in body:
            continue
        key, value = body.split("=", 1)
        meta[key.strip()] = value.strip()
    return meta


def count_data_lines(text):
    count = 0
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        count += 1
    return count


def verify_text(text):
    """Raise ValueError when the dump does not match its own header."""
    if "CC-CEDICT" not in text[:400]:
        raise ValueError("download is not a CC-CEDICT file")
    meta = parse_header(text)
    license_url = meta.get("license", "")
    if "creativecommons.org/licenses/by-sa/4.0" not in license_url:
        raise ValueError("header license is not CC BY-SA 4.0: %r" % license_url)
    if meta.get("publisher") != "MDBG":
        raise ValueError("header publisher is not MDBG: %r" % meta.get("publisher"))
    if "date" not in meta:
        raise ValueError("header has no #! date=")
    try:
        declared = int(meta["entries"])
    except (KeyError, ValueError) as exc:
        raise ValueError("header has no integer #! entries=") from exc
    actual = count_data_lines(text)
    if actual != declared:
        raise ValueError("entry count %d != header entries=%d" % (actual, declared))
    meta["data_lines"] = str(actual)
    return meta


def fetch_gzip(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
    if not payload:
        raise ValueError("empty download from %s" % url)
    return payload


def decompress_gzip(payload):
    try:
        return gzip.decompress(payload).decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("download is not gzip-compressed UTF-8") from exc


def atomic_write_bytes(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cedict.", dir=parent)
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


def download(url, output):
    payload = fetch_gzip(url)
    text = decompress_gzip(payload)
    meta = verify_text(text)
    encoded = text.encode("utf-8")
    atomic_write_bytes(output, encoded)
    gzip_path = output + ".gz"
    atomic_write_bytes(gzip_path, payload)
    return {
        "url": url,
        "page": PAGE_URL,
        "output": output,
        "gzip": gzip_path,
        "gzip_sha256": hashlib.sha256(payload).hexdigest(),
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "meta": meta,
    }


def format_report(info):
    meta = info["meta"]
    lines = [
        "url: %s" % info["url"],
        "page: %s" % info["page"],
        "output: %s" % info["output"],
        "gzip: %s" % info["gzip"],
        "gzip_sha256: %s" % info["gzip_sha256"],
        "text_sha256: %s" % info["text_sha256"],
        "date: %s" % meta.get("date", ""),
        "entries: %s" % meta.get("entries", ""),
        "data_lines: %s" % meta.get("data_lines", ""),
        "publisher: %s" % meta.get("publisher", ""),
        "license: %s" % meta.get("license", ""),
        "charset: %s" % meta.get("charset", ""),
        "format: %s" % meta.get("format", ""),
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=GZIP_URL, help="MDBG CC-CEDICT gzip URL")
    parser.add_argument("--output", default=default_output(), help="uncompressed UTF-8 path")
    args = parser.parse_args(argv)
    try:
        info = download(args.url, args.output)
    except Exception as exc:
        print("fetch_cedict: %s" % exc, file=sys.stderr)
        return 1
    sys.stdout.write(format_report(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())

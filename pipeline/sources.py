# -*- coding: utf-8 -*-
"""Download a caller-supplied dump URL into a directory.

The URL is an argument. This module does not accept or reject a corpus
by name, and it does not unpack the file.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request


def local_name(url):
    name = os.path.basename(urllib.parse.urlparse(url).path)
    if not name:
        raise ValueError("URL has no filename: %s" % url)
    return name


def fetch_url(url, dest_dir):
    """Stream ``url`` into ``dest_dir`` under the URL basename."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, local_name(url))
    partial = dest + ".partial"
    request = urllib.request.Request(url, headers={"User-Agent": "open-gram"})
    with urllib.request.urlopen(request) as src, open(partial, "wb") as out:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    os.replace(partial, dest)
    return dest

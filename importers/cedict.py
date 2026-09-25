# -*- coding: utf-8 -*-
"""CC-CEDICT to dict.full lines.

CC-CEDICT is a dictionary, so this script does not write a sentence
corpus. Each output line is a simplified headword and its sunpinyin
readings, which ``pipeline.run dict`` already accepts as ``--dict-full``.

Readings of one simplified word are gathered in file order. Pinyin is
lower case, tones are removed, syllables are joined with apostrophes,
and ü is written as v (nu: → nv, lu:e → lve). The CC-CEDICT erhua
syllable r5 is er, matching dict.full (花儿 hua'er).

The input is uncompressed CC-CEDICT text. This script does not download
it and does not assign word ids.
"""

from __future__ import annotations

import argparse
import os
import re
import sys


# Traditional Simplified [pin1 yin1] /gloss/
_ENTRY = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\[\]]+)\]\s+/")
_TONE = "12345"


def _syllable(raw, where):
    syllable = raw.lower()
    if syllable and syllable[-1] in _TONE:
        syllable = syllable[:-1]
    syllable = syllable.replace("u:", "v").replace("ü", "v")
    if syllable == "r":
        syllable = "er"
    if not syllable or "'" in syllable or ":" in syllable:
        raise ValueError("%s: bad pinyin syllable %r" % (where, raw))
    return syllable


def normalize_pinyin(text, where):
    """CC-CEDICT ``[pin1 yin1]`` to a sunpinyin reading."""
    parts = text.split()
    if not parts:
        raise ValueError("%s: empty pinyin" % where)
    return "'".join(_syllable(part, where) for part in parts)


def iter_entries(handle, name):
    """Yield ``(simplified, reading)`` from one CC-CEDICT file."""
    for line_no, line in enumerate(handle, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        where = "%s:%d" % (name, line_no)
        match = _ENTRY.match(line)
        if match is None:
            raise ValueError("%s: not a CC-CEDICT entry" % where)
        yield match.group(2), normalize_pinyin(match.group(3), where)


def load(paths):
    """Simplified words in first-seen order, each with unique readings."""
    order = []
    readings = {}
    for path in paths:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for word, reading in iter_entries(handle, path):
                bucket = readings.get(word)
                if bucket is None:
                    order.append(word)
                    readings[word] = [reading]
                elif reading not in bucket:
                    bucket.append(reading)
    return [(word, readings[word]) for word in order]


def convert(paths, output):
    rows = load(paths)
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        for word, readings in rows:
            out.write("%s %s\n" % (word, " ".join(readings)))
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="importers.cedict")
    parser.add_argument("--cedict", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        count = convert(args.cedict, args.output)
    except (OSError, ValueError) as exc:
        sys.stderr.write("open-gram: %s\n" % exc)
        return 1
    sys.stderr.write("entries %d\n%s\n" % (count, args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())

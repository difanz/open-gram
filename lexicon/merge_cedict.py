#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append new CC-CEDICT simplified surfaces to dict.full.

lexicon/merge.py looks up Baidu frequencies and is not used here.
This merge is local:

- Every existing dict.full row is copied unchanged, in order.
- lexicon/cedict.py supplies the simplified field and toneless pinyin
  (syllables joined with apostrophes). Rows it cannot normalize are skipped.
- The traditional field is not a surface. cedict.py reads only the
  simplified field, so a traditional-only spelling is not added.
- A surface already in dict.full is skipped, including extra CEDICT
  readings of that surface. Existing rows stay as they are.
- A surface that is new is appended once, in first-seen CEDICT order.
  Further distinct toneless readings of that new surface are extra
  fields on the same line (``surface py1 py2``), which is the dict.full
  shape. There is no frequency field.
- Identical readings of a new surface are not repeated.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import cedict
from fetch_cedict import count_data_lines, parse_header


def convert_line(line):
    """Return (simplified, toneless pinyin, trad_differs) or None.

    None means cedict.py would skip the line (comment, hybrid, or a
    pinyin that does not end in a tone digit).
    """
    if line.startswith("#") or not line.strip():
        return None
    match = cedict.cedict_pattern.match(line)
    if not match:
        return None
    simplified, raw_pinyin = match.groups()
    try:
        pinyin = cedict.normalize_pinyins(raw_pinyin)
    except Exception:
        return None
    if not simplified or not pinyin:
        return None
    traditional = line.split(" ", 1)[0]
    return simplified, pinyin, traditional != simplified


def _surfaces(text):
    found = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        found.add(line.split(" ", 1)[0])
    return found


def merge_text(existing_text, cedict_text):
    """Return (merged_text, stats).

    ``existing_text`` is kept byte-for-byte aside from a missing final
    newline. New rows are appended after it.
    """
    existing_surfaces = _surfaces(existing_text)
    readings = {}
    order = []
    seen_new_reading = set()
    stats = {
        "dict_full_surfaces": len(existing_surfaces),
        "cedict_data_lines": count_data_lines(cedict_text),
        "converted": 0,
        "skipped_normalize": 0,
        "skipped_present_rows": 0,
        "skipped_present_surfaces": 0,
        "new_surfaces": 0,
        "extra_readings": 0,
        "repeated_new_readings": 0,
        "converted_trad_differs": 0,
        "trad_surfaces_not_added": 0,
    }
    present_surfaces = set()
    trad_not_added = set()

    for line in cedict_text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        converted = convert_line(line)
        if converted is None:
            stats["skipped_normalize"] += 1
            continue
        simplified, pinyin, trad_differs = converted
        stats["converted"] += 1
        if trad_differs:
            stats["converted_trad_differs"] += 1
            traditional = line.split(" ", 1)[0]
            if traditional not in existing_surfaces:
                trad_not_added.add(traditional)
        if simplified in existing_surfaces:
            stats["skipped_present_rows"] += 1
            present_surfaces.add(simplified)
            continue
        key = (simplified, pinyin)
        if simplified not in readings:
            readings[simplified] = []
            order.append(simplified)
        if key in seen_new_reading:
            stats["repeated_new_readings"] += 1
            continue
        seen_new_reading.add(key)
        if readings[simplified]:
            stats["extra_readings"] += 1
        readings[simplified].append(pinyin)

    stats["skipped_present_surfaces"] = len(present_surfaces)
    stats["new_surfaces"] = len(order)
    stats["trad_surfaces_not_added"] = len(trad_not_added)
    stats["multi_reading_surfaces"] = sum(1 for word in order if len(readings[word]) > 1)

    prefix = existing_text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    extra = "".join("%s %s\n" % (word, " ".join(readings[word])) for word in order)
    merged = prefix + extra
    samples = [(word, list(readings[word])) for word in order]
    return merged, stats, samples


def merge_files(dict_path, cedict_path, output_path):
    with open(dict_path, "r", encoding="utf-8", newline="") as handle:
        existing = handle.read()
    with open(cedict_path, "r", encoding="utf-8") as handle:
        cedict_text = handle.read()
    # newline='' keeps the Android prefix intact. dict.full is LF only;
    # CR would be rewritten by the appended rows' LF endings.
    if "\r" in existing:
        raise ValueError("%s uses CR line endings; refusing to rewrite" % dict_path)
    merged, stats, samples = merge_text(existing, cedict_text)
    header = parse_header(cedict_text)
    stats["cedict_date"] = header.get("date", "")
    stats["cedict_entries"] = header.get("entries", "")
    stats["cedict_license"] = header.get("license", "")
    stats["cedict_publisher"] = header.get("publisher", "")
    _atomic_write(output_path, merged.encode("utf-8"))
    return stats, samples


def _atomic_write(path, data):
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dict.full.", dir=parent)
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


def format_stats(stats, samples, sample_limit):
    lines = ["%s: %s" % (key, stats[key]) for key in stats]
    lines.append("samples:")
    for word, pinyins in samples[:sample_limit]:
        lines.append("%s %s" % (word, " ".join(pinyins)))
    multi = [(word, pinyins) for word, pinyins in samples if len(pinyins) > 1]
    lines.append("multi_reading_samples:")
    for word, pinyins in multi[:sample_limit]:
        lines.append("%s %s" % (word, " ".join(pinyins)))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dict", dest="dict_path", required=True, help="existing dict.full")
    parser.add_argument("--cedict", required=True, help="uncompressed CC-CEDICT UTF-8 file")
    parser.add_argument("--output", required=True, help="merged dict.full path")
    parser.add_argument("--report", help="optional stats path")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="print stats and do not write")
    args = parser.parse_args(argv)
    if args.dry_run:
        with open(args.dict_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
        with open(args.cedict, "r", encoding="utf-8") as handle:
            cedict_text = handle.read()
        _merged, stats, samples = merge_text(existing, cedict_text)
        header = parse_header(cedict_text)
        stats["cedict_date"] = header.get("date", "")
        stats["cedict_entries"] = header.get("entries", "")
        stats["cedict_license"] = header.get("license", "")
        stats["cedict_publisher"] = header.get("publisher", "")
    else:
        stats, samples = merge_files(args.dict_path, args.cedict, args.output)
    report = format_stats(stats, samples, args.samples)
    sys.stdout.write(report)
    if args.report:
        parent = os.path.dirname(os.path.abspath(args.report))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

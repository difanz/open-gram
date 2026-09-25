# -*- coding: utf-8 -*-
"""Driver for dict.utf8 and lm_sc.3gm.arpa.

Stages, in order: dict, text, segment, counts, arpa.
``all`` runs that sequence. ``fetch`` downloads the given URLs and does
not select a corpus.

Binary packing remains in sunpinyin (slmpack, slmthread, tslmendian, genpyt).
"""

from __future__ import annotations

import argparse
import os
import sys

from pipeline.arpa import DEFAULT_DISCOUNT, SLMPACK_ORDER, write_arpa
from pipeline.lexicon_build import build_dict_utf8, default_dict_head
from pipeline.ngram_count import count_segmented, write_surface_counts
from pipeline.segment import segment_file
from pipeline.sources import fetch_url
from pipeline.wiki_text import dumps_to_sentences


def _order(value):
    order = int(value)
    if order < 1 or order > SLMPACK_ORDER:
        raise argparse.ArgumentTypeError("order must be in 1..%d" % SLMPACK_ORDER)
    return order


def _add_dict_args(parser, full_required):
    parser.add_argument("--dict-full", required=full_required)
    parser.add_argument("--dict-head", default=default_dict_head())
    parser.add_argument("--output", required=True)


def build_parser():
    parser = argparse.ArgumentParser(prog="pipeline.run")
    sub = parser.add_subparsers(dest="stage", required=True)

    dict_p = sub.add_parser("dict")
    _add_dict_args(dict_p, True)

    text_p = sub.add_parser("text")
    text_p.add_argument("--xml", action="append", required=True)
    text_p.add_argument("--output", required=True)

    seg_p = sub.add_parser("segment")
    seg_p.add_argument("--dict", required=True, help="dict.utf8")
    seg_p.add_argument("--input", required=True)
    seg_p.add_argument("--output", required=True)
    seg_p.add_argument("--segmenter", choices=("match", "preseg", "crf"), default="match")
    seg_p.add_argument("--crf-model")

    cnt_p = sub.add_parser("counts")
    cnt_p.add_argument("--dict", required=True, help="dict.utf8")
    cnt_p.add_argument("--segmented", required=True)
    cnt_p.add_argument("--counts", required=True, help="directory for shards and merged counts")
    cnt_p.add_argument("--order", type=_order, default=SLMPACK_ORDER)
    cnt_p.add_argument("--max-keys", type=int, default=2000000)
    cnt_p.add_argument("--surface", help="optional word-form count TSV")

    arpa_p = sub.add_parser("arpa")
    arpa_p.add_argument("--dict", required=True, help="dict.utf8")
    arpa_p.add_argument("--counts", required=True, help="directory written by the counts stage")
    arpa_p.add_argument("--output", required=True)
    arpa_p.add_argument("--order", type=_order, default=SLMPACK_ORDER)
    arpa_p.add_argument("--discount", type=float, default=DEFAULT_DISCOUNT)

    all_p = sub.add_parser("all")
    all_p.add_argument("--xml", action="append", required=True)
    all_p.add_argument("--dict-full", required=True)
    all_p.add_argument("--dict-head", default=default_dict_head())
    all_p.add_argument("--work", required=True)
    all_p.add_argument("--segmenter", choices=("match", "preseg", "crf"), default="match")
    all_p.add_argument("--crf-model")
    all_p.add_argument("--order", type=_order, default=SLMPACK_ORDER)
    all_p.add_argument("--discount", type=float, default=DEFAULT_DISCOUNT)
    all_p.add_argument("--max-keys", type=int, default=2000000)
    all_p.add_argument("--surface", action="store_true",
                       help="also write work/counts.tsv")

    fetch_p = sub.add_parser("fetch")
    fetch_p.add_argument("--dest", required=True)
    fetch_p.add_argument("--url", action="append", required=True)

    return parser


def _merged_dir(counts_dir):
    merged = os.path.join(counts_dir, "merged")
    if os.path.isdir(merged):
        return merged
    return counts_dir


def run_all(args):
    if args.segmenter == "preseg":
        raise ValueError(
            "stage all segments raw text; pass presegmented input to the segment stage"
        )
    work = args.work
    os.makedirs(work, exist_ok=True)
    dict_path = os.path.join(work, "dict.utf8")
    sentences = os.path.join(work, "sentences.txt")
    segmented = os.path.join(work, "segmented.txt")
    counts_dir = os.path.join(work, "counts")
    arpa_path = os.path.join(work, "lm_sc.3gm.arpa")
    build_dict_utf8(args.dict_full, dict_path, args.dict_head)
    nsent, script = dumps_to_sentences(args.xml, sentences)
    segment_file(
        sentences, segmented, dict_path,
        segmenter=args.segmenter, crf_model=args.crf_model,
    )
    merged = count_segmented(
        segmented, dict_path, counts_dir,
        order=args.order, max_keys=args.max_keys,
    )
    if args.surface:
        write_surface_counts(
            merged, dict_path, os.path.join(work, "counts.tsv"), order=args.order,
        )
    write_arpa(
        merged, dict_path, arpa_path,
        order=args.order, discount=args.discount,
    )
    sys.stderr.write(
        "dict %s\nsentences %d (%s)\nsegmented %s\ncounts %s\narpa %s\n"
        % (dict_path, nsent, script, segmented, merged, arpa_path)
    )
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.stage == "dict":
            build_dict_utf8(args.dict_full, args.output, args.dict_head)
        elif args.stage == "text":
            dumps_to_sentences(args.xml, args.output)
        elif args.stage == "segment":
            segment_file(
                args.input, args.output, args.dict,
                segmenter=args.segmenter, crf_model=args.crf_model,
            )
        elif args.stage == "counts":
            merged = count_segmented(
                args.segmented, args.dict, args.counts,
                order=args.order, max_keys=args.max_keys,
            )
            if args.surface:
                write_surface_counts(merged, args.dict, args.surface, order=args.order)
        elif args.stage == "arpa":
            write_arpa(
                _merged_dir(args.counts), args.dict, args.output,
                order=args.order, discount=args.discount,
            )
        elif args.stage == "all":
            return run_all(args)
        elif args.stage == "fetch":
            for url in args.url:
                fetch_url(url, args.dest)
        else:
            raise RuntimeError("unhandled stage %s" % args.stage)
    except (OSError, ValueError, KeyError, RuntimeError, PermissionError) as exc:
        sys.stderr.write("open-gram: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

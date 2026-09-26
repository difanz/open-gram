# -*- coding: utf-8 -*-
"""Driver for dict.utf8 and lm_sc.3gm.arpa.

Stages, in order: dict, segment, counts, arpa.
``all`` runs that sequence on a sentence corpus, one sentence per line.
Source converters live under ``importers/`` and are not part of this driver.

Binary packing remains in sunpinyin (slmpack, slmthread, tslmendian, genpyt).
"""

from __future__ import annotations

import argparse
import os
import sys

from pipeline.arpa import (
    DEFAULT_BREAKERS,
    DEFAULT_CUTS,
    DEFAULT_EXCLUDES,
    SLMPACK_ORDER,
    default_discounts,
    parse_discount,
    write_arpa,
)
from pipeline.lexicon_build import build_dict_utf8, default_dict_head
from pipeline.ngram_count import count_segmented, write_surface_counts
from pipeline.segment import segment_file


def _order(value):
    order = int(value)
    if order < 1 or order > SLMPACK_ORDER:
        raise argparse.ArgumentTypeError("order must be in 1..%d" % SLMPACK_ORDER)
    return order


def _cut_list(text):
    try:
        cuts = tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("cutoffs must be integers") from exc
    if not cuts:
        raise argparse.ArgumentTypeError("cutoff list is empty")
    return cuts


def _id_list(text):
    text = text.strip()
    if not text:
        return ()
    try:
        return tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("word ids must be integers") from exc


def _add_estimate_args(parser):
    parser.add_argument("--order", type=_order, default=SLMPACK_ORDER)
    parser.add_argument("--cut", type=_cut_list, default=DEFAULT_CUTS,
                        help="per-order cutoffs; drop a count at or below its cutoff")
    parser.add_argument("--discount", action="append",
                        help="GT,R,dis or ABS[,c] or LIN[,d], once per order")
    parser.add_argument("--breaker", type=_id_list, default=DEFAULT_BREAKERS)
    parser.add_argument("--exclude", type=_id_list, default=DEFAULT_EXCLUDES)
    parser.add_argument("--word-count", type=int,
                        help="lexicon size used for the level-0 probability")


def _estimate_kwargs(args):
    if args.discount:
        discounts = tuple(parse_discount(item) for item in args.discount)
    else:
        discounts = default_discounts()
    if len(discounts) != args.order or len(args.cut) != args.order:
        raise ValueError(
            "order %d needs %d cutoffs and %d discount methods"
            % (args.order, args.order, args.order)
        )
    return {
        "order": args.order,
        "cuts": args.cut,
        "discounts": discounts,
        "breakers": args.breaker,
        "excludes": args.exclude,
        "word_count": args.word_count,
    }


def _add_dict_args(parser, full_required):
    parser.add_argument("--dict-full", required=full_required)
    parser.add_argument("--dict-head", default=default_dict_head())
    parser.add_argument("--output", required=True)


def build_parser():
    parser = argparse.ArgumentParser(prog="pipeline.run")
    sub = parser.add_subparsers(dest="stage", required=True)

    dict_p = sub.add_parser("dict")
    _add_dict_args(dict_p, True)

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
    _add_estimate_args(arpa_p)

    all_p = sub.add_parser("all")
    all_p.add_argument("--corpus", action="append", required=True,
                       help="sentence file, one sentence per line")
    all_p.add_argument("--dict-full", required=True)
    all_p.add_argument("--dict-head", default=default_dict_head())
    all_p.add_argument("--work", required=True)
    all_p.add_argument("--segmenter", choices=("match", "preseg", "crf"), default="match")
    all_p.add_argument("--crf-model")
    _add_estimate_args(all_p)
    all_p.add_argument("--max-keys", type=int, default=2000000)
    all_p.add_argument("--surface", action="store_true",
                       help="also write work/counts.tsv")

    return parser


def _merged_dir(counts_dir):
    merged = os.path.join(counts_dir, "merged")
    if os.path.isdir(merged):
        return merged
    return counts_dir


def _copy_corpus(paths, dest):
    """Copy sentence files through unchanged. Returns the number of non-empty lines."""
    count = 0
    with open(dest, "w", encoding="utf-8") as out:
        for path in paths:
            with open(path, "r", encoding="utf-8") as src:
                for line in src:
                    out.write(line)
                    if not line.endswith("\n"):
                        out.write("\n")
                    if line.strip():
                        count += 1
    return count


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
    nsent = _copy_corpus(args.corpus, sentences)
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
    write_arpa(merged, dict_path, arpa_path, **_estimate_kwargs(args))
    sys.stderr.write(
        "dict %s\nsentences %d\nsegmented %s\ncounts %s\narpa %s\n"
        % (dict_path, nsent, segmented, merged, arpa_path)
    )
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.stage == "dict":
            build_dict_utf8(args.dict_full, args.output, args.dict_head)
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
                **_estimate_kwargs(args),
            )
        elif args.stage == "all":
            return run_all(args)
        else:
            raise RuntimeError("unhandled stage %s" % args.stage)
    except (OSError, ValueError, KeyError, RuntimeError, PermissionError) as exc:
        sys.stderr.write("open-gram: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

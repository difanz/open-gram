# -*- coding: utf-8 -*-
"""Word-id n-gram counts, flushed to sorted shards.

Each input line is one sentence. Tokens are slash-separated, and n-grams
do not cross lines. Distinct keys stay in memory until ``max_keys``, then
each order is written as an id-sorted shard. A k-way merge sums identical
tuples. The fixture uses a small ``max_keys`` so that merge is exercised.
Estimation reads only the merged files.
"""

from __future__ import annotations

import heapq
import os

from pipeline.lexicon_io import load_dict_utf8

_ORDER_MIN = 1
_ORDER_MAX = 3


class NgramCounter:
    def __init__(self, shard_dir, order=3, max_keys=2000000):
        if order < _ORDER_MIN or order > _ORDER_MAX:
            raise ValueError("order must be in %d..%d" % (_ORDER_MIN, _ORDER_MAX))
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self.shard_dir = shard_dir
        self.order = order
        self.max_keys = max_keys
        self.counts = [dict() for _ in range(order + 1)]
        self.nkeys = 0
        self.shard_id = 0
        os.makedirs(shard_dir, exist_ok=True)

    def add_sentence(self, ids):
        if not ids:
            return
        for n in range(1, self.order + 1):
            last = len(ids) - n + 1
            for index in range(last):
                self._add(n, ids[index:index + n])

    def _add(self, n, gram):
        bucket = self.counts[n]
        if gram in bucket:
            bucket[gram] += 1
            return
        if self.nkeys >= self.max_keys:
            self.flush()
            bucket = self.counts[n]
        bucket[gram] = bucket.get(gram, 0) + 1
        self.nkeys += 1

    def flush(self):
        if self.nkeys == 0:
            return
        dest = os.path.join(self.shard_dir, "%05d" % self.shard_id)
        os.makedirs(dest, exist_ok=True)
        for n in range(1, self.order + 1):
            path = os.path.join(dest, "%d.ng" % n)
            with open(path, "w", encoding="utf-8") as handle:
                for gram, count in sorted(self.counts[n].items()):
                    handle.write("%s\t%d\n" % (" ".join(str(i) for i in gram), count))
            self.counts[n].clear()
        self.nkeys = 0
        self.shard_id += 1

    def close(self):
        self.flush()
        return self.shard_id


def parse_ng_line(line):
    key, count = line.rstrip("\n").split("\t")
    ids = tuple(int(part) for part in key.split())
    return ids, int(count)


def iter_ng(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield parse_ng_line(line)


def merge_order(shard_paths, output):
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    handles = []
    try:
        iters = []
        for path in shard_paths:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                continue
            handle = open(path, "r", encoding="utf-8")
            handles.append(handle)
            iters.append(iter(parse_ng_line(line) for line in handle if line.strip()))
        heap = []
        for index, iterator in enumerate(iters):
            item = next(iterator, None)
            if item is not None:
                heapq.heappush(heap, (item[0], index, item[1], iterator))
        with open(output, "w", encoding="utf-8") as out:
            current = None
            total = 0
            while heap:
                key, index, count, iterator = heapq.heappop(heap)
                if current is None:
                    current = key
                    total = count
                elif key == current:
                    total += count
                else:
                    out.write("%s\t%d\n" % (" ".join(str(i) for i in current), total))
                    current = key
                    total = count
                nxt = next(iterator, None)
                if nxt is not None:
                    heapq.heappush(heap, (nxt[0], index, nxt[1], iterator))
            if current is not None:
                out.write("%s\t%d\n" % (" ".join(str(i) for i in current), total))
    finally:
        for handle in handles:
            handle.close()


def merge_shards(shard_dir, merged_dir, order):
    os.makedirs(merged_dir, exist_ok=True)
    names = sorted(
        name for name in os.listdir(shard_dir)
        if os.path.isdir(os.path.join(shard_dir, name))
    )
    for n in range(1, order + 1):
        shards = [os.path.join(shard_dir, name, "%d.ng" % n) for name in names]
        merge_order(shards, os.path.join(merged_dir, "%d.ng" % n))


def _tokens(line):
    line = line.strip()
    if not line:
        return []
    if "/" in line:
        return [part for part in line.split("/") if part]
    return line.split()


def count_segmented(segmented_path, dict_utf8, work_dir, order=3, max_keys=2000000):
    lexicon = load_dict_utf8(dict_utf8)
    unknown = lexicon.require("<unknown>")
    shard_dir = os.path.join(work_dir, "shards")
    merged_dir = os.path.join(work_dir, "merged")
    counter = NgramCounter(shard_dir, order=order, max_keys=max_keys)
    with open(segmented_path, "r", encoding="utf-8") as handle:
        for line in handle:
            tokens = _tokens(line)
            if not tokens:
                continue
            ids = tuple(lexicon.word_to_id.get(token, unknown) for token in tokens)
            counter.add_sentence(ids)
    counter.close()
    merge_shards(shard_dir, merged_dir, order)
    return merged_dir


def load_counts(merged_dir, order):
    counts = [dict() for _ in range(order + 1)]
    for n in range(1, order + 1):
        for gram, count in iter_ng(os.path.join(merged_dir, "%d.ng" % n)):
            counts[n][gram] = count
    return counts


def write_surface_counts(merged_dir, dict_utf8, output, order=3):
    """Write ``order``, surface words, and count, in word-id order."""
    lexicon = load_dict_utf8(dict_utf8)
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output, "w", encoding="utf-8") as out:
        for n in range(1, order + 1):
            for gram, count in iter_ng(os.path.join(merged_dir, "%d.ng" % n)):
                words = " ".join(lexicon.surface(wid) for wid in gram)
                out.write("%d\t%s\t%d\n" % (n, words, count))

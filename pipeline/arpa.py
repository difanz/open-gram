# -*- coding: utf-8 -*-
"""Absolute-discount trigram in the text dialect read by slmpack.

CArpaSlm stores direct probabilities, not -log(pr). The file has four
sections:

    \\0-gram\\1
     <pr> <bow>
    \\1-gram\\N
    <word>  <pr> <bow>
    \\2-gram\\N
    <w1> <w2>  <pr> <bow>
    \\3-gram\\N
    <w1> <w2> <w3>  <pr>

getwords() splits on the first two consecutive spaces. Each word is
followed by one space and each probability is written with %20.17f, which
is that separator for values in [0, 10). Rows are sorted by word id
because initChild() binary-searches that order.

Unigrams are maximum likelihood. Orders 2 and 3 use absolute discount.
The history count is the number of in-sentence continuations, not the
marginal count of the shorter gram. Level 0 is the empty history: an id
with no unigram row scores root_bow * ROOT_PR in getPrDirect.

Sections above the requested order are still emitted, empty, so the
trigram reader always sees four headers. Trigram rows are streamed from
the count file. Bigram conditional probabilities stay in sqlite so the
trigram backoff does not require a second copy of the leaf counts.
"""

from __future__ import annotations

import os
import sqlite3

from pipeline.lexicon_io import load_dict_utf8
from pipeline.ngram_count import iter_ng

SLMPACK_ORDER = 3
ROOT_PR = 1e-8
DEFAULT_DISCOUNT = 0.5
# CArpaSlm reads each row with getline(buf, 1024). That stores at most
# 1023 bytes and fails unless the newline falls inside the window, so the
# payload itself is limited to 1022 bytes.
_GETLINE_PAYLOAD = 1022


def discount_history(pairs, discount, lower_of):
    """Absolute discount for one history.

    ``pairs`` is a list of ``(word_id, count)``. ``lower_of(word_id)`` is
    the lower-order probability of that word in the backoff context.
    Returns ``(explicit_pr, bow)``. An empty extension list has backoff
    weight 1.
    """
    if discount < 0.0 or discount >= 1.0:
        raise ValueError("discount must be in [0, 1), got %r" % (discount,))
    if not pairs:
        return {}, 1.0
    hist_count = float(sum(count for _word, count in pairs))
    explicit = {}
    for word, count in pairs:
        pr = (count - discount) / hist_count
        if pr > 0.0:
            explicit[word] = pr
    alpha = 1.0 - sum(explicit.values())
    if alpha < 0.0:
        alpha = 0.0
    lower_sum = 0.0
    for word in explicit:
        lower_sum += lower_of(word)
    denom = 1.0 - lower_sum
    if alpha <= 1e-12:
        bow = 0.0
    elif denom <= 1e-12:
        bow = 1.0
    else:
        bow = alpha / denom
    if bow < 0.0:
        bow = 0.0
    return explicit, bow


def estimate_tables(counts, discount=DEFAULT_DISCOUNT):
    """In-memory estimate. ``counts[n]`` maps an id tuple to a count.

    Used to check the streaming writer. ``write_arpa()`` does not hold
    the trigram table.
    """
    probs = {}
    bows = {(): 1.0}
    total = sum(counts[1].values()) if counts[1] else 0
    if total:
        inv = 1.0 / float(total)
        for gram, count in counts[1].items():
            probs[gram] = count * inv
            bows[gram] = 1.0
    grouped = {}
    for gram, count in counts[2].items():
        grouped.setdefault(gram[:-1], []).append((gram[-1], count))
    for hist, pairs in grouped.items():
        explicit, bow = discount_history(
            pairs, discount, lambda word, _probs=probs: _probs[(word,)]
        )
        bows[hist] = bow
        for word, pr in explicit.items():
            gram = hist + (word,)
            probs[gram] = pr
            bows[gram] = 1.0
    grouped = {}
    for gram, count in counts[3].items():
        grouped.setdefault(gram[:-1], []).append((gram[-1], count))
    for hist, pairs in grouped.items():
        suffix = hist[1:]

        def lower(word, _suffix=suffix, _probs=probs):
            return _probs[_suffix + (word,)]

        explicit, bow = discount_history(pairs, discount, lower)
        bows[hist] = bow
        for word, pr in explicit.items():
            probs[hist + (word,)] = pr
    for gram in list(probs):
        if len(gram) < SLMPACK_ORDER:
            bows.setdefault(gram, 1.0)
    return probs, bows


def backoff_prob(gram, probs, bows, root_pr=ROOT_PR):
    """getPrDirect: stored probability, else bow(history) times the shorter gram."""
    if gram in probs:
        return probs[gram]
    if not gram:
        return root_pr
    bow = bows.get(gram[:-1], 1.0)
    return bow * backoff_prob(gram[1:], probs, bows, root_pr)


def format_prob(value):
    text = "%20.17f" % value
    if len(text) < 20 or not text.startswith(" "):
        raise ValueError(
            "probability %r is outside the %%20.17f padding slmpack expects" % (value,)
        )
    return text


def format_entry(words, pr, bow=None):
    text = "".join(word + " " for word in words)
    text += format_prob(pr)
    if bow is not None:
        text += " " + ("%20.17f" % bow)
    nbytes = len(text.encode("utf-8"))
    if nbytes > _GETLINE_PAYLOAD:
        raise ValueError(
            "ARPA row is %d bytes; slmpack getline accepts at most %d"
            % (nbytes, _GETLINE_PAYLOAD)
        )
    return text


def render_model(probs, bows, lexicon, root_pr=ROOT_PR):
    lines = ["\\0-gram\\1", format_entry([], root_pr, bows.get((), 1.0))]
    for n in range(1, SLMPACK_ORDER + 1):
        grams = sorted(gram for gram in probs if len(gram) == n)
        lines.append("\\%d-gram\\%d" % (n, len(grams)))
        for gram in grams:
            words = tuple(lexicon.surface(wid) for wid in gram)
            if n == SLMPACK_ORDER:
                lines.append(format_entry(words, probs[gram], None))
            else:
                lines.append(format_entry(words, probs[gram], bows.get(gram, 1.0)))
    return "\n".join(lines) + "\n"


def _group_sorted(rows, prefix_len):
    current = None
    batch = []
    expect = prefix_len + 1
    for gram, count in rows:
        if len(gram) != expect:
            raise ValueError("expected a %d-gram, got %r" % (expect, gram))
        key = gram[:prefix_len]
        if current is None:
            current = key
        elif key != current:
            yield current, batch
            current = key
            batch = []
        batch.append((gram[-1], count))
    if current is not None:
        yield current, batch


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line)
            handle.write("\n")


def write_arpa(merged_dir, dict_utf8, output, order=SLMPACK_ORDER,
               discount=DEFAULT_DISCOUNT, root_pr=ROOT_PR):
    if order < 1 or order > SLMPACK_ORDER:
        raise ValueError("order must be in 1..%d" % SLMPACK_ORDER)
    lexicon = load_dict_utf8(dict_utf8)
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    body = os.path.join(merged_dir, "arpa-body")
    os.makedirs(body, exist_ok=True)

    uni_pr = {}
    uni_bow = {}
    total = 0
    for gram, count in iter_ng(os.path.join(merged_dir, "1.ng")):
        uni_pr[gram[0]] = count
        total += count
    if total:
        inv = 1.0 / float(total)
        for wid in list(uni_pr):
            uni_pr[wid] *= inv
            uni_bow[wid] = 1.0

    db_path = os.path.join(merged_dir, "bigram.sqlite")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE bi ("
            "w1 INTEGER NOT NULL, w2 INTEGER NOT NULL, pr REAL NOT NULL, "
            "bow REAL NOT NULL, PRIMARY KEY (w1, w2))"
        )
        if order >= 2:
            def lower_uni(word):
                try:
                    return uni_pr[word]
                except KeyError:
                    raise RuntimeError(
                        "bigram continuation id %d has no unigram count" % word
                    ) from None

            for hist, pairs in _group_sorted(
                    iter_ng(os.path.join(merged_dir, "2.ng")), 1):
                explicit, bow = discount_history(pairs, discount, lower_uni)
                uni_bow[hist[0]] = bow
                conn.executemany(
                    "INSERT INTO bi (w1, w2, pr, bow) VALUES (?, ?, ?, 1.0)",
                    [(hist[0], word, pr) for word, pr in explicit.items()],
                )
            conn.commit()

        tri_path = os.path.join(body, "3")
        with open(tri_path, "w", encoding="utf-8") as tri_out:
            if order >= 3:
                def lower_bi(w2, w3):
                    row = conn.execute(
                        "SELECT pr FROM bi WHERE w1=? AND w2=?", (w2, w3)
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(
                            "trigram suffix (%d, %d) has no bigram probability"
                            % (w2, w3)
                        )
                    return row[0]

                for hist, pairs in _group_sorted(
                        iter_ng(os.path.join(merged_dir, "3.ng")), 2):
                    w2 = hist[1]

                    def lower(word, _w2=w2):
                        return lower_bi(_w2, word)

                    explicit, bow = discount_history(pairs, discount, lower)
                    found = conn.execute(
                        "SELECT 1 FROM bi WHERE w1=? AND w2=?",
                        (hist[0], hist[1]),
                    ).fetchone()
                    if found is None:
                        raise RuntimeError(
                            "trigram history %r is missing from the bigram table"
                            % (hist,)
                        )
                    conn.execute(
                        "UPDATE bi SET bow=? WHERE w1=? AND w2=?",
                        (bow, hist[0], hist[1]),
                    )
                    for word, pr in sorted(explicit.items()):
                        words = (
                            lexicon.surface(hist[0]),
                            lexicon.surface(hist[1]),
                            lexicon.surface(word),
                        )
                        tri_out.write(format_entry(words, pr, None))
                        tri_out.write("\n")
                conn.commit()

        uni_path = os.path.join(body, "1")
        with open(uni_path, "w", encoding="utf-8") as uni_out:
            for wid in sorted(uni_pr):
                uni_out.write(format_entry(
                    (lexicon.surface(wid),), uni_pr[wid], uni_bow.get(wid, 1.0)
                ))
                uni_out.write("\n")

        bi_path = os.path.join(body, "2")
        with open(bi_path, "w", encoding="utf-8") as bi_out:
            if order >= 2:
                for w1, w2, pr, bow in conn.execute(
                        "SELECT w1, w2, pr, bow FROM bi ORDER BY w1, w2"):
                    bi_out.write(format_entry(
                        (lexicon.surface(w1), lexicon.surface(w2)), pr, bow
                    ))
                    bi_out.write("\n")
    finally:
        conn.close()

    with open(output, "w", encoding="utf-8") as out:
        out.write("\\0-gram\\1\n")
        out.write(format_entry([], root_pr, 1.0))
        out.write("\n")
        for level, path in ((1, uni_path), (2, bi_path), (3, tri_path)):
            _append_section(out, level, path)
    return output


def _append_section(out, level, path):
    count = 0
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    out.write("\\%d-gram\\%d\n" % (level, count))
    if not count:
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                out.write(line if line.endswith("\n") else line + "\n")


def parse_arpa(text):
    """Parse slmpack text. Level 0 is two floats from sscanf.

    Every other row splits on the first two consecutive spaces, as in
    getwords().
    """
    lines = text.splitlines()
    index = 0
    parsed = {"root": None, "levels": {}}
    while index < len(lines):
        header = lines[index]
        index += 1
        if not header.startswith("\\"):
            raise ValueError("expected a section header, got %r" % (header,))
        try:
            level_s, size_s = header[1:].split("-gram\\")
            level = int(level_s)
            size = int(size_s)
        except ValueError:
            raise ValueError("bad section header %r" % (header,)) from None
        entries = []
        for _ in range(size):
            if index >= len(lines):
                raise ValueError("section %d ended early" % level)
            entries.append(_parse_entry(lines[index], level))
            index += 1
        if level == 0:
            if size != 1:
                raise ValueError("level 0 must contain one node")
            parsed["root"] = entries[0]
        else:
            parsed["levels"][level] = entries
    return parsed


def _parse_entry(line, level):
    if level == 0:
        nums = line.split()
        if len(nums) != 2:
            raise ValueError("level 0 row must be pr and bow, got %r" % (line,))
        return ((), float(nums[0]), float(nums[1]))
    split_at = line.find("  ")
    if split_at < 0:
        raise ValueError("missing the two-space separator in %r" % (line,))
    words = tuple(line[:split_at].split(" "))
    if not words or any(not word for word in words):
        raise ValueError("empty word in %r" % (line,))
    nums = line[split_at + 2:].split()
    if level >= SLMPACK_ORDER:
        if len(nums) != 1:
            raise ValueError("leaf row must contain one probability, got %r" % (line,))
        return (words, float(nums[0]), None)
    if len(nums) != 2:
        raise ValueError("backoff row must contain pr and bow, got %r" % (line,))
    return (words, float(nums[0]), float(nums[1]))

# -*- coding: utf-8 -*-
"""Trigram ARPA text in the dialect read by slmpack.

Discounting follows CSlmBuilder. Each order has a cutoff and one
Good-Turing, absolute, or linear discounter. A count at or below the
cutoff is dropped, unless that gram still has a kept extension.
Good-Turing stores nr[r] = r * N_r and discounts frequencies below R;
at R and above the count is multiplied by the high-frequency factor.
Absolute discount uses r' = r - c, and c <= 0 selects
nr[1] / (nr[1] + 2 * nr[2]). A breaker id may start or end an n-gram
and is not stored in the interior. An exclude id is omitted from every
gram that contains it. Level 0 is the uniform 1/|V|. The backoff weight
is (1 - Σ child pr) / (1 - Σ getPr(suffix)).

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

Sections above the requested order are still emitted, empty, so the
trigram reader always sees four headers. Trigram rows are streamed from
the count file. Bigram rows stay in sqlite.
"""

from __future__ import annotations

import os
import sqlite3

from pipeline.lexicon_io import load_dict_utf8
from pipeline.ngram_count import iter_ng

SLMPACK_ORDER = 3
SLM_MAX_R = 16
# slmbuild example: -c 0,3,2 -d GT,8,0.9995 -d ABS -d ABS -b 10 -e 9
# 10 is <stok>, 9 is <amigu> in dict_head.utf8.
DEFAULT_CUTS = (0, 3, 2)
DEFAULT_BREAKERS = (10,)
DEFAULT_EXCLUDES = (9,)
# CArpaSlm reads each row with getline(buf, 1024). That stores at most
# 1023 bytes and fails unless the newline falls inside the window, so the
# payload itself is limited to 1022 bytes.
_GETLINE_PAYLOAD = 1022


class GTDiscounter:
    """Good-Turing for r < R, then r' = r * high_dis.

    nr[r] stores r * N_r, as CSlmBuilder::CountNr does. The ratio
    nr[r+1]/nr[r] is therefore (r+1) N_{r+1} / (r N_r), and r times that
    ratio is the Good-Turing r*.
    """

    def __init__(self, threshold=8, high_dis=0.9995):
        self.threshold = int(threshold)
        self.high_dis = float(high_dis)
        self.dis = None
        self.thres = self.threshold

    def init(self, nr):
        n = SLM_MAX_R - 1
        self.dis = [0.0] * n
        self.thres = self.threshold if self.threshold <= n else n
        for freq in range(1, n):
            nxt = nr[freq + 1] if freq + 1 < len(nr) else 0
            if nr[freq] == 0 or nxt == 0:
                self.dis[freq] = 1.0
            else:
                self.dis[freq] = float(nxt) / float(nr[freq])

    def discount(self, freq):
        factor = self.dis[freq] if freq < self.thres else self.high_dis
        new_freq = freq * factor
        if new_freq >= float(freq):
            new_freq = freq * self.high_dis
        return new_freq


class ABSDiscounter:
    """Absolute discount. c <= 0 selects n1 / (n1 + 2 n2) from nr[]."""

    def __init__(self, c=0.0):
        self.c = float(c)

    def init(self, nr):
        if self.c <= 0.0:
            denom = float(nr[1]) + 2.0 * float(nr[2])
            self.c = (float(nr[1]) / denom) if denom > 0.0 else 0.0

    def discount(self, freq):
        if freq <= 0:
            return 0.0
        return freq - self.c


class LINDiscounter:
    """Linear discount. A factor outside (0, 1) selects 1 - nr[1]/nr[0]."""

    def __init__(self, dis=0.0):
        self.dis = float(dis)

    def init(self, nr):
        if self.dis <= 0.0 or self.dis >= 1.0:
            self.dis = 1.0 - (float(nr[1]) / float(nr[0])) if nr[0] else 1.0

    def discount(self, freq):
        return freq * self.dis


def default_discounts():
    return (GTDiscounter(8, 0.9995), ABSDiscounter(0.0), ABSDiscounter(0.0))


def parse_discount(text):
    """Parse one slmbuild ``-d`` argument: GT,R,dis | ABS[,c] | LIN[,d]."""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        raise ValueError("empty discount specification")
    kind = parts[0].upper()
    if kind == "GT":
        if len(parts) != 3:
            raise ValueError("GT discount needs R and dis, e.g. GT,8,0.9995")
        return GTDiscounter(int(parts[1]), float(parts[2]))
    if kind == "ABS":
        return ABSDiscounter(float(parts[1]) if len(parts) > 1 else 0.0)
    if kind == "LIN":
        return LINDiscounter(float(parts[1]) if len(parts) > 1 else 0.0)
    raise ValueError("unknown discount %r" % (text,))


def _nr_add(nr, freq):
    nr[0] += freq
    if 0 < freq < SLM_MAX_R:
        nr[freq] += freq


def _admit(gram, breakers, excludes):
    """Breaker ids may end an n-gram. They are dropped from the interior.

    An exclude id removes every n-gram that contains it. This is the
    effect of CSlmBuilder::AddNGram stopping at those ids.
    """
    if any(wid in excludes for wid in gram):
        return False
    if len(gram) >= 3 and any(wid in breakers for wid in gram[1:-1]):
        return False
    return True


def _empty_nr():
    return [[0] * SLM_MAX_R for _ in range(SLMPACK_ORDER + 1)]


def node_bow(child_prs, lower_prs):
    """CSlmBuilder::CalcNodeBow, direct probabilities."""
    if not child_prs:
        return 1.0
    sumnext = float(sum(child_prs))
    lower = float(sum(lower_prs))
    if sumnext <= 0.0:
        return 1.0
    if sumnext >= 1.0 or lower >= 1.0:
        base = max(sumnext, lower) + 0.0001
        return (base - sumnext) / (base - lower)
    return (1.0 - sumnext) / (1.0 - lower)


def conditional_pr(discounter, freq, parent_freq):
    """Discounted count over the history node's frequency.

    CSlmBuilder asserts that this lies in (0, 1). A non-positive result,
    or a result of 1 or more, is omitted instead of aborting.
    """
    if parent_freq <= 0:
        return None
    new_freq = discounter.discount(freq)
    if new_freq <= 0.0:
        return None
    pr = new_freq / float(parent_freq)
    if pr <= 0.0 or pr >= 1.0:
        return None
    return pr


def builder_get_pr(words, lookup, bows, root_pr):
    """CSlmBuilder::getPr.

    The search loop increments the level after a failed child lookup, so
    ``lvl == n - 1`` is the miss one step above the predicted word. A miss
    on the predicted word itself recurses to the suffix without multiplying
    that history's backoff weight.
    """
    n = len(words)
    if n == 0:
        return root_pr
    bow = 1.0
    hist = ()
    found = True
    level = 0
    pr = None
    while found and level < n:
        bow = bows.get(hist, 1.0)
        hist = hist + (words[level],)
        pr = lookup(hist)
        found = pr is not None
        level += 1
    if found:
        return pr
    if level == n - 1:
        return bow * builder_get_pr(words[1:], lookup, bows, root_pr)
    return builder_get_pr(words[1:], lookup, bows, root_pr)


def estimate_tables(counts, word_count, cuts=DEFAULT_CUTS, discounts=None,
                    breakers=DEFAULT_BREAKERS, excludes=DEFAULT_EXCLUDES):
    """In-memory CSlmBuilder estimate.

    ``counts[n]`` maps an id tuple to a count. Node frequency is that
    count. The unigram denominator is the sum of admitted unigram counts.
    Returns ``(probs, bows, root_pr)``. ``write_arpa`` is the out-of-core
    path and is checked against this function. ``init`` is called on each
    discounter.
    """
    if word_count < 1:
        raise ValueError("word count must be the lexicon size, at least 1")
    discs = list(discounts) if discounts is not None else list(default_discounts())
    if len(discs) != SLMPACK_ORDER or len(cuts) != SLMPACK_ORDER:
        raise ValueError("need one cutoff and one discount per order")
    breakers = frozenset(breakers)
    excludes = frozenset(excludes)
    kept_counts = [dict() for _ in range(SLMPACK_ORDER + 1)]
    nr = _empty_nr()
    for n in range(1, SLMPACK_ORDER + 1):
        for gram, freq in counts[n].items():
            if not _admit(gram, breakers, excludes):
                continue
            kept_counts[n][gram] = freq
            _nr_add(nr[n], freq)
    for disc, row in zip(discs, nr[1:]):
        disc.init(row)

    root_freq = nr[1][0]
    root_pr = 1.0 / float(word_count)
    probs = {}

    def parent_freq(gram):
        if len(gram) == 1:
            return root_freq
        return kept_counts[len(gram) - 1].get(gram[:-1], 0)

    surviving = [set() for _ in range(SLMPACK_ORDER + 1)]
    for gram, freq in kept_counts[3].items():
        if freq > cuts[2]:
            surviving[3].add(gram)
    bi_prefix = {gram[:2] for gram in surviving[3]}
    for gram, freq in kept_counts[2].items():
        if freq > cuts[1] or gram in bi_prefix:
            surviving[2].add(gram)
    uni_prefix = {gram[:1] for gram in surviving[2]}
    for gram, freq in kept_counts[1].items():
        if freq > cuts[0] or gram in uni_prefix:
            surviving[1].add(gram)

    for n in range(1, SLMPACK_ORDER + 1):
        for gram in surviving[n]:
            pr = conditional_pr(discs[n - 1], kept_counts[n][gram], parent_freq(gram))
            if pr is not None:
                probs[gram] = pr

    children = {}
    for gram, pr in probs.items():
        children.setdefault(gram[:-1], []).append((gram[-1], pr))
    lookup = probs.get
    bows = {}

    def lower_pr(hist, word):
        if not hist:
            return root_pr
        return builder_get_pr(hist[1:] + (word,), lookup, bows, root_pr)

    root_children = children.get((), [])
    bows[()] = node_bow(
        [pr for _word, pr in root_children],
        [lower_pr((), word) for word, _pr in root_children],
    )
    for length in (1, 2):
        for hist in [gram for gram in probs if len(gram) == length]:
            kids = children.get(hist, [])
            bows[hist] = node_bow(
                [pr for _word, pr in kids],
                [lower_pr(hist, word) for word, _pr in kids],
            )
    return probs, bows, root_pr


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


def render_model(probs, bows, lexicon, root_pr):
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


_SQL_BATCH = 10000


def _executemany(conn, sql, rows):
    pending = []
    for row in rows:
        pending.append(row)
        if len(pending) >= _SQL_BATCH:
            conn.executemany(sql, pending)
            del pending[:]
    if pending:
        conn.executemany(sql, pending)


def write_arpa(merged_dir, dict_utf8, output, order=SLMPACK_ORDER,
               cuts=DEFAULT_CUTS, discounts=None,
               breakers=DEFAULT_BREAKERS, excludes=DEFAULT_EXCLUDES,
               word_count=None):
    """Stream merged counts into text ARPA.

    Unigrams stay in a dict. Bigrams stay in sqlite. Trigrams are read
    twice and are not retained. The probabilities match ``estimate_tables``.
    """
    if order < 1 or order > SLMPACK_ORDER:
        raise ValueError("order must be in 1..%d" % SLMPACK_ORDER)
    discs = list(discounts) if discounts is not None else list(default_discounts())
    cuts = tuple(cuts)
    if len(discs) != order or len(cuts) != order:
        raise ValueError("need one cutoff and one discount per order")
    breakers = frozenset(breakers)
    excludes = frozenset(excludes)
    lexicon = load_dict_utf8(dict_utf8)
    if word_count is None:
        word_count = len(lexicon.word_to_id)
    if word_count < 1:
        raise ValueError("word count must be the lexicon size, at least 1")
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    body = os.path.join(merged_dir, "arpa-body")
    os.makedirs(body, exist_ok=True)

    nr = _empty_nr()
    uni_freq = {}
    for gram, freq in iter_ng(os.path.join(merged_dir, "1.ng")):
        if not _admit(gram, breakers, excludes):
            continue
        uni_freq[gram[0]] = freq
        _nr_add(nr[1], freq)

    db_path = os.path.join(merged_dir, "bigram.sqlite")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE bi ("
            "w1 INTEGER NOT NULL, w2 INTEGER NOT NULL, freq INTEGER NOT NULL, "
            "pr REAL, bow REAL NOT NULL DEFAULT 1.0, has_child INTEGER NOT NULL "
            "DEFAULT 0, PRIMARY KEY (w1, w2))"
        )
        if order >= 2:
            def bigram_rows():
                for gram, freq in iter_ng(os.path.join(merged_dir, "2.ng")):
                    if not _admit(gram, breakers, excludes):
                        continue
                    _nr_add(nr[2], freq)
                    yield (gram[0], gram[1], freq)

            _executemany(
                conn,
                "INSERT INTO bi (w1, w2, freq) VALUES (?, ?, ?)",
                bigram_rows(),
            )
        if order >= 3:
            conn.execute(
                "CREATE TABLE hist ("
                "w1 INTEGER NOT NULL, w2 INTEGER NOT NULL, "
                "PRIMARY KEY (w1, w2))"
            )

            def trigram_histories():
                for gram, freq in iter_ng(os.path.join(merged_dir, "3.ng")):
                    if not _admit(gram, breakers, excludes):
                        continue
                    _nr_add(nr[3], freq)
                    if freq > cuts[2]:
                        yield (gram[0], gram[1])

            _executemany(
                conn,
                "INSERT OR IGNORE INTO hist (w1, w2) VALUES (?, ?)",
                trigram_histories(),
            )
            missing = conn.execute(
                "SELECT COUNT(*) FROM hist AS h LEFT JOIN bi "
                "ON bi.w1 = h.w1 AND bi.w2 = h.w2 WHERE bi.w1 IS NULL"
            ).fetchone()[0]
            if missing:
                raise RuntimeError(
                    "%d trigram histories are missing from the bigram counts"
                    % missing
                )
            conn.execute(
                "UPDATE bi SET has_child = 1 WHERE (w1, w2) IN "
                "(SELECT w1, w2 FROM hist)"
            )
            conn.execute("DROP TABLE hist")
        conn.commit()

        for index in range(order):
            discs[index].init(nr[index + 1])
        root_freq = nr[1][0]
        root_pr = 1.0 / float(word_count)

        prefix_uni = set()
        if order >= 2:
            for (wid,) in conn.execute(
                    "SELECT DISTINCT w1 FROM bi WHERE freq > ? OR has_child = 1",
                    (cuts[1],)):
                prefix_uni.add(wid)
        uni_pr = {}
        for wid, freq in uni_freq.items():
            if freq > cuts[0] or wid in prefix_uni:
                pr = conditional_pr(discs[0], freq, root_freq)
                if pr is not None:
                    uni_pr[wid] = pr
        if order >= 2:
            def bigram_prs():
                for w1, w2, freq in conn.execute(
                        "SELECT w1, w2, freq FROM bi "
                        "WHERE freq > ? OR has_child = 1",
                        (cuts[1],)):
                    pr = conditional_pr(discs[1], freq, uni_freq.get(w1, 0))
                    if pr is not None:
                        yield (pr, w1, w2)

            _executemany(
                conn,
                "UPDATE bi SET pr = ? WHERE w1 = ? AND w2 = ?",
                bigram_prs(),
            )
            conn.commit()

        bows = {}
        bows[()] = node_bow(list(uni_pr.values()), [root_pr] * len(uni_pr))
        uni_bow = {wid: 1.0 for wid in uni_pr}

        def lookup(gram):
            if len(gram) == 1:
                return uni_pr.get(gram[0])
            if len(gram) == 2:
                row = conn.execute(
                    "SELECT pr FROM bi WHERE w1 = ? AND w2 = ?",
                    (gram[0], gram[1]),
                ).fetchone()
                if row is None:
                    return None
                return row[0]
            return None

        if order >= 2 and uni_pr:
            current = None
            child_prs = []
            lower = []

            def flush_unigram(wid):
                if wid is None or wid not in uni_pr:
                    return
                uni_bow[wid] = node_bow(child_prs, lower)

            for w1, w2, pr in conn.execute(
                    "SELECT w1, w2, pr FROM bi WHERE pr IS NOT NULL "
                    "ORDER BY w1, w2"):
                if w1 != current:
                    flush_unigram(current)
                    current = w1
                    child_prs = []
                    lower = []
                child_prs.append(pr)
                lower.append(builder_get_pr((w2,), lookup, bows, root_pr))
            flush_unigram(current)
        for wid, bow in uni_bow.items():
            bows[(wid,)] = bow

        tri_path = os.path.join(body, "3")
        with open(tri_path, "w", encoding="utf-8") as tri_out:
            if order >= 3:
                for hist, pairs in _group_sorted(
                        iter_ng(os.path.join(merged_dir, "3.ng")), 2):
                    kept = []
                    for word, freq in pairs:
                        gram = hist + (word,)
                        if freq > cuts[2] and _admit(gram, breakers, excludes):
                            kept.append((word, freq))
                    if not kept:
                        continue
                    parent_row = conn.execute(
                        "SELECT freq FROM bi WHERE w1 = ? AND w2 = ?",
                        (hist[0], hist[1]),
                    ).fetchone()
                    if parent_row is None:
                        raise RuntimeError(
                            "trigram history %r is missing from the bigram counts"
                            % (hist,)
                        )
                    child_prs = []
                    lower = []
                    emitted = []
                    for word, freq in kept:
                        pr = conditional_pr(discs[2], freq, parent_row[0])
                        if pr is None:
                            continue
                        child_prs.append(pr)
                        lower.append(builder_get_pr(
                            (hist[1], word), lookup, bows, root_pr,
                        ))
                        emitted.append((word, pr))
                    if not emitted:
                        continue
                    conn.execute(
                        "UPDATE bi SET bow = ? WHERE w1 = ? AND w2 = ?",
                        (node_bow(child_prs, lower), hist[0], hist[1]),
                    )
                    for word, pr in emitted:
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
                        "SELECT w1, w2, pr, bow FROM bi WHERE pr IS NOT NULL "
                        "ORDER BY w1, w2"):
                    bi_out.write(format_entry(
                        (lexicon.surface(w1), lexicon.surface(w2)), pr, bow
                    ))
                    bi_out.write("\n")
    finally:
        conn.close()

    with open(output, "w", encoding="utf-8") as out:
        out.write("\\0-gram\\1\n")
        out.write(format_entry([], root_pr, bows.get((), 1.0)))
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

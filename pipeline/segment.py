# -*- coding: utf-8 -*-
"""Segment sentences against dict.utf8.

The default segmenter is forward maximum matching. Text that the lexicon
does not list is mapped to the class tokens already defined in
dict_head.utf8:

  ASCII or fullwidth digit run  -> <Digit>
  other non-Han symbol           -> <Simbol>
  unmatched Han or Latin run     -> <unknown>

Tokens whose surface starts with ``<`` are not trie entries.

``preseg`` accepts slash-separated or whitespace-separated text and
rewrites it with slashes. ``crf`` is a subprocess of
segment/tagging/baseseg.py and requires a model path. Nothing under
tools/CRF++-0.53/ is modified.
"""

from __future__ import annotations

import os
import subprocess
import sys

from pipeline.lexicon_build import repo_root
from pipeline.lexicon_io import load_dict_utf8

_END = None


class _Trie:
    def __init__(self):
        self.root = {}

    def add(self, word):
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node[_END] = True

    def longest(self, text, index):
        node = self.root
        best = None
        j = index
        n = len(text)
        while j < n:
            node = node.get(text[j])
            if node is None:
                break
            j += 1
            if _END in node:
                best = j
        return best


def _is_cjk(ch):
    code = ord(ch)
    return (
        0x3400 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3FFFF
    )


def _is_digit_class(ch):
    code = ord(ch)
    return 0x30 <= code <= 0x39 or 0xFF10 <= code <= 0xFF19


def _is_latin(ch):
    code = ord(ch)
    return (
        0x41 <= code <= 0x5A
        or 0x61 <= code <= 0x7A
        or 0xFF21 <= code <= 0xFF3A
        or 0xFF41 <= code <= 0xFF5A
    )


def _is_space(ch):
    return ch.isspace()


def trie_from_lexicon(lexicon):
    trie = _Trie()
    for word in lexicon.word_to_id:
        if word.startswith("<"):
            continue
        trie.add(word)
    return trie


def segment_sentence(text, trie):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        if _is_space(text[i]):
            i += 1
            continue
        end = trie.longest(text, i)
        if end is not None and end > i:
            tokens.append(text[i:end])
            i = end
            continue
        if _is_digit_class(text[i]):
            j = i + 1
            while j < n and _is_digit_class(text[j]):
                j += 1
            tokens.append("<Digit>")
            i = j
            continue
        if _is_cjk(text[i]) or _is_latin(text[i]):
            kind = _is_cjk if _is_cjk(text[i]) else _is_latin
            tokens.append("<unknown>")
            i = _consume_oov_run(text, i, trie, kind)
            continue
        tokens.append("<Simbol>")
        i += 1
    return tokens


def _consume_oov_run(text, index, trie, kind):
    j = index + 1
    n = len(text)
    while j < n and kind(text[j]):
        if trie.longest(text, j) is not None:
            break
        j += 1
    return j


def segment_text_file(src, dest, lexicon):
    trie = trie_from_lexicon(lexicon)
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    written = 0
    with open(src, "r", encoding="utf-8") as inp, open(dest, "w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            tokens = segment_sentence(line, trie)
            if not tokens:
                continue
            out.write("/".join(tokens))
            out.write("\n")
            written += 1
    return written


def normalize_presegmented(src, dest):
    """Rewrite presegmented lines with the slash separator baseseg prints."""
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    written = 0
    with open(src, "r", encoding="utf-8") as inp, open(dest, "w", encoding="utf-8") as out:
        for line in inp:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "/" in line:
                tokens = [part for part in line.split("/") if part]
            else:
                tokens = line.split()
            if not tokens:
                continue
            out.write("/".join(tokens))
            out.write("\n")
            written += 1
    return written


def crf_command(model_path, input_path):
    script = os.path.join(repo_root(), "segment", "tagging", "baseseg.py")
    return [sys.executable, script, "-m", model_path, "-i", input_path]


def segment_with_crf(src, dest, model_path):
    if not model_path:
        raise RuntimeError("CRF segmentation requires --crf-model")
    if not os.path.isfile(model_path):
        raise RuntimeError("CRF model not found: %s" % model_path)
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    command = crf_command(model_path, src)
    with open(dest, "w", encoding="utf-8") as out:
        try:
            subprocess.run(command, check=True, stdout=out)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "CRF segmenter exited %d" % exc.returncode
            ) from exc
    return dest


def segment_file(src, dest, dict_utf8, segmenter="match", crf_model=None):
    if segmenter == "preseg":
        return normalize_presegmented(src, dest)
    if segmenter == "crf":
        return segment_with_crf(src, dest, crf_model)
    if segmenter != "match":
        raise ValueError("unknown segmenter %r" % (segmenter,))
    lexicon = load_dict_utf8(dict_utf8)
    for required in ("<unknown>", "<Digit>", "<Simbol>"):
        lexicon.require(required)
    return segment_text_file(src, dest, lexicon)

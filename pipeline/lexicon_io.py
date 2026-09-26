# -*- coding: utf-8 -*-
"""dict.utf8 as a word-to-id map.

The second field is the integer id stored by slmpack. A repeated surface
keeps the later id, matching slmpack's std::map.
"""

from __future__ import annotations


class Lexicon:
    def __init__(self):
        self.word_to_id = {}
        self.id_to_word = {}

    def add(self, word, wid):
        if not word or any(ch.isspace() for ch in word):
            raise ValueError("lexicon word must be a single token, got %r" % (word,))
        prev = self.word_to_id.get(word)
        if prev is not None and prev != wid and self.id_to_word.get(prev) == word:
            del self.id_to_word[prev]
        self.word_to_id[word] = wid
        self.id_to_word[wid] = word

    def require(self, word):
        try:
            return self.word_to_id[word]
        except KeyError:
            raise KeyError("token %r is not in the lexicon" % (word,)) from None

    def surface(self, wid):
        try:
            return self.id_to_word[wid]
        except KeyError:
            raise KeyError("no surface form for word id %d" % wid) from None


def load_dict_utf8(path):
    lexicon = Lexicon()
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise ValueError(
                    "%s:%d: expected 'word id ...', got %r" % (path, line_no, line)
                )
            lexicon.add(parts[0], int(parts[1]))
    if not lexicon.word_to_id:
        raise ValueError("empty dictionary %s" % path)
    return lexicon

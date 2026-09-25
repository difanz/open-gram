# -*- coding: utf-8 -*-
"""Build dict.utf8 from dict.full and lexicon/dict_head.utf8.

Numbering is add_id.py: head ids are kept, and dict.full receives
100, 101, ... in file order. CC-CEDICT is not an input.
"""

from __future__ import annotations

import importlib.util
import os


def repo_root():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _add_id():
    path = os.path.join(repo_root(), "lexicon", "add_id.py")
    spec = importlib.util.spec_from_file_location("opengram_add_id", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_dict_head():
    return os.path.join(repo_root(), "lexicon", "dict_head.utf8")


def default_dict_full():
    return os.path.join(repo_root(), "data", "dict.full")


def build_dict_utf8(dict_full, output, dict_head=None):
    head = dict_head or default_dict_head()
    parent = os.path.dirname(os.path.abspath(output))
    if parent:
        os.makedirs(parent, exist_ok=True)
    _add_id().main(dict_full, output, head)
    return output

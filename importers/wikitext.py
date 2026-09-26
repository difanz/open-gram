# -*- coding: utf-8 -*-
"""MediaWiki source to plain text for the sentence splitter.

Templates, citation tags, wiki tables, and file or category links are
removed. An ordinary wikilink keeps its visible label (or the target
title when it has no label). ``{{lang}}`` keeps its last parameter when
that parameter contains Han, so the word being named survives.
OpenCC and the final sentence filter stay in ``importers.wikimedia``.
"""

from __future__ import annotations

import re

import mwparserfromhell
from mwparserfromhell.parser import ParserError

# ASCII prefixes are interwiki codes and English namespace names (File,
# Category, Wikipedia, Help). Chinese namespace names are listed beside them.
_NAMED_PREFIXES = frozenset({
    "category", "file", "image", "media", "wikipedia", "template", "help",
    "portal", "draft", "module", "timedtext", "mediawiki", "special", "user",
    "talk", "project", "wp",
    "分类", "分類", "文件", "档案", "檔案", "图像", "圖像", "图片", "圖片",
    "媒体", "媒體", "维基百科", "維基百科", "模板", "帮助", "幫助",
    "主题", "主題", "草稿", "模块", "模組", "专题", "專題",
})
_ASCII_PREFIX = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
# Visible to mwparserfromhell's strip_code, but not running prose.
_DROP_TAGS = frozenset({
    "ref", "references", "gallery", "math", "score", "timeline", "imagemap",
    "syntaxhighlight", "source", "pre", "poem", "graph", "inputbox",
    "categorytree", "templatedata", "chem", "ce", "hiero",
})
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MAGIC = re.compile(r"__[A-Za-z][A-Za-z0-9_]*__")
_TAG_BLOCK = re.compile(
    r"<\s*(ref|references|gallery|math|syntaxhighlight|source|pre)\b[^>]*>"
    r".*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_SELF = re.compile(
    r"<\s*(ref|references|gallery)\b[^>]*/>",
    re.IGNORECASE,
)
_EMPTY_BRACKETS = re.compile(r"（\s*）|\(\s*\)|\[\s*\]|［\s*］")
_PX = re.compile(r"\b\d+px\b", re.IGNORECASE)
_CLASS_ATTR = re.compile(r"\bclass\s*=\s*[\w.-]+", re.IGNORECASE)
_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]|[\U00020000-\U0003ffff]")
# MediaWiki language-converter rules, not templates: -{zh-cn:简;zh-tw:繁}-
_CONVERTER = re.compile(r"-{([^{}]*)}-")
_CONVERTER_PREF = ("zh-cn", "zh-hans", "zh-sg", "zh-my")


def has_han(text):
    return _HAN.search(text) is not None


def _converter_replacement(inner):
    """Prefer a simplified variant. A rule with no labels keeps its text."""
    inner = inner.strip()
    if not inner or ":" not in inner:
        return inner
    found = {}
    for part in inner.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        found[key.strip().lower()] = value.strip()
    if not found:
        return inner
    for key in _CONVERTER_PREF:
        if found.get(key):
            return found[key]
    return next(iter(found.values()))


def unwrap_converter_rules(text):
    """Replace ``-{...}-`` rules. Inner rules are expanded first."""
    previous = None
    while previous != text:
        previous = text
        text = _CONVERTER.sub(lambda match: _converter_replacement(match.group(1)), text)
    return text


def _table_end(text, start):
    """Exclusive end of the table that begins at ``start``, or None if unclosed."""
    depth = 0
    i = start
    limit = len(text) - 1
    while i < limit:
        if text.startswith("{|", i):
            depth += 1
            i += 2
            continue
        if text.startswith("|}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        i += 1
    return None


def remove_wiki_tables(text):
    """Drop ``{| ... |}`` blocks, including nested tables.

    An unclosed ``{|`` is left in place so the rest of the page is kept.
    """
    parts = []
    i = 0
    while True:
        start = text.find("{|", i)
        if start < 0:
            parts.append(text[i:])
            break
        parts.append(text[i:start])
        end = _table_end(text, start)
        if end is None:
            parts.append(text[start:])
            break
        i = end
    return "".join(parts)


def _link_prefix(title):
    title = title.strip()
    if title.startswith(":"):
        title = title[1:].lstrip()
    if ":" not in title:
        return None
    return title.split(":", 1)[0].strip()


def _drop_link(title):
    prefix = _link_prefix(title)
    if prefix is None:
        return False
    if prefix in _NAMED_PREFIXES or prefix.lower() in _NAMED_PREFIXES:
        return True
    return _ASCII_PREFIX.fullmatch(prefix) is not None


def _template_name(tmpl):
    name = str(tmpl.name).strip().lower()
    if name.startswith("template:"):
        name = name[len("template:"):]
    return name.split(":", 1)[-1].strip()


def _is_lang_template(name):
    return name == "lang" or name.startswith("lang-") or name.startswith("langx")


def _strip_parsed(text):
    code = mwparserfromhell.parse(text)
    for tag in list(code.filter_tags(recursive=True)):
        if str(tag.tag).strip().lower() in _DROP_TAGS:
            try:
                code.remove(tag)
            except ValueError:
                pass
    for link in list(code.filter_wikilinks(recursive=True)):
        try:
            title = str(link.title)
        except ValueError:
            continue
        if _drop_link(title):
            try:
                code.remove(link)
            except ValueError:
                pass
    # Top-level lang templates only. A lang template inside a citation is
    # removed with that citation.
    for tmpl in list(code.filter_templates(recursive=False)):
        if not _is_lang_template(_template_name(tmpl)) or not tmpl.params:
            continue
        value = tmpl.params[-1].value
        if not has_han(str(value)):
            continue
        try:
            code.replace(tmpl, value)
        except ValueError:
            pass
    return code.strip_code(normalize=True, collapse=True, keep_template_params=False)


def _scrub_leftovers(text):
    text = _MAGIC.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _TAG_BLOCK.sub(" ", text)
    text = _TAG_SELF.sub(" ", text)
    text = _PX.sub(" ", text)
    text = _CLASS_ATTR.sub(" ", text)
    text = _EMPTY_BRACKETS.sub("", text)
    return text


def tidy_plain(text):
    """Collapse horizontal space and drop lines that are only table chrome."""
    lines = []
    for line in text.splitlines():
        line = line.replace("\u00a0", " ").replace("\u3000", " ")
        line = re.sub(r"[ \t]+", " ", line).strip()
        line = re.sub(r"^[*#;:]+", "", line).strip()
        if not line or line[0] in "|!":
            continue
        if line.startswith("{|") or line.startswith("|}"):
            continue
        lines.append(line)
    return "\n".join(lines)


def strip_wikitext(text):
    """Return prose with MediaWiki markup removed. Newlines separate paragraphs."""
    if not text:
        return ""
    plain = unwrap_converter_rules(text)
    plain = remove_wiki_tables(plain)
    try:
        plain = _strip_parsed(plain)
    except (ParserError, ValueError):
        # Converter rules and tables are already gone. Tag scrubs still run.
        pass
    plain = _scrub_leftovers(plain)
    return tidy_plain(plain)

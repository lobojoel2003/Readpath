# SPDX-License-Identifier: Apache-2.0
"""Guess a document's language from its text.

Small and dependency-free: common-word scores for Latin-script languages and
Unicode script ranges for the rest. Good enough to fill in a missing /Lang,
which a person can confirm in the review.
"""

from __future__ import annotations

import re
from collections import Counter

_WORDS = {
    "en-US": "the and of to in is that for it with as was on be by this are or from at an have not you your will which all their can has".split(),
    "es": "el la de que y en los del las un por con una para es se al lo como más pero sus le ya o este sí porque esta entre cuando muy sin sobre también".split(),
    "fr": "le la les de des et est un une du en que qui dans pour pas sur au avec ce il ne se par plus sont ou vous nous".split(),
    "de": "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden aus er hat dass sie".split(),
    "pt": "o a os as de do da dos das que e em um uma para com não por mais se como mas foi ao ele das tem à seu sua ou".split(),
    "it": "il di che e la per un in del della non sono una le si con da gli al nel anche come più ma questo ha".split(),
    "vi": "và của là có trong được cho các những một không này với người đã để khi từ theo về".split(),
    "tl": "ang ng sa na at mga ay para ito ni si kung hindi may mga ako ka siya kami tayo".split(),
    "ht": "ak nan pou yo li se ki pa sa nou ou mwen gen la te nan tout fè".split(),
    "nl": "de het een en van in is dat op te zijn voor met niet aan er die als bij".split(),
    "pl": "i w na z że do się nie to jest jak o po co tak za przez".split(),
}

_SCRIPTS = [
    ("zh", re.compile(r"[一-鿿]")),
    ("ja", re.compile(r"[぀-ヿ]")),
    ("ko", re.compile(r"[가-힯]")),
    ("ar", re.compile(r"[؀-ۿ]")),
    ("ru", re.compile(r"[Ѐ-ӿ]")),
    ("hy", re.compile(r"[԰-֏]")),
    ("fa", re.compile(r"[پچژگ]")),
    ("he", re.compile(r"[֐-׿]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("th", re.compile(r"[฀-๿]")),
    ("km", re.compile(r"[ក-៿]")),
    ("am", re.compile(r"[ሀ-፿]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
]

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def is_valid_lang(tag: str | None) -> bool:
    """Loose BCP 47 check (e.g. 'en', 'en-US', 'zh-Hant')."""
    return bool(tag) and bool(_LANG_RE.match(tag.strip()))  # type: ignore[union-attr]


def detect_language(text: str) -> tuple[str | None, float]:
    """Return (BCP 47 tag, confidence 0..1). None when there is too little text."""
    sample = text[:200000]
    letters = sum(1 for ch in sample if ch.isalpha())
    if letters < 40:
        return None, 0.0
    for tag, rx in _SCRIPTS:
        hits = len(rx.findall(sample))
        if hits / letters > 0.3:
            if tag == "ar" and len(_SCRIPTS[6][1].findall(sample)) > hits * 0.02:
                return "fa", 0.7
            return tag, min(1.0, hits / letters + 0.3)
    words = Counter(re.findall(r"[^\W\d_]+", sample.lower()))
    total = sum(words.values()) or 1
    scores = {tag: sum(words[w] for w in vocab) / total for tag, vocab in _WORDS.items()}
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    ranked = sorted(scores.values(), reverse=True)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0)
    if ranked[0] < 0.05:
        return None, 0.0
    return best, max(0.0, min(1.0, margin * 5 + ranked[0]))

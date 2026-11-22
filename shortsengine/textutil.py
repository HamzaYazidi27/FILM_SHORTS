"""Small text utilities for similarity, fingerprints and claim checks."""
from __future__ import annotations

import hashlib
import re
import unicodedata

STOP = set("""a an the and or but if of to in on at for with by from as is are was were be been being it its this that
these those i you he she we they me him her us them my your his their our not no do does did have has had so than then
just very can could would should will shall may might must there here what which who whom whose when where why how all
any some""".split())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[‘’´`]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"[–—]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str, drop_stop: bool = True) -> list[str]:
    t = re.findall(r"[a-z0-9']+", norm(s))
    return [w for w in t if not (drop_stop and w in STOP)]


def jaccard(a: str, b: str) -> float:
    A, B = set(tokens(a)), set(tokens(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def shingles(s: str, k: int = 4) -> set[tuple]:
    t = tokens(s, drop_stop=False)
    return {tuple(t[i:i + k]) for i in range(max(0, len(t) - k + 1))}


def shingle_sim(a: str, b: str, k: int = 4) -> float:
    A, B = shingles(a, k), shingles(b, k)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def fingerprint(*parts: str) -> str:
    key = " ".join(sorted(set(tokens(" ".join(parts)))))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}


def quote_in_text(quote: str, text: str) -> bool:
    """True if the quote appears in text after normalising whitespace/punctuation/case."""
    clean = lambda x: re.sub(r"[^a-z0-9]+", " ", norm(x)).strip()
    q = clean(quote)
    return len(q) >= 12 and q in clean(text)


SPECULATIVE = re.compile(r"\b(allegedly|reportedly|rumou?red|may have|might have|possibly|unconfirmed|it is believed|speculat)\w*",
                         re.I)


def is_speculative(s: str) -> bool:
    return bool(SPECULATIVE.search(s))


def factual_numbers(s: str) -> set[str]:
    """Numbers that look like factual specifics (years, amounts, percentages, >= 3 digits).
    Everyday small numbers ('seat 3', 'two minutes') are not treated as claims."""
    out = set()
    for m in re.finditer(r"([$£€]\s?)?(\d[\d,]*(?:\.\d+)?)\s?(%|percent|million|billion|thousand)?", s, re.I):
        n = m.group(2).replace(",", "")
        try:
            val = float(n)
        except ValueError:
            continue
        if m.group(1) or m.group(3) or val >= 100 or "." in n:
            out.add(n)
    return out

"""Qatari entity-name normalisation.

Company names on Monaqasat, in bank narrations and in QDB's own KYC file are
written differently every time. This collapses the variation so that two
spellings of the same company produce the same key.

Same approach as altdata/normalize.py -- kept here so this package stands
alone.
"""

from __future__ import annotations

import re
import unicodedata

# Legal forms. Stripped for the comparison key, kept in the display name.
LEGAL_SUFFIXES = {
    "wll", "w l l", "w.l.l", "llc", "l l c", "l.l.c", "qsc", "q s c",
    "qpsc", "qfc", "spc", "s p c", "wpc", "psc", "plc", "ltd", "limited",
    "co", "company", "est", "establishment", "corp", "corporation",
    "holding", "holdings", "group", "grp", "intl", "international",
    "trdg", "trad",
}

ABBREVIATIONS = {
    "trdg": "trading", "trad": "trading", "constr": "construction",
    "const": "construction", "contg": "contracting", "cont": "contracting",
    "mfg": "manufacturing", "ind": "industries", "inds": "industries",
    "indl": "industrial", "svcs": "services", "svc": "services",
    "serv": "services", "tech": "technology", "eng": "engineering",
    "engg": "engineering", "equip": "equipment", "elec": "electrical",
    "mech": "mechanical", "transp": "transport", "trnsp": "transport",
    "maint": "maintenance", "gen": "general", "intl": "international",
    "dev": "development", "mgmt": "management", "sol": "solutions",
    "solns": "solutions", "med": "medical", "phar": "pharmacy",
    "rest": "restaurant", "cnt": "centre", "ctr": "centre",
    "center": "centre", "est": "establishment",
}

# Bank-narration debris. Harmless on registry names, essential on narrations.
NOISE_PATTERNS = [
    r"\bref\s*[:#]?\s*\w+\b",
    r"\btrf\s+(from|to)\b",
    r"\btransfer\s+(from|to)\b",
    r"\bpayment\s+(from|to)\b",
    r"\binward\s+remittance\b",
    r"\boutward\s+remittance\b",
    r"\bsalary\b", r"\bcheque\s*no\b", r"\bchq\b",
    r"\bvalue\s*date\b", r"\binv(oice)?\s*no\.?\s*\w+\b",
    r"\b\d{6,}\b",                      # long reference numbers
    r"\bqa\d{2}[a-z0-9]{10,}\b",        # IBANs
]

STOPWORDS = {"the", "of", "and", "for", "al", "el"}

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_DOTTED = re.compile(r"\b(?:[a-z]\.){2,}[a-z]?\b")
_SPACED = re.compile(r"\b(?:[a-z] ){1,}[a-z]\b")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def normalize_name(raw: str) -> str:
    """Lower-cased, de-noised, expanded, legal-form-stripped name."""
    if not raw:
        return ""
    s = _strip_accents(str(raw)).lower()

    for pat in NOISE_PATTERNS:
        s = re.sub(pat, " ", s)

    # "w.l.l" -> "wll", "w l l" -> "wll"
    s = _DOTTED.sub(lambda m: m.group(0).replace(".", ""), s)
    s = _NON_ALNUM.sub(" ", s)
    s = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), s)
    s = _WS.sub(" ", s).strip()

    words = []
    for w in s.split():
        w = ABBREVIATIONS.get(w, w)
        words.append(w)

    # Strip trailing legal forms, repeatedly (".. trading co wll")
    while words and (words[-1] in LEGAL_SUFFIXES
                     or " ".join(words[-2:]) in LEGAL_SUFFIXES):
        words.pop()

    words = [w for w in words if w not in STOPWORDS]
    return " ".join(words)


def canonical_key(raw: str) -> str:
    """Order-insensitive key: the normalised tokens, sorted.

    "GULF PACKAGING INDUSTRIES" and "INDUSTRIES PACKAGING GULF" collapse to
    the same key, which is what you want when narrations reorder words.
    """
    return " ".join(sorted(set(normalize_name(raw).split())))


def normalize_cr(raw: str) -> tuple[str | None, str | None]:
    """Split Monaqasat's "29309/1 | 10497" into its two identifiers.

    The first is the commercial registration number (sometimes with a branch
    suffix after "/"), the second a secondary establishment or classification
    number. Either may be missing.
    """
    if not raw:
        return None, None
    parts = [p.strip() for p in str(raw).split("|")]
    primary = parts[0] or None
    secondary = parts[1] if len(parts) > 1 and parts[1] else None
    return primary, secondary


def cr_root(cr: str | None) -> str | None:
    """Digits before any branch suffix -- the joinable part of a CR number."""
    if not cr:
        return None
    m = re.match(r"\s*(\d+)", str(cr))
    return m.group(1) if m else None


# Arabic → Latin. Monaqasat English names are machine transliterations
# ("Kyrwy Llmqawlat"); QDB's book often holds the Arabic. Matching has to
# survive both.
_ARABIC_LETTERS = re.compile(r"[\u0600-\u06FF]")

_AR_WORDS = {
    "شركة": "company",
    "مؤسسة": "establishment",
    "مؤسسه": "establishment",
    "للتجارة": "trading",
    "التجارة": "trading",
    "التجاريه": "trading",
    "التجارية": "trading",
    "للمقاولات": "contracting",
    "المقاولات": "contracting",
    "والمقاولات": "contracting",
    "للنقليات": "transport",
    "والنقليات": "transport",
    "النقليات": "transport",
    "للخدمات": "services",
    "والخدمات": "services",
    "الخدمات": "services",
    "الهندسية": "engineering",
    "للهندسة": "engineering",
    "الصناعية": "industrial",
    "للصناعة": "industries",
    "المحدودة": "",
    "ذ.م.م": "",
    "ذات": "",
}

# Unvocalised Arabic → consonants. Several Latin letters per Arabic letter
# because MOF, banks and KYC each pick a different one.
_AR_CHAR = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "a", "ء": "", "ؤ": "w", "ئ": "y",
    "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh",
    "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh",
    "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "ة": "a", "و": "w", "ي": "y", "ى": "a", "پ": "p",
    "چ": "j", "ڤ": "v", "گ": "g",
}


def is_arabic(raw: str | None) -> bool:
    if not raw:
        return False
    return len(_ARABIC_LETTERS.findall(str(raw))) >= 2


def arabic_to_latin(raw: str | None) -> str:
    """Approximate Latin from Arabic so it can share a key with Monaqasat names.

    Not a scholarly transliteration -- it exists to make
    'شركة الريان للتجارة' collide with 'AL RAYYAN TRADING WLL'.
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFC", str(raw))
    s = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", s)   # harakat + tatweel
    for ar, en in sorted(_AR_WORDS.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(ar, f" {en} ")
    # Definite article at the start of a word: الريان → ريان (then 'rayan')
    s = re.sub(r"(^|[^\u0600-\u06FF])ال", r"\1 ", s)
    out = []
    for ch in s:
        if ch in _AR_CHAR:
            out.append(_AR_CHAR[ch])
        elif ch.isspace() or ch == "-":
            out.append(" ")
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
    latin = _WS.sub(" ", "".join(out)).strip()
    latin = normalize_name(latin)
    words = [w for w in latin.split() if w not in {"company", "establishment"}]
    return " ".join(words)


def consonant_skeleton(raw: str | None) -> str:
    """Vowels and doubled letters stripped -- survives Rayyan/Ryan/Al-Rayyan."""
    if not raw:
        return ""
    s = arabic_to_latin(raw) if is_arabic(raw) else normalize_name(raw)
    out: list[str] = []
    prev = ""
    for ch in s:
        if ch in "aeiouy":
            continue
        if ch == " ":
            if out and out[-1] != " ":
                out.append(" ")
            prev = ""
            continue
        if ch == prev:
            continue
        out.append(ch)
        prev = ch
    return "".join(out).strip()


def match_keys(raw: str | None) -> set[str]:
    """Every comparison key a name might be known by."""
    if not raw:
        return set()
    keys: set[str] = set()
    if is_arabic(raw):
        latin = arabic_to_latin(raw)
        if latin:
            keys.add(latin)
            keys.add(canonical_key(latin))
    else:
        n = normalize_name(raw)
        if n:
            keys.add(n)
            keys.add(canonical_key(raw))
    extra: set[str] = set()
    for key in list(keys):
        words = [w for w in key.split()
                 if w not in {"company", "establishment"}]
        if words and words != key.split():
            stripped = " ".join(words)
            extra.add(stripped)
            extra.add(canonical_key(stripped))
        sk = consonant_skeleton(key)
        if sk and len(sk.replace(" ", "")) >= 5:
            extra.add(sk)
    keys |= extra
    return {k for k in keys if k}


def similarity(a: str, b: str) -> float:
    """Best of direct, normalised, and Arabic-transliterated token similarity."""
    candidates_a = [a] if a else []
    candidates_b = [b] if b else []
    if a and is_arabic(a):
        candidates_a.append(arabic_to_latin(a))
    if b and is_arabic(b):
        candidates_b.append(arabic_to_latin(b))

    best = 0.0
    for xa in candidates_a:
        for xb in candidates_b:
            best = max(best, _similarity_latin(xa, xb))
            if best >= 0.999:
                return 1.0
    return best


def _similarity_latin(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    try:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio(na, nb) / 100.0
    except ImportError:
        import difflib
        ta, tb = set(na.split()), set(nb.split())
        jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
        seq = difflib.SequenceMatcher(None, na, nb).ratio()
        return max(jacc, seq)

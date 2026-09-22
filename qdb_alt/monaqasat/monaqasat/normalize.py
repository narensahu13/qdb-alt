"""Qatari entity-name normalisation.

Company names on Monaqasat, in bank narrations and in QDB's own KYC file are
written differently every time. This collapses the variation so that two
spellings of the same company produce the same key.

Three kinds of comparison are supported, in falling order of trust:

  exact       the same normalised name, in the same script
  fuzzy       similar normalised names, in the same script (token_sort_ratio)
  translit    an Arabic-script name against a Latin-script one, compared as
              consonant skeletons of the whole name

What the live site shows (checked Sept 2026): most company names on the
English pages are English ("AL RAYYAN TRADING WLL") or a letter-by-letter
machine transliteration of the Arabic ("Kyrwy Llmqawlat Walnqlyat
Walkhdmlt"), but some are in Arabic script even on the English pages
("قطر للوقود (وقود)", tender 3217/2022). All three are handled.

Same approach as altdata/normalize.py -- kept here so this package stands
alone.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

try:                                     # optional; much faster and better
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:                      # pragma: no cover - exercised on CI
    _rf_fuzz = None

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


# --------------------------------------------------------------------------
# Arabic script
# --------------------------------------------------------------------------

_HAS_ARABIC = re.compile(r"[؀-ۿ]")
_AR_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_AR_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا",
    "ٱ": "ا",                      # hamza / madda alef forms -> alef
    "ى": "ي", "ئ": "ي",  # alef maqsura, yeh hamza -> yeh
    "ة": "ه",                      # taa marbuta -> heh
    "ؤ": "و",                      # waw hamza -> waw
    "ی": "ي", "ک": "ك",  # Persian yeh, keheh
    "ـ": None,                           # tatweel
})
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥"
                              "٦٧٨٩۰۱"
                              "۲۳۴۵۶۷"
                              "۸۹",
                              "01234567890123456789")

# Legal forms and generic words, as they look after folding and after the
# dots are dropped: ذ.م.م -> ذمم, ش.م.ق -> شمق, مؤسسة -> موسسه.
_AR_DROP = {
    "ذمم", "شمق", "شمعق", "ششو", "شمخ", "شمع", "شركه", "موسسه", "مجموعه",
    "ذات", "مسووليه", "محدوده", "قابضه", "القابضه", "و",
}
_AR_ARTICLE = re.compile("^(?:وال|بال|كال|فال|لل|ال)(?=..)")

# Generic words an English-style name would translate rather than
# transliterate: "AL RAYYAN TRADING" for الريان للتجارة. Keys are folded,
# article-free forms.
_AR_TRANSLATE = {
    "تجاره": "trading", "تجاريه": "trading", "مقاولات": "contracting",
    "خدمات": "services", "نقليات": "transport", "نقل": "transport",
    "هندسه": "engineering", "هندسيه": "engineering",
    "صناعه": "industries", "صناعات": "industries", "صناعيه": "industrial",
    "طباعه": "printing", "تنظيف": "cleaning", "سياحه": "tourism",
    "سفر": "travel", "استشارات": "consulting", "تكنولوجيا": "technology",
    "تقنيه": "technology", "معدات": "equipment", "اغذيه": "food",
    "ضيافه": "hospitality", "توريدات": "supplies", "تسويق": "marketing",
    "وكالات": "agencies", "الكترونيات": "electronics", "مواد": "materials",
    "بناء": "building", "عقارات": "real estate", "تجميل": "beauty",
}

# Letter by letter, as Monaqasat's machine transliterations are written:
# للمقاولات -> Llmqawlat, والنقليات -> Walnqlyat. Letters whose Latin
# rendering varies between sources (ع, ة, ى, ء) map to a vowel or to nothing,
# because the skeleton drops vowels anyway.
_AR_LETTERS = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h",
    "خ": "kh", "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s",
    "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a",
    "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m",
    "ن": "n", "ه": "h", "و": "w", "ي": "y", "ء": "", "پ": "p",
    "چ": "ch", "ڤ": "v", "گ": "g",
}


def is_arabic(text: str | None) -> bool:
    """At least two Arabic letters. A stray Arabic comma is not a name."""
    if not text:
        return False
    return len(re.findall("[ء-يٱ-ۓ]", str(text))) >= 2


def _fold_arabic(raw: str) -> str:
    """Diacritics off, letter variants folded, dots between letters dropped
    (ذ.م.م -> ذمم), digits to ASCII, anything else to spaces."""
    s = _AR_DIACRITICS.sub("", unicodedata.normalize("NFC", raw))
    s = s.translate(_AR_FOLD)
    s = re.sub("(?<=[؀-ۿ])\\.(?=[؀-ۿ])", "", s)
    s = s.translate(ARABIC_DIGITS)
    return re.sub("[^ء-ي0-9 ]+", " ", s)


def normalize_arabic(raw: str) -> str:
    """Arabic name -> folded, legal-form and article free, in Arabic script.

    شركة الريان للتجارة ذ.م.م, مؤسسة الرّيان للتجارة and الريان للتجاره all
    give 'ريان تجاره'.
    """
    words = []
    for w in _fold_arabic(raw).split():
        if w in _AR_DROP:
            continue
        w = _AR_ARTICLE.sub("", w)
        if w and w not in _AR_DROP:
            words.append(w)
    return " ".join(words)


def arabic_to_latin(raw: str | None, mode: str = "translate") -> str:
    """Approximate Latin rendering of an Arabic name, for cross-script checks.

    mode="translate"  generic words translated, articles dropped, the rest
                      letter by letter: الريان للتجارة -> 'ryan trading'.
                      Matches English-style names ("AL RAYYAN TRADING").
    mode="letters"    every word letter by letter, prefixes kept:
                      كيروي للمقاولات -> 'kyrwy llmqawlat'. Matches the
                      site's machine transliterations.

    Not a scholarly transliteration; it only needs to land close enough for
    a skeleton comparison, which is reported as its own method and score.
    """
    if not raw:
        return ""
    out = []
    for w in _fold_arabic(str(raw)).split():
        if w in _AR_DROP or not w:
            continue
        if w.isdigit():
            out.append(w)
            continue
        if mode == "translate":
            bare = _AR_ARTICLE.sub("", w)
            if bare in _AR_DROP:
                continue
            if bare in _AR_TRANSLATE:
                out.append(_AR_TRANSLATE[bare])
                continue
            w = bare
        out.append("".join(_AR_LETTERS.get(ch, "") for ch in w))
    return _WS.sub(" ", " ".join(x for x in out if x)).strip()


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------

def normalize_name(raw: str | None) -> str:
    """Lower-cased, de-noised, expanded, legal-form-stripped name.

    Arabic-script names are folded separately (letter variants, diacritics,
    legal forms, the definite article) and kept in Arabic script, so an
    Arabic name on the site still has a key -- it used to normalise to ''.
    """
    if not raw:
        return ""
    if is_arabic(raw):
        return normalize_arabic(str(raw))
    s = _strip_accents(str(raw)).lower()

    for pat in NOISE_PATTERNS:
        s = re.sub(pat, " ", s)

    # "w.l.l" -> "wll", "w l l" -> "wll"
    s = _DOTTED.sub(lambda m: m.group(0).replace(".", ""), s)
    s = _NON_ALNUM.sub(" ", s)
    s = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), s)
    s = _WS.sub(" ", s).strip()

    words = [ABBREVIATIONS.get(w, w) for w in s.split()]

    # Strip trailing legal forms, repeatedly (".. trading co wll")
    while words and (words[-1] in LEGAL_SUFFIXES
                     or " ".join(words[-2:]) in LEGAL_SUFFIXES):
        words.pop()

    words = [w for w in words if w not in STOPWORDS]
    return " ".join(words)


def canonical_key(raw: str | None) -> str:
    """Order-insensitive key: the normalised tokens, sorted.

    "GULF PACKAGING INDUSTRIES" and "INDUSTRIES PACKAGING GULF" collapse to
    the same key, which is what you want when narrations reorder words.
    """
    return " ".join(sorted(set(normalize_name(raw).split())))


def match_keys(raw: str | None) -> set[str]:
    """Exact comparison keys for a name: the normalised form and the
    order-insensitive form, in the name's own script.

    Deliberately nothing looser. Consonant skeletons used to be in this set,
    which made "Nasser Trading" an exact match for "Nisr Trading" and
    "Al Amin Trading" for "Amana Trading".
    """
    if not raw:
        return set()
    return {k for k in (normalize_name(raw), canonical_key(raw)) if k}


# --------------------------------------------------------------------------
# skeletons -- only ever used across scripts
# --------------------------------------------------------------------------

_SKELETON_DROP = set("aeiou")


def consonant_skeleton(raw: str | None) -> str:
    """Vowels and doubled letters stripped, words kept apart by spaces.

    'AL RAYYAN TRADING' -> 'ryn trdng'; الريان للتجارة -> 'ryn trdng'.
    y and w are kept: they are ي and و, real letters of the Arabic name, and
    dropping them made too many different names collide.
    """
    if not raw:
        return ""
    text = arabic_to_latin(raw) if is_arabic(raw) else normalize_name(raw)
    out: list[str] = []
    prev = ""
    for ch in text:
        if ch == " ":
            if out and out[-1] != " ":
                out.append(" ")
            prev = ""
            continue
        if ch in _SKELETON_DROP or ch == prev:
            continue
        out.append(ch)
        prev = ch
    return "".join(out).strip()


def _latin_for_skeleton(raw: str) -> str:
    """Latin name prepared for a skeleton: legal forms and the article off,
    but no abbreviation expansion quirks beyond normalize_name's."""
    return normalize_name(raw)


def skeleton_forms(raw: str | None) -> list[str]:
    """Space-free skeletons a name may be known by across scripts.

    A Latin name has one. An Arabic name has two: its translated rendering
    (for English-style site names) and its letter-by-letter rendering (for
    machine transliterations).
    """
    if not raw:
        return []
    if is_arabic(raw):
        forms = [arabic_to_latin(raw, "translate"),
                 arabic_to_latin(raw, "letters")]
    else:
        forms = [_latin_for_skeleton(raw)]
    out = []
    for f in forms:
        sk = consonant_skeleton(f).replace(" ", "")
        if sk and sk not in out:
            out.append(sk)
    return out


# --------------------------------------------------------------------------
# similarity
# --------------------------------------------------------------------------

def _ratio(a: str, b: str) -> float:
    if _rf_fuzz is not None:
        return _rf_fuzz.ratio(a, b) / 100.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def token_sort_similarity(na: str, nb: str) -> float:
    """Similarity of two already-normalised names, word order ignored.

    token_sort, not token_set: token_set scores 1.0 whenever one name's
    words are a subset of the other's, so "Gulf Trading" would equal
    "Gulf Trading & Contracting Services".
    """
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if _rf_fuzz is not None:
        return _rf_fuzz.token_sort_ratio(na, nb) / 100.0
    a = " ".join(sorted(na.split()))
    b = " ".join(sorted(nb.split()))
    return difflib.SequenceMatcher(None, a, b).ratio()


MIN_SKELETON = 6      # below this, too many different names share a skeleton


def translit_similarity(a: str | None, b: str | None) -> float:
    """Cross-script similarity: best ratio between the names' skeletons.

    Returns 0.0 unless exactly one side is Arabic and both skeletons are at
    least MIN_SKELETON letters -- short skeletons ("rn", "mn trdng") are
    shared by too many unrelated companies.
    """
    if not a or not b or is_arabic(a) == is_arabic(b):
        return 0.0
    best = 0.0
    for sa in skeleton_forms(a):
        for sb in skeleton_forms(b):
            if len(sa) < MIN_SKELETON or len(sb) < MIN_SKELETON:
                continue
            best = max(best, _ratio(sa, sb))
    return best


def similarity(a: str | None, b: str | None) -> float:
    """Name similarity 0..1: token-sort within a script, skeletons across."""
    if not a or not b:
        return 0.0
    if is_arabic(a) != is_arabic(b):
        return translit_similarity(a, b)
    return token_sort_similarity(normalize_name(a), normalize_name(b))


# --------------------------------------------------------------------------
# commercial registration numbers
# --------------------------------------------------------------------------

def normalize_cr(raw: str | None) -> tuple[str | None, str | None]:
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


def cr_root(cr) -> str | None:
    """The joinable part of a commercial registration number.

    Handles every form a CR number turns up in, on either side of the join:
      29309/1        branch suffix              -> 29309
      CR-29309       prefix                     -> 29309
      029309         leading zeros              -> 29309
      29,309         Excel thousands separator  -> 29309  (was "29": a
                                                           different company)
      29309.0        Excel number as float      -> 29309
      ٢٩٣٠٩          Arabic-Indic digits        -> 29309
    """
    if cr is None:
        return None
    if isinstance(cr, float):
        if cr != cr:                        # NaN from a blank Excel cell
            return None
        cr = int(cr) if cr.is_integer() else cr
    s = str(cr).strip().translate(ARABIC_DIGITS)
    if not s:
        return None
    s = re.sub(r"\.0+$", "", s)
    s = re.sub(r"(?<=\d)[,٬،](?=\d{3}(?!\d))", "", s)
    m = re.search(r"[0-9]+", s)
    if not m:
        return None
    digits = m.group(0).lstrip("0")
    return digits or None

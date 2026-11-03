"""Language-aware tokenization and normalization shared by the text metrics.

- Whitespace languages: Unicode word tokens.
- CJK (Chinese, Japanese, Korean) text: each CJK character is a token.
- ``normalize`` follows the QuAC / SQuAD protocol used by FMEval: lowercase, drop punctuation,
  drop English articles (only when the language is English or unknown), collapse whitespace.
"""

from __future__ import annotations

import re
import string
import unicodedata

_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
)
_CJK_RE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CJK_RANGES) + "]")
_WORD = re.compile(r"\w+", re.UNICODE)
_ARTICLES_ANYCASE = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_PUNCT = set(string.punctuation)

# A small English stop-word list (keyword-overlap modes).
STOPWORDS = frozenset(
    """a an the and or but if then else of to in on at by for with from as is are was were be been
    being it its this that these those i you he she we they me him her us them my your his our
    their what which who whom how why when where do does did not no so than too very can will
    just""".split()
)


# AgentSight ``utils/similarity_utils.DEFAULT_STOP_WORDS`` (verbatim, source order).
SEMANTIC_STOPWORDS = frozenset(
    """
    the a an is are was were be been being have has had do does did will would could should may
    might must shall can to of in for on with at by from as into through during before after
    above below between under again further then once here there when where why how all each
    few more most other some such no nor not only own same so than too very just and but if or
    because until while this that these those i you he she it we they what which who whom its
    your my his her our their me him us them am about against any both down up out off over s t
    d ll m re ve
    """.split()
)
SEMANTIC_MIN_WORD_LENGTH = 2  # SemanticRelevanceScorer(min_word_length=2)
_NON_WORD = re.compile(r"[^\w\s]")


def semantic_keywords(text: str, language: str | None = None) -> list[str]:
    """AgentSight ``SemanticRelevanceScorer._tokenize``: punctuation -> space, lowercase, split,
    drop stop words and words shorter than 2 characters. CJK text is tokenized per character
    first (an extension: AgentSight keeps a CJK run as one word)."""
    if not text:
        return []
    if has_cjk(text):
        words = [t.lower() for t in tokenize(text, language)]
    else:
        words = _NON_WORD.sub(" ", text.lower()).split()
    return [w for w in words if w not in SEMANTIC_STOPWORDS and len(w) >= SEMANTIC_MIN_WORD_LENGTH]


def keyword_jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard of two keyword lists; 0.0 when either is empty (AgentSight ``_keyword_score`` and
    ``scoring_utils.compute_keyword_overlap``)."""
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def has_cjk(text: str) -> bool:
    return _CJK_RE.search(text) is not None


def _is_english(language: str | None) -> bool:
    return language is None or language.lower().split("-")[0] == "en"


def strip_punctuation(text: str) -> str:
    return "".join(
        ch for ch in text if ch not in _PUNCT and not unicodedata.category(ch).startswith("P")
    )


def remove_articles(text: str, language: str | None = None) -> str:
    """Remove English articles (a, an, the); a no-op for other languages. Mixed CJK / English
    text is treated like English, so a pair normalizes alike whichever side has CJK."""
    if not _is_english(language):
        return text
    return _ARTICLES_ANYCASE.sub(" ", text)


def normalize(text: str, language: str | None = None) -> str:
    """QuAC/SQuAD normalization (lowercase, strip punctuation and articles, fix whitespace)."""
    return " ".join(remove_articles(strip_punctuation(text.lower()), language).split())


def tokenize(text: str, language: str | None = None) -> list[str]:
    """Split ``text`` into tokens; CJK characters become individual tokens."""
    if not has_cjk(text):
        return _WORD.findall(text)
    tokens: list[str] = []
    for word in _WORD.findall(text):
        buf = ""
        for ch in word:
            if is_cjk_char(ch):
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            else:
                buf += ch
        if buf:
            tokens.append(buf)
    return tokens


def normalized_tokens(text: str, language: str | None = None) -> list[str]:
    return tokenize(normalize(text, language), language)


class PorterStemmer:
    """The Porter stemmer, in two variants (our implementation, no NLTK):

    - ``mode="original"``: the published algorithm (Porter, 1980), as ``rouge-score`` uses it;
    - ``mode="nltk"``: with NLTK's extensions (its default), as FMEval's METEOR uses it: a table
      of irregular forms, words of ≤ 2 letters left alone, ``-ies`` / ``-ied`` on 4-letter words
      kept as ``-ie``, ``y → i`` only after a consonant, ``-alli`` before ``-bli``, ``-bli → -ble``
      instead of ``-abli → -able``, and ``-fulli → -ful``, ``-logi → -log``.
    """

    _VOWELS = frozenset("aeiou")
    _IRREGULAR = {  # NLTK mode: whole-word forms
        "sky": "sky", "skies": "sky", "dying": "die", "lying": "lie", "tying": "tie",
        "news": "news", "innings": "inning", "inning": "inning", "outings": "outing",
        "outing": "outing", "cannings": "canning", "canning": "canning", "howe": "howe",
        "proceed": "proceed", "exceed": "exceed", "succeed": "succeed",
    }  # fmt: skip
    _STEP2 = (
        ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
        ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"), ("eli", "e"),
        ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"), ("ator", "ate"), ("alism", "al"),
        ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"), ("aliti", "al"),
        ("iviti", "ive"), ("biliti", "ble"),
    )  # fmt: skip
    _STEP3 = (
        ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"),
        ("ful", ""), ("ness", ""),
    )  # fmt: skip
    _STEP4 = (
        "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion",
        "ou", "ism", "ate", "iti", "ous", "ive", "ize",
    )  # fmt: skip

    def __init__(self, mode: str = "original"):
        if mode not in ("original", "nltk"):
            raise ValueError("mode must be 'original' or 'nltk'")
        self.nltk = mode == "nltk"
        if self.nltk:  # -bli replaces -abli (same position); -fulli and -logi come last
            step2 = [("bli", "ble") if r[0] == "abli" else r for r in self._STEP2]
            step2 += [("fulli", "ful"), ("logi", "log")]
        else:
            step2 = list(self._STEP2)
        self._step2_rules = tuple(step2)

    def _cons(self, w: str, i: int) -> bool:
        ch = w[i]
        if ch in self._VOWELS:
            return False
        if ch == "y":
            return i == 0 or not self._cons(w, i - 1)
        return True

    def _m(self, stem: str) -> int:
        """Number of VC sequences in ``stem``."""
        n, i, length = 0, 0, len(stem)
        while i < length and self._cons(stem, i):
            i += 1
        while i < length:
            while i < length and not self._cons(stem, i):
                i += 1
            if i >= length:
                break
            while i < length and self._cons(stem, i):
                i += 1
            n += 1
        return n

    def _has_vowel(self, stem: str) -> bool:
        return any(not self._cons(stem, i) for i in range(len(stem)))

    def _double_cons(self, w: str) -> bool:
        return len(w) >= 2 and w[-1] == w[-2] and self._cons(w, len(w) - 1)

    def _cvc(self, w: str) -> bool:
        if self.nltk and len(w) == 2:
            return not self._cons(w, 0) and self._cons(w, 1)
        if len(w) < 3:
            return False
        return (
            self._cons(w, len(w) - 3)
            and not self._cons(w, len(w) - 2)
            and self._cons(w, len(w) - 1)
            and w[-1] not in "wxy"
        )

    def _rules(self, w: str, rules, measured=None) -> str:
        """The first rule whose suffix matches decides: replace it if ``m(stem) > 0``."""
        for suffix, repl in rules:
            if w.endswith(suffix):
                stem = w[: len(w) - len(suffix)]
                return (
                    stem + repl
                    if self._m(measured(suffix, w, stem) if measured else stem) > 0
                    else w
                )
        return w

    def _step2(self, w: str) -> str:
        if self.nltk and w.endswith("alli") and self._m(w[:-4]) > 0:
            return self._step2(w[:-4] + "al")

        def measured(suffix: str, word: str, stem: str) -> str:
            return word[:-3] if self.nltk and suffix == "logi" else stem  # keeps the "l"

        return self._rules(w, self._step2_rules, measured)

    def stem(self, word: str) -> str:
        w = word.lower()
        if self.nltk:
            if w in self._IRREGULAR:
                return self._IRREGULAR[w]
            if len(w) <= 2:
                return w
        # Step 1a
        if self.nltk and w.endswith("ies") and len(w) == 4:
            w = w[:-3] + "ie"
        elif w.endswith(("sses", "ies")):
            w = w[:-2]
        elif w.endswith("ss"):
            pass
        elif w.endswith("s"):
            w = w[:-1]
        # Step 1b
        flag = False
        if self.nltk and w.endswith("ied"):
            w = w[:-3] + ("ie" if len(w) == 4 else "i")
        elif w.endswith("eed"):
            if self._m(w[:-3]) > 0:
                w = w[:-1]
        elif w.endswith("ed") and self._has_vowel(w[:-2]):
            w, flag = w[:-2], True
        elif w.endswith("ing") and self._has_vowel(w[:-3]):
            w, flag = w[:-3], True
        if flag:
            if w.endswith(("at", "bl", "iz")):
                w += "e"
            elif self._double_cons(w) and w[-1] not in "lsz":
                w = w[:-1]
            elif self._m(w) == 1 and self._cvc(w):
                w += "e"
        # Step 1c
        if w.endswith("y"):
            stem = w[:-1]
            if self.nltk:
                replace = len(stem) > 1 and self._cons(stem, len(stem) - 1)
            else:
                replace = self._has_vowel(stem)
            if replace:
                w = stem + "i"
        w = self._step2(w)
        w = self._rules(w, self._STEP3)
        # Step 4
        for suffix in self._STEP4:
            if w.endswith(suffix):
                stem = w[: len(w) - len(suffix)]
                if self._m(stem) > 1 and (suffix != "ion" or stem.endswith(("s", "t"))):
                    w = stem
                break
        # Step 5a
        if w.endswith("e"):
            stem = w[:-1]
            m = self._m(stem)
            if m > 1 or (m == 1 and not self._cvc(stem)):
                w = stem
        # Step 5b
        if self._m(w) > 1 and self._double_cons(w) and w.endswith("l"):
            w = w[:-1]
        return w


_STEMMERS: dict[str, PorterStemmer] = {}


def stemmer(mode: str = "original") -> PorterStemmer:
    """A shared :class:`PorterStemmer` (``original`` for ROUGE, ``nltk`` for METEOR)."""
    if mode not in _STEMMERS:
        _STEMMERS[mode] = PorterStemmer(mode)
    return _STEMMERS[mode]


# --------------------------------------------------------------------------- Treebank tokenizer
# Penn Treebank word tokenization with the improvements NLTK's ``word_tokenize`` applies to one
# sentence (punctuation, quotes, brackets and clitics such as n't / 's split off). Our
# implementation of the convention; FMEval's METEOR tokenizes this way.
_TB_STARTING_QUOTES = (
    (re.compile("([«“‘„]|[`]+)"), r" \1 "),
    (re.compile(r'^"'), r"``"),
    (re.compile(r"(``)"), r" \1 "),
    (re.compile(r"([ \(\[{<])(\"|\'{2})"), r"\1 `` "),
    (re.compile(r"(?i)(?<!\w)(\')(?!(?:re|ve|ll|m|t|s|d|n)\b)(?=\w)"), r"\1 "),
)
_TB_PUNCTUATION = (
    (re.compile(r"([^\.])(\.)([\]\)}>\"'»”’ ]*)\s*$"), r"\1 \2 \3 "),
    (re.compile(r"([:,])([^\d])"), r" \1 \2"),
    (re.compile(r"([:,])$"), r" \1 "),
    (re.compile(r"\.{2,}"), r" \g<0> "),
    (re.compile(r"[;@#$%&]"), r" \g<0> "),
    (re.compile("[\u2012-\u2015]"), r" \g<0> "),  # figure / en / em dashes
    (re.compile(r"([^\.])(\.)([\]\)}>\"']*)\s*$"), r"\1 \2\3 "),
    (re.compile(r"[?!]"), r" \g<0> "),
    (re.compile(r"([^'])' "), r"\1 ' "),
    (re.compile(r"[*]"), r" \g<0> "),
)
_TB_BRACKETS = (re.compile(r"[\]\[\(\)\{\}\<\>]"), r" \g<0> ")
_TB_DOUBLE_DASHES = (re.compile(r"--"), r" -- ")
_TB_ENDING_QUOTES = (
    (re.compile("([»”’])"), r" \1 "),
    (re.compile(r"''"), " '' "),
    (re.compile(r'"'), " '' "),
    (re.compile(r"\s+"), " "),
    (re.compile(r"([^' ])('[sS]|'[mM]|'[dD]|') "), r"\1 \2 "),
    (re.compile(r"([^' ])('ll|'LL|'re|'RE|'ve|'VE|n't|N'T) "), r"\1 \2 "),
)
_TB_CONTRACTIONS = tuple(
    re.compile(p)
    for p in (
        r"(?i)\b(can)(?#X)(not)\b",
        r"(?i)\b(d)(?#X)('ye)\b",
        r"(?i)\b(gim)(?#X)(me)\b",
        r"(?i)\b(gon)(?#X)(na)\b",
        r"(?i)\b(got)(?#X)(ta)\b",
        r"(?i)\b(lem)(?#X)(me)\b",
        r"(?i)\b(more)(?#X)('n)\b",
        r"(?i)\b(wan)(?#X)(na)(?=\s)",
        r"(?i) ('t)(?#X)(is)\b",
        r"(?i) ('t)(?#X)(was)\b",
    )
)


def treebank_tokenize(text: str) -> list[str]:
    """Penn Treebank word tokens of one sentence (e.g. ``"don't."`` → ``do n't .``)."""
    for pattern, repl in _TB_STARTING_QUOTES:
        text = pattern.sub(repl, text)
    for pattern, repl in _TB_PUNCTUATION:
        text = pattern.sub(repl, text)
    text = _TB_BRACKETS[0].sub(_TB_BRACKETS[1], text)
    text = _TB_DOUBLE_DASHES[0].sub(_TB_DOUBLE_DASHES[1], text)
    text = f" {text} "
    for pattern, repl in _TB_ENDING_QUOTES:
        text = pattern.sub(repl, text)
    for pattern in _TB_CONTRACTIONS:
        text = pattern.sub(r" \1 \2 ", text)
    return text.split()

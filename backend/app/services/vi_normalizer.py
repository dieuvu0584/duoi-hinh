"""Deterministic Vietnamese text normalisation before speech synthesis (docs/video-pipeline.md).

Numbers, dates, times, percentages, money and common units are spelled out in Vietnamese words by
OUR code, so the result does not depend on how a TTS model reads digits. The output text (not the
reviewed original) is what gets synthesised and cached; `NORMALIZER_VERSION` is part of the audio
cache key, so changing any rule here changes every key.

Conventions (northern, formal): 1 after 20+ -> "mốt", 5 after 10+ -> "lăm", 4 -> "bốn", zero tens
in the middle -> "linh", groups "nghìn / triệu / tỷ", decimal comma -> "phẩy", thousands dot
(24.500) -> 24500. Month 4 is "tư".
"""

import re
import unicodedata

NORMALIZER_VERSION = 1

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
_SCALES = ["", "nghìn", "triệu", "tỷ"]
MAX_NUMBER = 999_999_999_999

DEFAULT_LEXICON: dict[str, str] = {
    "TP.HCM": "Thành phố Hồ Chí Minh",
    "TP.": "thành phố ",
    "TP": "thành phố",
    "UBND": "Ủy ban nhân dân",
    "HN": "Hà Nội",
    "&": " và ",
}


def _read_below_thousand(n: int, *, leading: bool) -> str:
    """Read 0..999. `leading` False means a lower group, which keeps 'không trăm' / 'linh'."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    parts: list[str] = []
    if hundreds:
        parts += [_DIGITS[hundreds], "trăm"]
    elif not leading and n:
        parts += ["không", "trăm"]
    if tens == 0:
        if units:
            if hundreds or not leading:
                parts.append("linh")
            parts.append(_DIGITS[units])
    else:
        parts.append("mười" if tens == 1 else f"{_DIGITS[tens]} mươi")
        if units == 1:
            parts.append("một" if tens == 1 else "mốt")
        elif units == 5:
            parts.append("lăm")
        elif units:
            parts.append(_DIGITS[units])
    return " ".join(parts)


def number_to_words(n: int) -> str:
    if n < 0 or n > MAX_NUMBER:
        raise ValueError("number out of range")
    if n == 0:
        return _DIGITS[0]
    groups: list[int] = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    words: list[str] = []
    for index in range(len(groups) - 1, -1, -1):
        g = groups[index]
        if g == 0:
            continue
        leading = index == len(groups) - 1
        words.append(_read_below_thousand(g, leading=leading))
        if _SCALES[index]:
            words.append(_SCALES[index])
    return " ".join(w for w in words if w)


def digits_to_words(s: str) -> str:
    return " ".join(_DIGITS[int(c)] for c in s if c.isdigit())


def _int_words(text: str) -> str:
    """A plain digit string: leading zeros or very long strings are read digit by digit."""
    if len(text) > 1 and text[0] == "0" or len(text) > 12:
        return digits_to_words(text)
    return number_to_words(int(text))


def _month(m: int) -> str:
    return {4: "tư"}.get(m) or number_to_words(m)


def _valid_date(d: int, m: int) -> bool:
    return 1 <= d <= 31 and 1 <= m <= 12


# ----------------------------------------------------------------------------- patterns
_DATE_FULL = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")
_DATE_SHORT = re.compile(r"(?i)\b(ngày\s+)(\d{1,2})/(\d{1,2})(?![\d/])")
_FRACTION = re.compile(r"(?<![\d/])(\d{1,3})/(\d{1,3})(?![\d/])")
_TIME_H = re.compile(r"(?<!\d)(\d{1,2})h(\d{2})?(?!\w)")
_TIME_COLON = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_PERCENT = re.compile(r"(\d+(?:,\d+)?)\s*%")
_MONEY_SUFFIX = re.compile(r"(\d[\d.]*(?:,\d+)?)\s*(?:đ|₫|VND|vnđ)(?!\w)", re.IGNORECASE)
_USD = re.compile(r"\$\s*(\d[\d.,]*)|(\d[\d.,]*)\s*(?:USD|\$)")
_THOUSANDS = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?![\d.])")
_DECIMAL = re.compile(r"(?<![\d.,])(\d+),(\d+)(?![\d,])")
_PLAIN = re.compile(r"\d+")
_UNITS = [
    (re.compile(r"(?<=\d)\s*km(?!\w)"), " ki lô mét"),
    (re.compile(r"(?<=\d)\s*kg(?!\w)"), " ki lô gam"),
    (re.compile(r"(?<=\d)\s*cm(?!\w)"), " xăng ti mét"),
    (re.compile(r"(?<=\d)\s*mm(?!\w)"), " mi li mét"),
    (re.compile(r"(?<=\d)\s*ml(?!\w)"), " mi li lít"),
    (re.compile(r"(?<=\d)\s*°\s*C(?!\w)"), " độ xê"),
    (re.compile(r"(?<=\d)\s*m2(?!\w)"), " mét vuông"),
    (re.compile(r"(?<=\d)\s*m(?!\w)"), " mét"),
    (re.compile(r"(?<=\d)\s*g(?!\w)"), " gam"),
    (re.compile(r"(?<=\d)\s*l(?!\w)"), " lít"),
]
_URL = re.compile(r"https?://\S+|www\.\S+")
_HASHTAG = re.compile(r"(?<!\w)#\w+")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
_MARKUP = re.compile(r"[*_`~<>\[\]{}|\\^]")


def _num_from_vn(text: str) -> str:
    """'24.500' (thousands dots) or '1,5' (decimal comma) or '300' -> words."""
    if "," in text:
        whole, frac = text.replace(".", "").split(",", 1)
        return f"{_int_words(whole)} phẩy {digits_to_words(frac)}"
    return _int_words(text.replace(".", ""))


def _date_full(m: re.Match) -> str:
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not _valid_date(d, mo):
        return m.group(0)
    return f"ngày {number_to_words(d)} tháng {_month(mo)} năm {number_to_words(y)}"


def _date_short(m: re.Match) -> str:
    d, mo = int(m.group(2)), int(m.group(3))
    if not _valid_date(d, mo):
        return m.group(0)
    return f"{m.group(1)}{number_to_words(d)} tháng {_month(mo)}"


def _fraction_or_date(m: re.Match) -> str:
    """'a/b' without the word 'ngày': a proper fraction (3/4) is a fraction; a/b with a >= b that
    can be a day/month (25/10, 4/4) is read as a date, because such fractions are very rare."""
    a, b = int(m.group(1)), int(m.group(2))
    if a >= b and _valid_date(a, b):
        return f"ngày {number_to_words(a)} tháng {_month(b)}"
    return f"{number_to_words(a)} phần {number_to_words(b)}"


def _time_h(m: re.Match) -> str:
    hours, minutes = int(m.group(1)), m.group(2)
    if hours > 24:
        return m.group(0)
    out = f"{number_to_words(hours)} giờ"
    if minutes and int(minutes):
        out += f" {_read_minutes(int(minutes))} phút"
    return out


def _read_minutes(n: int) -> str:
    return f"linh {number_to_words(n)}" if 0 < n < 10 else number_to_words(n)


def _time_colon(m: re.Match) -> str:
    hours, minutes = int(m.group(1)), int(m.group(2))
    if hours > 24 or minutes > 59:
        return m.group(0)
    out = f"{number_to_words(hours)} giờ"
    if minutes:
        out += f" {_read_minutes(minutes)} phút"
    return out


def normalize(text: str, lexicon: dict[str, str] | None = None) -> str:
    """Return speech-ready Vietnamese text. Never raises on odd input (unknown bits stay as is)."""
    t = unicodedata.normalize("NFC", text or "")
    t = _URL.sub(" ", t)
    t = _HASHTAG.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = _MARKUP.sub(" ", t)

    for pattern, replacement in (lexicon if lexicon is not None else DEFAULT_LEXICON).items():
        t = re.sub(rf"(?<!\w){re.escape(pattern)}(?!\w)" if pattern[-1:].isalnum()
                   else re.escape(pattern), replacement, t)

    t = _DATE_FULL.sub(_date_full, t)
    t = _DATE_SHORT.sub(_date_short, t)
    t = _TIME_COLON.sub(_time_colon, t)
    t = _TIME_H.sub(_time_h, t)
    t = _PERCENT.sub(lambda m: f"{_num_from_vn(m.group(1))} phần trăm", t)
    t = _MONEY_SUFFIX.sub(lambda m: f"{_num_from_vn(m.group(1))} đồng", t)
    t = _USD.sub(lambda m: f"{_num_from_vn(m.group(1) or m.group(2))} đô la", t)
    for pattern, spoken in _UNITS:
        t = pattern.sub(spoken, t)
    t = _FRACTION.sub(_fraction_or_date, t)
    t = _THOUSANDS.sub(lambda m: number_to_words(int(m.group(0).replace(".", ""))), t)
    t = _DECIMAL.sub(lambda m: _num_from_vn(m.group(0)), t)
    t = _PLAIN.sub(lambda m: _int_words(m.group(0)), t)
    return " ".join(t.split())

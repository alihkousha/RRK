"""Who signed a notice: details.issuer, its signature line, written thousands of ways ("مسئول ثبت شرکتهای
اصفهان", "رئیس اداره ثبت اسناد و املاک اهواز", "اداره ثبت شرکتها و موسسات غیرتجاری واحد ثبتی زنجان"), read as
the kind of office and its city, so notices group by office however the line spells it."""
from __future__ import annotations

import re
from typing import NamedTuple

from parsing.entities import normalize

KINDS = [  # the first that matches
    ("liquidator", r"(?:مدیر|هیئت|هیات|ناظر)\s*تصفیه"),
    ("court", r"دادگاه|دادرس"),
    ("free_zone", r"منطقه\s*آزاد"),
    ("ip", r"عل(?:ا[ئی]م|امت)\s*تجار(?:ی|تی)|مالکیت\s*(?:های\s*)?صنعتی|اختراع|طرح\s*(?:ها|های)?\s*صنعتی"),
    ("deeds", r"ثبت\s*اسناد"),  # the deeds office: it registers the companies of smaller towns
    ("registry", r"ثبت\s*(?:شرکت|موسسات)|مرجع\s*ثبت|ثبت\s*\S+\s*$|اداره\s*ثبت"),
]
_KINDS = [(kind, re.compile(pattern)) for kind, pattern in KINDS]
# The words of an office's title; the city is what follows the last of them.
_TITLE = re.compile(r"(?<!\S)(?:ثبتی?|اسناد\s*و?\s*املاک|اسناد|املاک|غیر\s*تجاری|شرکت\s*(?:ها|های)?|شرکتهای?|موسسات"
                    r"|شهرستان|ناحیه\s*\S+|واحد|استان\s*\S+|منطقه\s*آزاد|معنوی)(?!\S)")
_CITY = re.compile(r"[^\W\d_]{2,}(?:\s[^\W\d_]{2,}){0,2}")


class Issuer(NamedTuple):
    kind: str  # liquidator | court | free_zone | ip | deeds | registry | other
    city: str | None


def read_issuer(text: str | None) -> Issuer | None:
    if not text or not text.strip():
        return None
    line = " ".join(normalize(text).replace("ؤ", "و").split())
    line = re.split(r"\s[-–ـ]|[-–ـ]\s", line)[0]  # "... در حال تصفیه - رضا ندائی": the signer's name
    kind = next((kind for kind, pattern in _KINDS if pattern.search(line)), "other")
    if kind not in ("deeds", "registry", "free_zone") or not (titles := list(_TITLE.finditer(line))):
        return Issuer(kind, None)
    city = re.sub(r"^اسناد(?=\S)", "", line[titles[-1].end():].strip())  # "رئیس ثبت اسنادکرج"
    return Issuer(kind, city if _CITY.fullmatch(city) else None)


def office(text: str | None) -> str | None:
    """The office a notice groups under: its city, else "~<kind>"."""
    issuer = read_issuer(text)
    return issuer and (issuer.city or f"~{issuer.kind}")

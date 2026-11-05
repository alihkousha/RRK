"""What a notice decided: its events (a board elected, the capital raised, the address moved, ...) and the
capital change it records, read from the text, the ad's type and the roles the parser found in it."""
from __future__ import annotations

import re

from .entities import AMOUNT, LEFT, SIGNATORY, amount, normalize

# The listing's ad types that are one event (ad_types: تاسیس, انحلال, ختم تصفیه, دعوت از ..., کاهش اختیاری
# سرمایه), and the ones that are none of the events (trademarks, patents, tenders, corrections, ...).
BY_TYPE = {"1": ("establish",), "11": ("dissolve",), "19": ("liquidate_end",), "4": ("invitation",),
           "5": ("invitation",), "1001": ("capital_down", "invitation")}
OTHER_TYPES = {"6", "8", "9", "10", "12", "13", "14", "15", "16", "17", "18", "20"}
_BOARD = {"ceo", "chair", "vice_chair", "board", "alternate_board", "secretary", "treasurer"}
_CLAUSE = r"[^.؛<]{0,120}?"
KEYWORDS = [  # in the order the GUI lists them
    ("establish", r"خلاصه\s*(?:اظهارنامه|شرکتنامه)|اظهارنامه\s*(?:و\s*)?(?:اساسنامه|شرکتنامه)|مجمع\s*(?:عمومی\s*)?مو[سؤ]س"),
    ("capital_up", rf"افزایش\s*سرمایه|سرمایه{_CLAUSE}افزایش"),
    ("capital_down", rf"کاهش\s*سرمایه|سرمایه{_CLAUSE}(?:کاهش|تقلیل)"),
    ("address", rf"(?:نشانی|آدرس|مرکز|محل)\s*(?:اصلی|قانونی)?\s*(?:شرکت|موسسه)?(?!\s*(?:محل\s*)?تصفیه){_CLAUSE}"
                r"(?:تغییر|انتقال|منتقل)"
                r"|(?:انتقال|تغییر)\s*(?:نشانی|آدرس|محل|مرکز)"),
    ("name", rf"نام\s*(?:شرکت|موسسه){_CLAUSE}(?:تغییر|اصلاح)\s*(?:یافت|گردید|شد)|تغییر\s*نام"),
    ("activity", rf"موضوع\s*(?:شرکت|فعالیت|موسسه)?{_CLAUSE}(?:تغییر|اصلاح|الحاق|اضافه|افزوده|حذف)"
                 r"|(?:الحاق|اضافه)\s*(?:گردید|شد)?\s*به\s*موضوع"),
    ("accounts", r"ترازنامه|صورت\s*(?:های|ها)?\s*مالی|حساب\s*(?:های\s*)?سود\s*و\s*زیان"),
    ("newspaper", rf"روزنامه{_CLAUSE}(?:جهت|برای|به\s*منظور)\s*(?:درج|نشر|انتشار|چاپ)|(?:درج|نشر)\s*آگهی{_CLAUSE}روزنامه"
                  rf"|روزنامه{_CLAUSE}انتخاب|در\s*روزنامه{_CLAUSE}(?:آگهی|درج|منتشر)\s*(?:می|خواهد)"),
    ("transfer", rf"(?:واگذار|انتقال|صلح){_CLAUSE}(?:سهام|سهم\s*الشرکه)|(?:سهام|سهم\s*الشرکه){_CLAUSE}(?:واگذار|منتقل|صلح)"),
    ("dissolve", r"منحل"),
    ("liquidate_end", r"(?:ختم|پایان|خاتمه)\s*(?:عملیات\s*)?تصفیه"),
    ("branch", rf"شعبه{_CLAUSE}(?:تاسیس|دائر|افتتاح|ایجاد)|(?:تاسیس|افتتاح|ایجاد)\s*شعبه"),
    ("merger", r"ادغام"),
    ("duration", rf"مدت\s*(?:شرکت|فعالیت){_CLAUSE}(?:تغییر|تمدید)"),
    ("invitation", r"دعوت\s*(?:می|بعمل|به\s*عمل|نامه|شود|میشود)"),
]
_KEYWORDS = [(event, re.compile(pattern)) for event, pattern in KEYWORDS]
EVENTS = ("establish", "board", "inspector", "resign", "signatory", *(e for e, _ in KEYWORDS[1:]), "articles", "other")
_SIGNING = re.compile(r"حق\s*امضا|امضا[ءی]?\s*(?:کلیه|تمامی)|(?<!\S)(?:ب(?:ا\s*)?|به\s*)امضا[ءی]?(?!\S)[^.؛<]{0,250}?"
                      r"(?:معتبر|نافذ|دارای\s*اعتبار)")
# "ماده 5 اساسنامه اصلاح شد" where no other event is the reason: the articles alone changed.
_ARTICLES = re.compile(r"اساسنامه[^.؛<]{0,60}?(?:اصلاح|تصویب|تغییر)|(?:اصلاح|تصویب)\s*(?:ماده|مواد|اساسنامه)")
_OTHER_CHANGE = re.compile(r"سرمایه|نشانی|آدرس|مرکز|موضوع|نام\s*شرکت|مدت|روزنامه|سهم\s*الشرکه|سهام"
                           r"|مربوطه|(?:به|ب)\s*(?:شرح|نحو)\s*(?:فوق|مذکور)|در\s*نتیجه")  # "ماده مربوطه ...": a consequence
_SENTENCE = re.compile(r"\.\s|<br\s*/?>|؛|\s\d+\s*[-ـ]\s")
_OWNER = r"(?:اولیه\s*)?(?:شرکت|موسسه|تعاونی|شخصیت\s*حقوقی)?"
# "سرمایه شرکت از مبلغ X ریال بمبلغ Y ریال افزایش یافت", "... از مبلغ X ریال منقسم به 100 سهم ... به مبلغ Y ریال".
_CAPITAL_CHANGE = re.compile(rf"سرمایه\s*{_OWNER}[^.؛\d]{{0,30}}?از\s*(?:مبلغ\s*)?(?P<old>{AMOUNT})\s*(?:(?:ریال\s*)?(?:به|ب|تا)\s*"
                             rf"(?:مبلغ\s*)?|ریال[^.؛]{{0,200}}?(?:به|ب|تا)\s*مبلغ\s*)(?P<new>{AMOUNT})")
# A founding notice's: "سرمایه شخصیت حقوقی عبارت است از مبلغ X ریال", "سرمایه شرکت: بمبلغ X", "سرمایه شرکت مبلغ: X".
_CAPITAL = re.compile(rf"سرمایه\s*{_OWNER}\s*:?\s*(?:سرمایه\s*{_OWNER}\s*)?(?:عبارت\s*است\s*از\s*)?(?:نقدی\s*)?"
                      rf"(?:(?:به\s*|ب)?مبلغ\s*:?\s*)?(?P<new>{AMOUNT})\s*ریال")


def notice_events(text: str | None, ad_type: str | None, roles=()) -> list[str]:
    """The events of one notice, in EVENTS order.  *roles*: the role strings of the people and companies the
    parser found in it (ad_mentions rows, extract_mentions)."""
    if ad_type in OTHER_TYPES:
        return ["other"]
    norm = normalize(text or "")
    found = set(BY_TYPE.get(ad_type or "", ()))
    # A creditors' or meeting invitation names the dissolution it follows; a founding notice the branches it may open.
    skip = {"dissolve", "liquidate_end", "invitation"} if found else set()
    found.update(event for event, pattern in _KEYWORDS if event not in skip and pattern.search(norm))
    if "establish" in found:
        found.discard("branch")
    if (capital := notice_capital(norm, ad_type)) and capital[0]:
        found.add("capital_up" if capital[1] > capital[0] else "capital_down")
    keys = {key for role in roles if role for key in role.split(",")}
    if keys & _BOARD:
        found.add("board")
    if keys & {"inspector", "alternate_inspector"}:
        found.add("inspector")
    if LEFT in keys or "استعفا" in norm:
        found.add("resign")
    if SIGNATORY in keys or _SIGNING.search(norm):
        found.add("signatory")
    if "establish" not in found and any(_ARTICLES.search(part) and not _OTHER_CHANGE.search(part)
                                        for part in _SENTENCE.split(norm)):
        found.add("articles")
    return [event for event in EVENTS if event in found] or ["other"]


def notice_capital(text: str | None, ad_type: str | None = None) -> tuple[int | None, int] | None:
    """The capital change a notice records (from, to), the first if several; a founding notice's (ad type 1)
    capital is (None, capital).  Other notices restating their capital record no change."""
    norm = normalize(text or "")
    if (change := _CAPITAL_CHANGE.search(norm)) and (new := amount(change["new"])):
        return amount(change["old"]), new
    if ad_type == "1" and (stated := _CAPITAL.search(norm)) and (new := amount(stated["new"])):
        return None, new
    return None

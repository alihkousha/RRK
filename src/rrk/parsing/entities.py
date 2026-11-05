"""People, companies and addresses named in an ad's full text.

The notices write them with their official numbers: a person's 10-digit
national code (``به شماره ملی``), a company's 11-digit national id
(``شناسه ملی``), an address's 10-digit postal code (``کد پستی``).  The
numbers are the identity; names are read from the words before them and are
only labels (the same code is often spelled several ways).  Older notices name
the board without numbers ("آقای ... به سمت مدیرعامل"): those people are read
too, with no number (``gui.explore.resolve_people`` gives them the code the
same company's other notices give that name).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

# One character for one, so offsets in the normalized text are offsets in the original.
_NORMALIZE = [("ي", "ی"), ("ى", "ی"), ("ك", "ک"), ("ة", "ه"), ("ۀ", "ه"), ("أ", "ا"), ("إ", "ا"),
              ("\u200c", " "), ("\u200e", " "), ("\u200f", " "), ("\u00a0", " "),
              *((chr(0x06F0 + d), str(d)) for d in range(10)), *((chr(0x0660 + d), str(d)) for d in range(10))]


def normalize(text: str) -> str:
    """Persian letters for the Arabic ones the gazette mixes in, Latin digits."""
    for old, new in _NORMALIZE:  # str.replace: 15x faster than str.translate on these texts
        text = text.replace(old, new)
    return text


def valid_code(number: str) -> bool | None:
    """Whether the check digit of a national code (10 digits) or a company's national
    id (11) holds; None for other lengths.  A wrong one is a typo in the notice."""
    digits = [int(d) for d in number]
    if len(digits) == 10:
        total = sum(d * (10 - i) for i, d in enumerate(digits[:9])) % 11
        return digits[9] == (total if total < 2 else 11 - total)
    if len(digits) == 11:
        total = sum((d + digits[9] + 2) * (29, 27, 23, 19, 17)[i % 5] for i, d in enumerate(digits[:10])) % 11
        return digits[10] == total % 10
    return None


_ID = re.compile(r"(?<!\w)(?:(?:به|با|ب|و|دارای|دارنده)\s*)?(?:(?:(?P<label>شناسه)|شماره|(?:شماره\s*)?کد|ش\.?)\s*ملی"
                 r"|ش\s*\.\s*م(?![^\W\d]))\s*\.?\s*:?\s*(?P<id>\d{10,11})(?!\d)")  # "ش.م 10720226493"
# "24کدپستی 1415794831", "وکد پستی ...", "کد پستی به شماره ..."
_POSTAL = re.compile(r"(?<![^\W\d_])(?:و|(?:به|با)\s*)?کد\s*پستی\s*(?:به\s*شماره\s*)?:?\s*(?P<id>\d{10})(?!\d)")
_HONORIFIC = {"آقای", "اقای", "آقایان", "خانم", "خانمها", "دکتر", "مهندس"}
_NAME_STOP = {"و", "از", "به", "با", "نمایندگی", "بنمایندگی", "عبارتند", "شد", "شدند", "گردید", "گردیدند", "مدیره",
              "هیئت", "هیات", "مدیران", "اعضای", "اعضاء", "عضو", "سهامدار", "سهامداران", "شرکا", "شرکاء",
              "بازرس", "مدیرعامل", "رئیس", "سمت", "عنوان", "انتخاب", "تصمیمات", "ذیل", "البدل", "اصلی", "بجای",
              "جای", "ترتیب", "بترتیب", "استعفای", "دارنده", "شامل", "که", "بسمت", "بعنوان", "دارد", "دارند"}  # "دو نفر شامل A", "که A به سمت"
# Words a name read without a number can't hold: "نامبرده فوق به سمت ...", "مجمع عمومی به عنوان ...",
# "به کدملی 0068448600 شناسه ملی بسمت ...".
_NOT_A_NAME = {"نامبرده", "نامبردگان", "ایشان", "وی", "مشارالیه", "مشارالیها", "مشارالیهم", "فوق", "مذکور", "مزبور",
               "الذکر", "کلیه", "همگی", "یکی", "هریک", "مدت", "سال", "تاریخ", "جلسه", "مجمع", "عمومی", "عادی",
               "صورتجلسه", "اداره", "ثبت", "روزنامه", "شعبه", "سهم", "ریال", "امضا", "امضاء", "اوراق", "اسناد",
               "امضای", "بامضای", "بامضاء", "باامضاء", "در", "تعهد", "مابقی", "باقیمانده", "سرمایه", "بمدت", "ماه",
               "سهامی", "خاص", "است", "گردیده", "پرداخت", "دریافت", "اداری", "واصل", "مشارکت", "دادن", "ساکن",
               "همراه", "هر", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه",
               "تعاونی", "نماینده", "قانونی", "زیر", "شده", "حسابرس", "اعضا", "بعنوان", "داده", "واگذار",
               "خدمات", "حسابرسی", "مدیریت", "مالی", "شناسه", "کد", "شماره"}  # "موسسه حسابرسی و خدمات مدیریت ..."
_COMPANY_WORD = re.compile(r"(?:^|\s)(شرکت|موسسه|بانک|صندوق|سازمان)\s")
_LEGAL_FORM = re.compile(r"\([^)]*\)|سهامی\s*(?:خاص|عام)|با\s*مسئولیت\s*محدود|(?:به|با|ب)?\s*شماره\s*ثبت\s*\d+"
                         r"|تحت\s*شماره\s*\d+|در\s*تاریخ\s*[\d/]+|فوق\s*الذکر|فوق|مذکور|مزبور|یاد\s*شده")
_ADDRESS_START = re.compile(r"(?:نشانی|آدرس|مرکز\s*اصلی|محل)(?:\s*(?:شرکت|موسسه|تصفیه))?")
# The end of a sentence or clause, or "5-2 - " / " -2 " starting the next list item.
_SENTENCE_END = re.compile(r"\.\s|[:؛]\s?|\s\d+(?:-\d+)?\s*-\s|\s-\d+\s")
_FULL_STOP = re.compile(r"[^\W\d]{2}\.\s")  # not "ش. ملی"
_ROLE_REACH = 150  # characters after a number that can still hold its role
# What follows a number in a list before the next name: "و کدپستی ...", "صادره از مرودشت، ", "(همگی خارج از شرکاء)".
_LIST_TAIL = re.compile(_POSTAL.pattern + r"|(?<!\S)(?:صادره|متولد)\s*(?:از\s*)?\S+|\([^()]{0,40}\)")
_INTRO = r"(?:به|با)\s*سمت|بسمت|به\s*عنوان|بعنوان"
# A role given to a name with no number after it: "... احمد رامهران به سمت رئیس هیئت مدیره".
_ROLE_INTRO = re.compile(rf"(?<!\S)(?:{_INTRO})(?!\S)")
_NEXT_PERSON = re.compile(r"(?<!\S)و?(?:آقای|اقای|آقایان|خانم)(?!\S)")
# "... انتخاب گردید و کلیه اوراق با امضای مدیرعامل ...", "... انتخاب و امضاء ...": the role came before.
_CLAUSE_VERB = re.compile(r"(?<!\S)(?:شد|شدند|گردید|گردیدند|انتخاب|تعیین|منصوب)(?!\S)|(?<!\S)ضمنا")
_RESPECTIVELY = re.compile(r"(?<!\S)(?:به|ب)\s*ترتیب(?!\S)")
# What the gazette writes between a name and its number: "فرزند حسن به ش.ش 320 صادره از شیراز متولد ...".
_IDENTITY = re.compile(r"(?:(?<!\S)(?:به|با|ب|و)\s*)?(?<!\S)(?:فرزند|ش\s*\.\s*ش|ش\s+ش|شماره\s*شناسنامه|شناسنامه"
                       r"|صادره|متولد|تاریخ\s*تولد)(?![^\W\d])")  # "ش ش", not "شش" (six)
# The words that say whose a number is when its label and length don't (_is_company).
_PERSON_WORD = re.compile(r"(?<!\S)و?(?:آقای|اقای|آقایان|خانم|خانمها|دکتر|مهندس|فرزند)(?!\S)|شناسنامه|متولد")
# A role between a name and its number: "A به سمت رئیس هیئت مدیره به شماره ملی ...", "2 -A –نایب رئیس-کد ملی ...".
_ROLE_START = re.compile(rf"(?<!\S)(?:{_INTRO})(?!\S)|[-–ـ]")
_ROLE_FILLER = re.compile(rf"{_INTRO}|هی[ائ]ت\s*مدیره|اصلی|علی\s*البدل|شرکت|(?<!\S)و(?!\S)|[-–ـ\s(:]")
# Leaving: "قبول استعفای آقای ...", "... بجای آقای ...", "... از شرکت خارج شدند", "عزل آقای ...".
_LEAVING_BEFORE = re.compile(r"استعفا|عزل|برکنار|خروج|بجای|به\s*جای|سلب\s*(?:سمت|عضویت)")
_LEAVING_AFTER = re.compile(r"استعفا|از\s*(?:شرکت|تعاونی|عضویت|سمت\s*\S+|ردیف\s*\S+)\s*(?:خارج|کناره)|عزل|برکنار"
                            r"|(?<!\S)با\s*دریافت")
# After a header's role, before its names: "مدیران تصفیه شرکت ( ", "بازرس اصلی : آقای ".
_HEADER_TAIL = re.compile(r"(?:[\s:()\-–ـ،,]|(?<!\S)(?:شرکت|و?(?:آقای|اقای|آقایان|خانم|دکتر|مهندس))(?!\S))+$")
_HEADER_REST = re.compile(r"(?:\s*(?:اصلی|علی\s*البدل|جدید|هی[ائ]ت\s*مدیره|شرکت))*\s*")
# A sentence heading a list: "اعضاء هیئت مدیره به مدت 2 سال به قرار ذیل انتخاب گردیدند: A ... و B ...".
_LIST_HEADER = re.compile(r"(?:(?:به|ب)\s*(?:قرار|شرح)\s*(?:ذیل|زیر)(?:\s+[^\s\d.:؛]+){0,3}|عبارتند\s*از)\s*[.:؛]?\s*$")
_TEN_DIGITS = re.compile(r"\d{10}")
_WORD = re.compile(r"\S+")
# ـ: "0080047793ـ شهلا صفوی", but not a kashida inside a word ("گـودرزی")
_NAME_BREAK = re.compile(r"[\d.:،,؛()\[\]«»\-–—<>/]|(?<![^\W\d_])ـ|ـ(?![^\W\d_])")
# Not part of the name before a number or role: "به شماره شناسنامه 12 و", "X را به سمت", "X با حفظ سمت به عنوان",
# "X (آوات)" (a nickname), "X خارج از اعضاء به سمت مدیرعامل" (a CEO from outside the board).
_BEFORE_NAME = re.compile(r"(?:(?:و\s*)?(?:به|با)?\s*(?:شماره\s*)?شناسنامه\s*(?:شماره\s*)?\d+\s*[و،]?|(?<!\S)را"
                          r"|(?<!\S)با\s*حفظ\s*سمت(?:\s*قبلی)?|\([^()\d]{1,40}\)|(?<!\S)را\s*که"
                          r"|(?<!\S)خارج\s*از(?:\s+\S+){1,3}|(?<!\S)(?:به|با))\s*$")  # "به به ش ملی"
_AFTER_NAME = " \t\r\n(،,:.-–ـ"  # "علی احمدی (کد ملی", "مجید ستاری، فرزند", "-حسن نیکبخت –"
_LIST_DASH = re.compile(r"[-–ـ(]+(?=[^\W\d_]+$)")  # "-حسن": a list item's dash, then the name
_REPLACED = re.compile(r"(?<!\S)(?:بجای|به\s*جای)\s*$")
# Where a name read forward from "آقای" ends: "آقای A برای مدت ...", "آقای A می باشد", "آقای A دائر نمود".
_NAME_END = {"برای", "بمدت", "می", "میباشد", "باشد", "است", "بوده", "ضمنا", "دائر", "نمود", "را", "در", "تا", "طی",
             "جهت", "هر", "هریک", "هرکدام", "تابعیت", "نشانی", "آدرس", "مستقر", "ساکن", "فرزند", "متولد", "صادره",
             "بشماره", "باپرداخت", "بادریافت", "بصندوق", "دارنده", "بنمایندگی", "طبق", "برابر", "بموجب", "ضمن", "نیز",
             "همراه", "اصلاح", "صحیح", "کد", "شماره", "استعفا", "استعفاء", "بسمت", "بعنوان", "اساسنامه", "سرمایه",
             "نایب", "نائب", "نواب", "مدیر", "مدیران", "خزانه", "اثر", "ذیل"}
_TITLES = {"دکتر", "مهندس", "حاج", "حاجی"}  # "آقای دکتر A": before the name, not in it
_HONORIFIC_AT = re.compile(r"(?<![^\s(\-–ـ:،,])و?(?:آقایان|آقای|اقای|خانمها|خانم|آقا\s*/\s*خانم)(?:\s*:)?(?!\S)")
# A "و" glued to the next name: "وخانم الهه یزدانی وسعید باغبانی جاوید بسمت ...".
_GLUED_AND = re.compile(r"و(?=(?:سید|محمد|علی|حسین|حسن|رضا|مهدی|احمد|محمود|مصطفی|سعید|رحیم|سجاد|مجید|حمید|جواد"
                        r"|نصر|فاطمه|مریم|زهرا|اکبر|عباس|ابراهیم|اسماعیل|یوسف|جعفر|کاظم|قاسم|هادی|امیر|مسعود"
                        r"|ناصر|منصور|محسن|زینب|معصومه|اعظم)[^\W\d_]*$)")
_LETTER_BEFORE = re.compile(r"[^\W\d_][\s\-–ـ(]*$")  # a role right after a word: maybe a name's
_ITEM_BEFORE = re.compile(r"(?:^|[.:،,\-–ـ>/؛]|\d|(?<!\S)و)\s*$")  # where a list item's name can start
# ... or after the role ending the item before: "A رئیس هیئت مدیره B نایب رئیس هیئت مدیره C مدیرعامل برای ..."
_AFTER_ITEM = re.compile(r"(?:^|[.:،,\-–ـ>/؛]|\d|(?<!\S)و|مدیره|عامل|ر[ئی]یس|عضو|اصلی|البدل|منشی|خزانه\s*دار|دبیر)\s*$")
# A role written before its name, heading it: "بازرس اصلی زهرا دهناد، بازرس علی البدل سعید شرفا برای ...".
_ROLE_FIRST = re.compile(r"بازرس(?:ان|ین)?\s*(?:اصلی|علی\s*البدل)\s*:?\s*")
_LIST_START = re.compile(r"(?:(?:به|ب)\s*(?:قرار|شرح)\s*(?:ذیل|زیر)(?:\s+[^\s\d.:؛]+){0,3}|عبارتند\s*از)\s*[.:؛]\s*")
_PEOPLE_LIST = re.compile(r"اعضا|مدیر|بازرس|شرکا|سهامدار|موسس|اشخاص|افراد|انتخاب")
_REPRESENTING = re.compile(r"(?<!\S)(?:به|ب)\s*نمایندگی(?!\S)")
_UNNUMBERED_REACH = 80  # characters after a name without a number that can still hold its role
# A liquidator's signature closing the notice: "مدیر تصفیه شرکت نوآوران امیر پلاستیک ـ خسرو صدقیانی".
_SIGNATURE = re.compile(r"(?:مدیر(?:ان)?|هی[ائ]ت)\s*(?:تصفیه|تسویه)[^<.:]{0,150}?\s[ـ\-–]\s*(?=[^\W\d_])"
                        r"(?P<name>[^<\d.:،,()]{4,60}?)[\s.]*(?:<[^>]*>\s*)*$")

# Most specific first: "نایب رئیس هیئت مدیره" also contains "رئیس هیئت مدیره".
ROLES = [
    ("liquidator", re.compile(r"(?:(?:عضو|اعضای|اعضاء|اعضا)\s*)?(?:مدیر(?:ان)?|هی[ائ]ت)\s*(?:تصفیه|تسویه)")),
    ("liquidation_supervisor", re.compile(r"ناظر(?:ین|ان)?\s*تصفیه")),
    ("vice_chair", re.compile(r"(?:نا[ئی]ب|نواب)\s*ر[ئی]یس")),
    # Bare "رئیس" where a list item ends: "A رئیس و مدیرعامل، B ...", "1- رئیس: آقای A"; not "رئیس اداره ثبت".
    ("chair", re.compile(r"(?:ر[ئی]یس|ریاست)\s*هی[ائ]ت\s*مدیره"
                         r"|ر[ئی]یس(?=\s*(?:$|[،,.:؛\-–ـ/<)]|و(?!\S)|\d|برای|به\s*مدت|بمدت))")),
    ("ceo", re.compile(r"مدیر\s*عامل")),
    ("alternate_inspector", re.compile(r"بازرس(?:ان|ین)?\s*(?:اصلی\s*و\s*)?علی\s*البدل")),
    ("inspector", re.compile(r"بازرس")),
    ("alternate_board", re.compile(r"(?:عضو|اعضای|اعضاء)\s*علی\s*البدل")),
    ("board", re.compile(r"(?:عضو|اعضای|اعضاء|اعضا)(?!\w)")),
    ("secretary", re.compile(r"منشی|(?<!\S)دبیر(?!ستان|خانه|کل|ی)")),
    ("treasurer", re.compile(r"خزانه\s*دار")),
    ("partner", re.compile(r"شرکا|شریک|سهم\s*الشرکه")),
    ("shareholder", re.compile(r"دارنده\s*(?:تعداد\s*)?[\d/,.]*\s*سهم(?!\s*الشرکه)|سهامدار")),
]
# A role written right after a name, without "به سمت": "1- خانم A رئیس هیات مدیره 2- ...", "A مدیرعامل، B رئیس و C
# عضو", "A دارنده 29000000ریال سهم الشرکه"; the run of roles and what joins them, and what may follow it.
_ROLE_AFTER = re.compile("|".join(p.pattern for _, p in ROLES) + r"|(?<!\S)دارنده(?!\S)")
_ROLE_WORD = re.compile("|".join(p.pattern for _, p in ROLES))
_RUN = re.compile(r"(?:\s*(?:" + "|".join(p.pattern for _, p in ROLES)
                  + r"|هی[ائ]ت\s*مدیره|اصلی|علی\s*البدل|شرکت|و(?!\S)|[،,\-–ـ()]))+")
_RUN_END = re.compile(r"\s*(?:$|[.؛/<]|\d|(?:برای|بمدت|به\s*مدت|انتخاب|تعیین|منصوب|هر|که|می|میباشد)(?!\S)"
                      r"|ضمنا)")
LEFT = "left"  # with the roles: the notice records them leaving (resigned, removed, replaced, withdrew)
SIGNATORY = "signatory"  # named where the notice says whose signature binds the company
# "... با امضای آقای A همراه با مهر شرکت معتبر است", "باامضاء A و B (مدیرعامل) الزاما به همراه مهر ...".
_SIGNING = re.compile(r"(?<!\S)ب(?:ا\s*)?امضا[ءی]?(?!\S)(?P<clause>.{1,250}?)"
                      r"(?=همراه|بهمراه|به\s*همراه|الزاما|معتبر|نافذ|دارای\s*اعتبار|[.؛]\s)", re.S)


VERSION = 6  # of the reading below: bump it and the explorer reads every stored text again


# Amounts: "7500000", "490.000.000", "000/000/75" (groups written right to left), "هفتاد و چهارمیلیون و ششصد هزار".
_NUMBER_WORDS = {
    "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "شیش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
    "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14, "پانزده": 15, "پونزده": 15, "شانزده": 16, "هفده": 17,
    "هیفده": 17, "هجده": 18, "هیجده": 18, "نوزده": 19, "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60,
    "هفتاد": 70, "هشتاد": 80, "نود": 90, "صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400,
    "پانصد": 500, "پونصد": 500, "ششصد": 600, "هفتصد": 700, "هشتصد": 800, "نهصد": 900,
    "هزار": 10 ** 3, "میلیون": 10 ** 6, "میلیارد": 10 ** 9, "بیلیون": 10 ** 9, "تریلیون": 10 ** 12}
_NUMBER_WORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))  # "چهارصد" before "چهار"
_SCALES = {"هزار", "میلیون", "میلیارد", "بیلیون", "تریلیون"}
AMOUNT = (rf"(?:\d+(?:[.,/٬]\d+)*(?:\s*(?:{'|'.join(_SCALES)}))?"
          rf"|(?<![^\W\d_])(?:{_NUMBER_WORD})(?:(?:\s*و\s*|\s*)(?:{_NUMBER_WORD}))*(?![^\W\d_]))")


_LARGEST = 2 ** 63 - 1  # SQLite's integers; no company comes near


def amount(text: str) -> int | None:
    """The number an AMOUNT match writes, else None (a date, a decimal)."""
    text = text.strip()
    if text[:1].isdigit():
        digits, scale = (text.split(None, 1) + [""])[:2]  # any whitespace, as AMOUNT's \s ("122\x1f میلیون")
        if not scale and (cut := re.search(r"[^\d.,/٬]", digits)):
            digits, scale = digits[:cut.start()], digits[cut.start():]
        groups = re.split(r"[.,/٬]", digits)
        if len(groups) > 1 and all(len(g) == 3 for g in groups[:-1]) and (groups[0] == "000" or len(groups[-1]) < 3):
            value = int("".join(reversed(groups)))  # "000/500/17": 17,500,000
        elif all(len(g) == 3 for g in groups[1:]):
            value = int("".join(groups))
        else:
            return None
        scaled = value * _NUMBER_WORDS.get(scale.strip(), 1)
        # Past _LARGEST the scale is a typo after the rials: "از 339913500000ریال به مبلغ2800495500000میلیارد ریال".
        return scaled if scaled <= _LARGEST else value if value <= _LARGEST else None
    total = current = 0
    for word in re.findall(_NUMBER_WORD, text):
        if word in _SCALES:
            total, current = total + (current or 1) * _NUMBER_WORDS[word], 0
        else:
            current += _NUMBER_WORDS[word]
    return value if 0 < (value := total + current) <= _LARGEST else None


# A partner's share capital, in the clause after the name: what it becomes ("سهم الشرکه خود را به 490.000.000 ریال
# افزایش داد"), what they hold ("دارنده 29000000ریال سهم الشرکه"), else what they pay in or take out.
_NEW_STAKE = (rf"سهم\s*الشرکه\s*(?:خود|خویش)\s*(?:را)?\s*(?:از\s*(?:مبلغ\s*)?{AMOUNT}\s*(?:ریال\s*)?)?به\s*(?:مبلغ\s*)?"
              rf"(?P<new>{AMOUNT})")
_STAKE = re.compile(rf"{_NEW_STAKE}|(?:دارنده|دارای)\s*(?:مبلغ\s*)?(?P<held>{AMOUNT})\s*ریال"
                    rf"|(?P<written>{AMOUNT})\s*ریال\s*سهم\s*الشرکه|^[\s:\-–ـ]*(?:مبلغ\s*)?(?P<bare>{AMOUNT})\s*ریال"
                    rf"|(?<!\S)ب(?:ا\s*)?(?:پرداخت|دریافت)\s*(?:مبلغ\s*)?(?P<paid>{AMOUNT})")
_STAKE_END = re.compile(r"سرمایه|در\s*نتیجه|[.؛]\s")
_PARTNERSHIP = re.compile(r"سهم\s*الشرکه|شرکا|صندوق\s*(?:شرکت|موسسه)")  # a company of partners: what they pay in is their stake


def _stake(window: str) -> int | None:
    """The share capital in rials the clause after a partner's name gives them."""
    window = window[:m.start()] if (m := _STAKE_END.search(window)) else window
    found = {k: v for m in _STAKE.finditer(window) for k, v in m.groupdict().items() if v}
    return next((amount(found[k]) for k in ("new", "held", "written", "bare", "paid") if k in found), None)


_EACH = re.compile(rf"هر\s*(?:کدام|یک)\s*(?:از\s*(?:ایشان|آنها|نامبردگان)\s*)?(?:میزان\s*)?{_NEW_STAKE}")


def _each(text: str, pos: int) -> int | None:
    """"A با پرداخت X، B با پرداخت Y ... هرکدام سهم الشرکه خود را به مبلغ Z افزایش داد": Z, each one's stake."""
    each = _EACH.search(text, pos, pos + 600)
    return amount(each["new"]) if each and not re.search(r"[.؛]\s|<br", text[pos:each.start()]) else None


_RIALS = re.compile(rf"(?P<amount>{AMOUNT})\s*ریال")
_GIVEN_ALL = re.compile(r"(?:صلح|واگذاری|انتقال)\s*(?:کلیه|تمامی|تمام)\s*(?:سهم\s*الشرکه|سهام)\s*(?:خود|خویش)\s*(?:را)?\s*"
                        r"به\s*(?:آقای|خانم|شرکت)?\s*$")


def _listed_stakes(text: str, mentions: list) -> list:
    """Stakes listed apart from where the partners are named, by name: "میزان سهم الشرکه شرکا: آقای A 500000 ریال
    و خانم B 500000 ریال"."""
    wanted = {m.name.replace(" ", ""): i for i, m in enumerate(mentions)
              if m.kind != "address" and m.name and m.stake is None}
    if not wanted:
        return mentions
    for hit in _RIALS.finditer(text):
        before = text[max(0, hit.start() - 70):hit.start()]
        if (cut := re.search(r"\d", before[::-1])) is not None:  # nothing but words between the name and the amount
            before = before[len(before) - cut.start():]
        flat = re.sub(r"[\s\u200cـ]", "", before)
        for key, i in wanted.items():
            m = mentions[i]
            if m.stake is None and key in flat and not m.start <= hit.start() < m.end and re.search(
                    r"سهم\s*الشرکه", text[max(0, hit.start() - 150):hit.end() + 40]):
                mentions[i] = m._replace(stake=_each(text, hit.end()) or amount(hit["amount"]))
    for i, m in enumerate(mentions):  # "A دارنده X ریال سهم الشرکه ... با صلح کلیه سهم الشرکه خود به آقای B": B's is X
        if m.stake is None and m.kind != "address" and _GIVEN_ALL.search(text, max(0, m.start - 70), m.start):
            giver = next((p for p in reversed(mentions[:i]) if p.kind != "address"), None)
            if giver and giver.stake:
                mentions[i] = m._replace(stake=giver.stake)
    return mentions


class Mention(NamedTuple):
    kind: str  # person | company | address
    entity_id: str | None  # national code | national id | postal code; None: a person named without one
    name: str | None
    role: str | None  # ROLES keys and LEFT, comma separated
    represents: str | None  # the company a person sits for ("به نمایندگی")
    start: int  # the name and number, as offsets in the text
    end: int
    stake: int | None = None  # a partner's share capital in rials (_stake)


_WORTH_READING = re.compile(r"مل[يی]|پست[يی]|ش\s*\.\s*م|سمت|عنوان|آقا|خانم|شرکا|سهم\s*الشرکه|تصفیه|تسویه|مدیرعامل"
                            r"|ر[ئی]یس|عضو|اعضا|بازرس")  # most ads (trademarks) name no one


def extract_mentions(text: str) -> list[Mention]:
    """Every person, company and address *text* names, in text order."""
    if not _WORTH_READING.search(text):
        return []
    norm = normalize(text)
    found = []
    for match in _ID.finditer(norm):
        number = match["id"]
        if len(set(number)) == 1:  # 0000000000: a blank in the form
            continue
        if _is_company(norm, match):
            name, start = _company_name(norm, match.start())
            found.append(("company", number, name, start, match.end(), None))
            continue
        at, between = _role_between(norm, match.start())
        name, start, _ = _name_at(norm, at)
        if between and not name:  # no name before that role: it is the one before's
            (name, start, _), between = _name_at(norm, match.start()), None
        found.append(("person", number, name, start, match.end(), between))
    for match in _POSTAL.finditer(norm):
        name, start = _address(norm, match.start())
        found.append(("address", match["id"], name, start, match.end(), None))
    found += _unnumbered(norm, found)
    found.sort(key=lambda m: m[3])

    mentions = []
    following_role, following_stake, earlier = None, None, []
    partnership = bool(_PARTNERSHIP.search(norm))
    for i in reversed(range(len(found))):  # backwards: a group shares the role written after it
        kind, number, name, start, end, between = found[i]
        stake = None
        # Up to the next person or company: "به کد ملی ... و کدپستی ... بسمت رئیس" is one sentence.
        stop = next((f[3] for f in found[i + 1:] if f[0] != "address"), len(norm))
        window, cut = _own_clause(norm, end, min(end + (_ROLE_REACH if number else _UNNUMBERED_REACH), stop))
        role = None
        if kind != "address":
            role = _roles(window)
            # "A ... و B ... بترتیب بسمت رئیس و نایب رئیس": the last role is B's, the ones before A's.
            if role and (respectively := _RESPECTIVELY.search(window)) and len(
                    parts := _role_parts(window[respectively.end():])) > 1:
                role, earlier = parts[-1], parts[:-1]
            # "A ... و B ... به عنوان اعضای هیئت مدیره", "A ... به نمایندگی از شرکت B ... به سمت ..."
            elif role is None and not between and not cut and (
                    len(_LIST_TAIL.sub(" ", window).strip(" ،,و")) < 3 or "نمایندگی" in window):
                role = earlier.pop() if earlier else following_role
                stake = following_stake
            following_role = role
            # Written between its name and number: its own, not the one before's; a finder's for a name
            # without one ("بازرس اصلی A، بازرس علی البدل B"): the window after it is the next one's.
            role = between if number is None and between else role or between
        if kind != "address" and partnership:
            reach = norm[end:min(end + 300, stop)]
            stake = _each(norm, end) or _stake(reach[:reach.rfind(" ")] if end + 300 < stop else reach) or stake
            following_stake = stake
        mentions.append(Mention(kind, number, name, role, None, start, end, stake))
    mentions.reverse()

    # "... - به موجب صورتجلسه احمد رامهران به سمت رئیس هیئت مدیره": the role goes to the one
    # person this ad numbered under that name; a name it didn't number is a person without one.
    numbered: dict[str, set[str]] = {}
    for m in mentions:
        if m.kind == "person" and m.name and m.entity_id:
            numbered.setdefault(m.name.replace(" ", ""), set()).add(m.entity_id)
    unnumbered: dict[int, Mention] = {}
    for intro in _ROLE_INTRO.finditer(norm):
        # Not into the next one's: "A به سمت نایب رئیس، B ... به کدملی ... به سمت مدیرعامل".
        upto = next((m.start for m in mentions if m.start >= intro.end() and m.kind != "address"), len(norm))
        clause, _ = _own_clause(norm, intro.end(), min(intro.end() + _ROLE_REACH, upto))
        if (role := _roles(clause)) is None and re.match(r"\s*(?:عضو\s*)?علی\s*البدل", clause):
            earlier = _roles(_clause_tail(norm[max(0, intro.start() - 200):intro.start()])) or ""
            role = ("alternate_inspector" if "inspector" in earlier
                    else "alternate_board" if set(earlier.split(",")) & {"board", "chair", "vice_chair", "ceo"} else None)
        if role is None:
            continue
        name, at, _ = _name_at(norm, intro.start())
        if len(ids := numbered.get(name.replace(" ", ""), set()) if name else set()) == 1:
            i = next(i for i, m in enumerate(mentions) if m.kind == "person" and m.entity_id in ids)
            mentions[i] = mentions[i]._replace(role=_roles(clause, mentions[i].role))
            continue
        respectively = _RESPECTIVELY.search(norm, max(0, intro.start() - 12), intro.start())
        group = _named_group(norm, respectively.start() if respectively else intro.start(), mentions, numbered)
        if respectively and group:
            if len(parts := _role_parts(clause)) != len(group):  # "به ترتیب" with a list it can't pair
                continue
            roles = parts
        else:
            roles = [role] * len(group)
        for (name, start, end, replaced), role in zip(group, roles):
            if replaced:  # "A بجای B به عنوان عضو": B leaves that seat
                role = f"{role},{LEFT}"
            unnumbered.setdefault(start, Mention("person", None, name, role, None, start, end))
    mentions = sorted([*mentions, *unnumbered.values()], key=lambda m: m.start)

    # "مدیر تصفیه آقای ...", "مدیران تصفیه شرکت ( A کد ملی ... و B کد ملی ... )": a role written before the names.
    header, previous_end = None, 0
    for i, m in enumerate(mentions):
        before = norm[previous_end:m.start]
        previous_end = max(previous_end, m.end)
        if m.kind != "person":
            header = None if m.kind == "company" else header
            continue
        if header and re.fullmatch(r"[\s،,و\-–ـ]*", before):
            role = header
        else:
            header = role = _role_before(before)
        if m.role is None and role:
            mentions[i] = m._replace(role=role)
        elif role == "board" and m.role and not set(m.role.split(",")) & {"alternate_board", "inspector",
                                                                          "alternate_inspector", "liquidator"}:
            mentions[i] = m._replace(role=_roles("عضو", m.role))

    # Who leaves: "قبول استعفای آقایان A و B", "عزل آقای A از سمت مدیرعامل", "A ... از شرکت خارج شدند".
    listed, previous_end = False, 0
    for i, m in enumerate(mentions):
        before = _clause_tail(norm[previous_end:m.start])
        previous_end = max(previous_end, m.end)
        if m.kind != "person":
            continue
        after, _ = _own_clause(norm, m.end, m.end + 60)
        replaced = bool(_REPLACED.search(before))
        # The list goes on ("و B"), unless B is given a seat ("... A، آقای B به سمت مدیرعامل").
        listed = (listed and bool(re.fullmatch(r"[\s،,و\-–ـ]*", before)) and not _ROLE_INTRO.search(after)) or (
            bool(_LEAVING_BEFORE.search(before)) and not replaced)
        if (listed or replaced or _LEAVING_AFTER.search(after)) and LEFT not in (m.role or "").split(","):
            mentions[i] = m._replace(role=f"{m.role},{LEFT}" if m.role else LEFT)
            # "A ... بجای B ... به عنوان عضو": A takes the seat B leaves.
            if replaced and i and mentions[i - 1].kind == "person" and mentions[i - 1].role is None:
                if (seat := _roles(m.role or "")) is not None:  # the ROLES keys, without LEFT
                    mentions[i - 1] = mentions[i - 1]._replace(role=seat)

    mentions = _signatories(norm, mentions)
    if partnership:
        mentions = _listed_stakes(norm, mentions)

    for i, (a, b) in enumerate(zip(mentions, mentions[1:])):
        if {a.kind, b.kind} == {"person", "company"} and "نمایندگی" in norm[a.end:b.start]:
            person, company = (i, b) if a.kind == "person" else (i + 1, a)
            mentions[person] = mentions[person]._replace(represents=company.entity_id)
    return mentions


def _signatories(text: str, mentions: list[Mention]) -> list[Mention]:
    """The people named where the notice says whose signature binds the company get SIGNATORY.  A name read there
    again is the one named before (its roles there are the clause's, not its own)."""
    clauses = [(m.start("clause"), m.end("clause")) for m in _SIGNING.finditer(text)]
    if not clauses:
        return mentions
    inside = [any(a <= m.start < b for a, b in clauses) for m in mentions]
    named = {m.name.replace(" ", "") for m, within in zip(mentions, inside) if m.kind == "person" and m.name and not within}
    signing = " " + " ".join(re.sub(r"[^\w]|\d|_", " ", " ".join(text[a:b] for a, b in clauses)).split()) + " "
    kept = []
    for m, within in zip(mentions, inside):
        if m.kind == "person" and within:
            if m.entity_id is None and m.name and m.name.replace(" ", "") in named:
                continue  # read again below, under its first mention
            m = m._replace(role=_roles("", f"{m.role},{SIGNATORY}" if m.entity_id else SIGNATORY))
        elif m.kind == "person" and m.name and f" {m.name} " in signing:
            m = m._replace(role=_roles("", f"{m.role},{SIGNATORY}"))
        kept.append(m)
    return kept


def ad_mentions(text: str | None, company_national_id: str | None, company_name: str | None) -> list[tuple]:
    """``mentions`` rows (kind, entity_id, written, valid, name, role, represents, subject,
    start, end, stake) of one ad.  Its subject is the listing's company, else the first company
    the text names.  A person named without a number is "~<subject>~<name without spaces>"
    (dropped when the ad has no subject: nothing to tell them apart by)."""
    mentions = extract_mentions(text or "")
    listed = normalize(company_national_id or "")  # the listing writes some with Persian digits
    subject = listed if listed.isdigit() else next((m.entity_id for m in mentions if m.kind == "company"), None)
    page_name = normalize(" ".join(company_name.split())) if company_name else None
    rows = []
    for m in mentions:
        length = {"person": 10, "company": 11}.get(m.kind)  # another length is a typo too
        valid = (len(m.entity_id) == length and valid_code(m.entity_id)) if length and m.entity_id else None
        if m.kind == "company" and m.entity_id == subject:  # its own title: no role in itself
            rows.append((m.kind, m.entity_id, m.entity_id, valid, page_name or m.name, None, None, 1, m.start, m.end, None))
        elif m.entity_id:
            rows.append((m.kind, m.entity_id, m.entity_id, valid, m.name, m.role, m.represents, 0, m.start, m.end, m.stake))
        elif subject:
            rows.append((m.kind, f"~{subject}~{m.name.replace(' ', '')}", None, None, m.name, m.role, m.represents, 0,
                         m.start, m.end, m.stake))
    if subject and not any(row[7] for row in rows):
        rows.append(("company", subject, subject, len(subject) == 11 and valid_code(subject), page_name,
                     None, None, 1, None, None, None))
    return rows


def _is_company(text: str, match: re.Match) -> bool:
    """Whether the number *match* found is a company's national id.  Mostly its form says: 10 digits
    after "کد ملی" are a person's, 11 a company's.  But "شناسه ملی" is written for people too, and a
    person's code with a digit too many ("کدملی 37806876321") has 11: unless it is a company id
    (10 or 14, then its check digit), the last person or company word of its clause decides,
    else its check digit and label."""
    number = match["id"]
    if len(number) == 10 and not match["label"]:
        return False
    if len(number) == 11 and number[:2] in ("10", "14") and valid_code(number):
        return True
    clause = text[max(0, match.start() - 120):match.start()]
    clause = clause[max((m.end() for m in (*_TEN_DIGITS.finditer(clause), *_SENTENCE_END.finditer(clause))),
                        default=0):]
    company = max((m.start() for m in _COMPANY_WORD.finditer(clause)), default=-1)
    person = max((m.start() for m in _PERSON_WORD.finditer(clause)), default=-1)
    if company != person:
        return company > person
    if len(number) == 10:
        return not valid_code(number)
    return bool(match["label"]) or number[:2] in ("10", "14") or valid_code(number)


def _role_between(text: str, pos: int) -> tuple[int, str | None]:
    """A role written between a person's name and the number at *pos*: where it starts and
    which ("A به سمت رئیس هیئت مدیره به شماره ملی ..."), else (pos, None)."""
    tail = text[max(0, pos - 60):pos]
    at, role = pos, None
    for start in sorted((m.start() for m in _ROLE_START.finditer(tail)), reverse=True):  # widest pure role
        rest = part = tail[start:]
        for _, pattern in ROLES:
            rest = pattern.sub(" ", rest)
        if _ROLE_FILLER.sub("", rest):  # a name or other words: the role, if any, ended
            break
        if rest != part:
            at, role = pos - len(tail) + start, _roles(part)
    return at, role


def _own_clause(text: str, start: int, end: int) -> tuple[str, bool]:
    """The start of *text*[start:end] (the text after a number) that can hold that number's role, and
    whether it ends early: at the end of a sentence or clause, or where the text turns to someone
    it gives no number ("... - به موجب صورتجلسه احمد رامهران به سمت ...", "... و آقای ...")."""
    window = text[start:end]
    ends = [m.start() for m in (_SENTENCE_END.search(window), _NEXT_PERSON.search(window),
                                _CLAUSE_VERB.search(window)) if m]
    for intro in _ROLE_INTRO.finditer(window):
        name, at, _ = _name_at(text, start + intro.start())
        # Not "123 به ترتیب به سمت" (still its own), "به نمایندگی از شرکت کرپ ناز بسمت" or "ریال سهم الشرکه به سمت";
        # nor the name the window follows ("A به سمت ...": A's own).
        if at >= start and _plausible_name(name):
            ends.append(at - start)
    return window[:min(ends, default=len(window))], bool(ends)


@lru_cache(maxsize=4096)
def _name_at(text: str, pos: int) -> tuple[str | None, int, int]:
    """_person_name, once per text and place: the windows after each mention ask for the same ones."""
    return _person_name(text, pos)


def _clause_tail(text: str) -> str:
    """*text* from its last sentence end (the text before a name, up to the previous mention)."""
    ends = [m.end() for m in _FULL_STOP.finditer(text)]
    return text[max(ends, default=max(0, len(text) - 60)):]


def _roles(window: str, also: str | None = None) -> str | None:
    keys = set(also.split(",")) if also else set()
    window = re.sub(r"خارج\s*از\s*\S+", " ", window)  # "مدیر عامل ( خارج از شرکا)": not a partner
    for key, pattern in ROLES:
        if pattern.search(window):
            keys.add(key)
            window = pattern.sub(" ", window)
    if keys & {"chair", "vice_chair"}:  # "عضو و رئیس هیئت مدیره": the chair says it
        keys.discard("board")
    return ",".join(key for key in (*(key for key, _ in ROLES), SIGNATORY, LEFT) if key in keys) or None


def _role_parts(clause: str) -> list[str]:
    """The roles of a "به ترتیب ..." list in their order: "بازرسان اصلی و علی البدل" -> inspector,
    alternate_inspector; "رئیس و نایب رئیس هیئت مدیره" -> chair, vice_chair."""
    parts: list[str] = []
    for part in re.split(r"\s+و\s+|،|\s-\s", clause):
        if role := _roles(part):
            parts.append(role)
        elif re.search(r"علی\s*البدل", part) and parts:
            parts.append({"inspector": "alternate_inspector", "board": "alternate_board"}.get(parts[-1], parts[-1]))
        elif re.fullmatch(rf"\s*(?:{_INTRO})?\s*(?:های)?\s*ر[ئی]یس\s*", part):  # "بسمت های رئیس، ..."
            parts.append("chair")
    return parts


def _role_before(text: str) -> str | None:
    """The role a header right before a name gives it: "مدیر تصفیه آقای", "مدیران تصفیه شرکت (",
    "بازرس اصلی :", a sentence heading a list.  Not "به سمت مدیرعامل، آقای" (the role of the one before)."""
    if listed := _LIST_HEADER.search(text):
        role = _roles(_clause_tail(text[:listed.start()]))
        return role if role and "," not in role else None  # "اعضای هیئت مدیره و بازرسان ...": whose is which?
    head = _HEADER_TAIL.sub("", _clause_tail(text))[-40:]
    head = head[max((m.end() for m in (*_SENTENCE_END.finditer(head), *_CLAUSE_VERB.finditer(head))), default=0):]
    ends = [m.end() for _, pattern in ROLES for m in pattern.finditer(head)]
    if not ends or _ROLE_INTRO.search(head) or not _HEADER_REST.fullmatch(head[max(ends):]):
        return None
    return _roles(head)


def _named_group(text: str, pos: int, mentions: list[Mention], numbered: dict) -> list[tuple]:
    """The names written without numbers right before *pos* ("به سمت ..."), joined by "و":
    [(name, start, end, replaced)] in text order; replaced: "A بجای B": B, who leaves the seat."""
    taken = [(m.start, m.end) for m in mentions]
    group = []
    while len(group) < 12:
        name, start, end = _name_at(text, pos)
        if (not _plausible_name(name) or name.replace(" ", "") in numbered
                or any(a < end and start < b for a, b in taken)):
            break
        before = text[max(0, start - 12):start]
        group.append((name, start, end, bool(_REPLACED.search(before))))
        if (replaced := _REPLACED.search(before)) is not None:  # walk on to the one taking the seat
            pos = max(0, start - 12) + replaced.start()
            continue
        if not (joint := re.search(r"(?:\s+و|\s*[،,])\s*$", text[max(0, start - 6):start])):
            break
        pos = max(0, start - 6) + joint.start()
    return group[::-1]


def _unnumbered(text: str, found: list) -> list[tuple]:
    """People named without a number whom no "به سمت" introduces (extract_mentions reads those itself): after
    "آقای" or "خانم" with the names joined to it, before a role written right after the name, a liquidator's
    signature.  Rows as extract_mentions collects them; a name the ad also numbers is that person."""
    taken = [(f[3], f[4]) for f in found]
    numbered = {f[2].replace(" ", "") for f in found if f[0] == "person" and f[2]}
    people: list[tuple] = []

    def add(name: str | None, start: int, end: int, role: str | None = None) -> bool:
        if (not _plausible_name(name) or name.replace(" ", "") in numbered
                or any(a < end and start < b for a, b in taken)):
            return False
        taken.append((start, end))
        people.append(("person", None, name, start, end, role))
        return True

    def names_from(start: int, pos: int, role: str | None = None) -> None:
        while True:
            name, end = _name_after(text, pos)
            if not add(name, start, end, role):
                return
            # "آقایان A و B ـ C", "وخانم الهه یزدانی وسعید ...": the names joined to it
            if not (joint := re.match(r"\s*(?:(?:و|[،,\-–ـ])\s+|و(?=[^\W\d_]))", text[end:end + 4])):
                return
            start = pos = end + joint.end()

    for match in _HONORIFIC_AT.finditer(text):
        names_from(match.start() + (match[0][0] == "و"), match.end())
    for match in _LIST_START.finditer(text):  # "به قرار ذیل انتخاب گردیدند: A - B - C" (the header pass gives the role)
        if _PEOPLE_LIST.search(text, max(0, match.start() - 100), match.start()):  # not "موضوعات ... عبارتند از:"
            names_from(match.end(), match.end())
    for match in _ROLE_FIRST.finditer(text):
        if _ITEM_BEFORE.search(text, max(0, match.start() - 8), match.start()):  # not "A بازرس اصلی B ...": A's
            names_from(match.end(), match.end(), _roles(match[0]))
    for match in _REPRESENTING.finditer(text):  # "A بنمایندگی از شرکت ...", "شرکت ... به نمایندگی A و ..."
        name, start, end = _name_at(text, match.start())
        if name and _ITEM_BEFORE.search(text[max(0, start - 12):start]):
            add(name, start, end)
        names_from(match.end(), match.end())
    for match in _ROLE_AFTER.finditer(text):
        if any(a <= match.start() < b for a, b in taken):
            continue
        run = _RUN.match(text, match.start())
        if not (match[0] == "دارنده" or re.search(r"(?:[،,\-–ـ)]|(?<!\S)و)$", run[0].rstrip())
                or _RUN_END.match(text, run.end())):
            continue  # "حدود اختیارات مدیرعامل طبق ...": a role, but not a list item's
        if not _LETTER_BEFORE.search(text, max(0, match.start() - 16), match.start()):
            continue  # "به اعضاء" is fine here, "123 مدیرعامل" or "): مدیرعامل" not
        name, start, end = _name_at(text, match.start())
        if not name or not re.fullmatch(r"[\s\-–ـ(]*", text[end:match.start()]):
            continue  # "A بسمت مدیرعامل" (read with "به سمت"), "به اعضاء", "واصل گردیده: اعضاء"
        # "B و C عضو": the names joined to it
        while name and _AFTER_ITEM.search(text[max(0, start - 16):start]) and add(name, start, end):
            if not (joint := re.search(r"(?:\s+و|\s*[،,\-–ـ])\s*$", text[max(0, start - 6):start])):
                break
            name, start, end = _name_at(text, max(0, start - 6) + joint.start())
    if signed := _SIGNATURE.search(text):
        name = " ".join(signed["name"].split())
        add(name, signed.start("name"), signed.start("name") + len(signed["name"].rstrip()), "liquidator")
    return people


def _name_after(text: str, pos: int) -> tuple[str | None, int]:
    """The name written from *pos* on (after "آقای") and its end: 2-5 words up to where a name can end (a stop
    word, a role, a number or punctuation, a "و" glued to the next name)."""
    words: list[str] = []
    end = pos
    for match in _WORD.finditer(text, pos):
        word = match.group()
        if words and _GLUED_AND.match(word):
            break
        cut = _NAME_BREAK.search(word)
        part = (word[:cut.start()] if cut else word).replace("ـ", "")
        if not words and (part in _TITLES or part == "ها"):  # "خانم ها A"
            continue
        if (not part or part in _HONORIFIC or part in _NAME_STOP or part in _NAME_END or "\u064b" in part
                or _ROLE_WORD.search(part)):
            break
        words.append(part)
        end = match.start() + len(part)
        if cut or len(words) > 5:
            break
    return (" ".join(words).replace("ـ", "") if 2 <= len(words) <= 5 else None), end


def _plausible_name(name: str | None) -> bool:
    words = name.replace("ـ", "").split() if name else []
    return (2 <= len(words) <= 6 and not _COMPANY_WORD.search(f" {name} ") and not _ROLE_WORD.search(name)
            and not _NOT_A_NAME.intersection(words) and all(len(w) > 1 for w in words))


def _person_name(text: str, pos: int) -> tuple[str | None, int, int]:
    """The name written before *pos* (a number or "به سمت"), its start and its end."""
    region = text[max(0, pos - 200):pos]
    # Past what stands between a name and its number ("فرزند نور اله. کد ملی"), but not past the previous person's.
    floor = max((*(m.end() for m in _TEN_DIGITS.finditer(region)),
                 *(m.end() for m in _FULL_STOP.finditer(region) if region[m.end():].strip())), default=0)
    floor = max(floor, max((m.start() for m in _NEXT_PERSON.finditer(region, floor)), default=floor))
    # The first with a name before it: "کد ملی ... صادره از مرودشت، محمد دهقان فرزند ..." is the one before's.
    for filler in (*(m.start() for m in _IDENTITY.finditer(region, floor)), len(region)):
        if (found := _name_before(text, pos - len(region) + filler))[0]:
            return found
    return None, pos, pos


def _name_before(text: str, pos: int) -> tuple[str | None, int, int]:
    low = max(0, pos - 250)
    before = text[low:pos]
    before = (before[:-90] + _BEFORE_NAME.sub("", before[-90:])).rstrip(_AFTER_NAME)  # it ends at $: the tail will do
    base = max(0, len(before) - 200)
    words = [(m.group(), low + m.start()) for m in _WORD.finditer(before, base)][-7:]
    name: list[tuple[str, int]] = []
    for word, offset in reversed(words):
        if word.replace("ـ", "") in _NAME_STOP:
            break
        if _NAME_BREAK.search(word):
            if dash := _LIST_DASH.match(word):
                name.insert(0, (word[dash.end():], offset + dash.end()))
            break
        if word[0] == "و" and word[1:] in _HONORIFIC:  # "وخانم"
            word, offset = word[1:], offset + 1
        if word.endswith("آقای") and len(word) > 4:  # "پس از بحث و تبادل نظرآقای A"
            break
        if _GLUED_AND.match(word):  # "یزدانی وسعید باغبانی": the "و" joins it to the one before
            name.insert(0, (word[1:], offset + 1))
            break
        name.insert(0, (word, offset))
        if word in _HONORIFIC:
            break
    plain = [w for w, _ in name]
    if "فرزند" in plain:  # "علی احمدی فرزند حسن": the father's name is not his
        name = name[:plain.index("فرزند")]
    label = [(w, o) for w, o in name if w not in _HONORIFIC]
    if label and label[0][0] == "ها":  # "خانم ها A": the plural, but "محمد ضرابی ها" is a name
        label = label[1:]
    if not name:
        return None, pos, pos
    return (" ".join(w for w, _ in label).replace("ـ", "") or None, name[0][1],
            (label[-1][1] + len(label[-1][0])) if label else pos)


def _company_name(text: str, pos: int) -> tuple[str | None, int]:
    base = max(0, pos - 160)
    before = text[base:pos]
    # The last "شرکت ..." before the number, unless only its legal form follows
    # ("شرکت صنایع ... شرکت سهامی خاص به شماره ثبت 151 و شناسه ملی").
    for keyword in reversed(list(_COMPANY_WORD.finditer(before))):
        name = " ".join(_LEGAL_FORM.sub(" ", before[keyword.end():]).split())
        name = re.sub(r"^(?:و\s)+|(?:\s(?:و|شرکت))+$|^و$", "", name)  # "... شرکت سهامی خاص به شماره ثبت 151 و"
        if re.search(r"[.:؛]\s|\d{10}", name):  # a sentence ends or someone's number comes in between
            return None, pos
        if name := name.strip(" ،,-"):
            return (name if keyword[1] == "شرکت" else f"{keyword[1]} {name}"), base + keyword.start(1)
    return None, pos


def _address(text: str, pos: int) -> tuple[str | None, int]:
    base = max(0, pos - 250)
    before = text[base:pos]
    keywords = list(_ADDRESS_START.finditer(before))
    offset = keywords[-1].end() if keywords else before.find(" ", max(0, len(before) - 120)) + 1
    offset = max(offset, max((m.end() for m in _ID.finditer(before)), default=0))  # "کد ملی ... و کدپستی"
    offset += re.match(r"[\s:\-\d.)(]*", before[offset:]).end()  # "مرکز اصلی شرکت : 3-1 - "
    name = " ".join(before[offset:].split()).strip(" ،,-")
    return (name if name not in ("", "و") else None), base + offset

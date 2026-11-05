from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DETAIL_CODE_RE = re.compile(r"[?&]p28_code=(\d+)")


@dataclass(frozen=True, slots=True)
class Advertisement:
    """One row of the open-data results grid (region ``AGAHI_GHANOUNI2``).

    Field order follows the grid's columns.  Dates are Jalali (``1400/06/14``)
    except ``pay_date``, which is Gregorian (``05/09/2021``).
    """

    tracking_code: str  # CODEPEYGIRI
    title: str  # NEWSTITLE
    letter_date: str | None  # NEWSDATE
    page_number: str | None  # PAGENUMBER
    newspaper_number: str | None  # NEWSPAPERCODE
    newspaper_date: str | None  # NEWSPAPERDATE
    indicator_number: str | None  # INDIKATORNUMBER
    company_national_id: str | None  # COMPANYCODE
    # FOOTER_H: the first 200 characters of the ad text, ending in "....".
    # The full text is on the detail page (``detail_code``).
    summary: str | None
    detail_code: str | None  # p28_code of the detail page, from DETAIL/DETAIL2
    status: str | None  # VAZIAT_AGAHI
    newspaper_type_code: str | None  # NEWSPAPER_TYPE value
    newspaper_type: str | None  # NEWSPAPER_TYPE label: تهران / شهرستان / معاملاتی
    pay_date: str | None  # PAYDATE_USER
    full_title: str | None  # NEWSTITLE_FULL_TEXT

    @classmethod
    def from_listing_row(cls, row: list[Any] | tuple[Any, ...]) -> "Advertisement":
        # 15 columns, then APEX's per-row metadata ({salt, protected, rowVersion}).
        if len(row) < 15:
            raise ValueError(f"expected at least 15 columns, got {len(row)}")

        newspaper_type = row[12] if isinstance(row[12], dict) else {}

        return cls(
            tracking_code=_text(row[0]),
            title=_text(row[1]),
            letter_date=_optional_text(row[2]),
            page_number=_optional_text(row[3]),
            newspaper_number=_optional_text(row[4]),
            newspaper_date=_optional_text(row[5]),
            indicator_number=_optional_text(row[6]),
            company_national_id=_optional_text(row[7]),
            summary=_optional_text(row[8]),
            detail_code=(
                extract_detail_code(_optional_text(row[10]))
                or extract_detail_code(_optional_text(row[9]))
            ),
            status=_optional_text(row[11]),
            newspaper_type_code=_optional_text(newspaper_type.get("v")),
            newspaper_type=_optional_text(newspaper_type.get("d")),
            pay_date=_optional_text(row[13]),
            full_title=_optional_text(row[14]),
        )


@dataclass(frozen=True, slots=True)
class AdDetail:
    """What the detail page (``f-detail-ad``, page 28) adds to the listing row.
    Its other fields (tracking code, dates, newspaper, title) repeat the row."""

    full_text: str | None  # region "متن آگهی:"; the listing's summary is cut to 200 chars
    company_name: str | None  # P28_COMPANYNAME
    registration_number: str | None  # P28_SABTNUMBER
    publication_count: str | None  # P28_HCNEWSSTAGE: how many times the ad is published
    issuer: str | None  # P28_AGAHI_SADER_KONANDE: the authority that issued the ad


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def extract_detail_code(html: str | None) -> str | None:
    """Extract p28_code from the detail link HTML."""
    if not html:
        return None
    match = _DETAIL_CODE_RE.search(html)
    return match.group(1) if match else None

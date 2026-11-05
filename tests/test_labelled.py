"""The parser against 200 hand-labelled ads in tests/labelled/ (git-ignored: real names and codes).

python tests/test_labelled.py   prints the scores
pytest                          fails when a score drops below FLOORS (raise them as the parser improves)

The labels (tests/labelled/label.py) list every person, company and address a text names, without the ad's
own company: kind, number, name, roles, left, represents, stake; then the notice's events and capital change.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "rrk"))
from parsing.entities import LEFT, extract_mentions, normalize  # noqa: E402
from parsing.events import notice_capital, notice_events  # noqa: E402

HERE = Path(__file__).parent / "labelled"
KINDS = {"p": "person", "c": "company", "a": "address"}
FLOORS = {"numbered": (0.99, 0.98), "unnumbered people": (0.98, 0.97), "names": (0.93, 0.93), "roles": (0.98, 0.95),
          "left": (0.8, 0.7), "represents": (0.85, 0.95), "stakes": (0.98, 0.93), "events": (0.96, 0.97),
          "capital": (0.98, 0.99)}


def _key(name):
    return re.sub(r"[\s‌ـ]", "", normalize(name or ""))


def _roles(roles):
    roles = set(filter(None, (roles or "").split(","))) - {LEFT}
    return roles | {"board"} if roles & {"chair", "vice_chair"} else roles  # a chair sits on the board


def gold(label):
    """{entity: [name, roles, left, represents, stake]} of one label; unnumbered people go by name."""
    found = {}
    for kind, number, name, roles, left, rep, stake in label["m"]:
        entity = (KINDS[kind], number or "~" + _key(name))
        known = found.setdefault(entity, [name, set(), False, None, None])
        known[1] |= _roles(roles)
        known[2] |= bool(left)
        known[3] = known[3] or rep
        known[4] = known[4] or stake
    return found


def read(ad):
    """The parser's reading of one ad in the labels' form, without the ad's own company (as
    ad_mentions picks it).  Unnumbered people go by name, also where ad_mentions drops them."""
    mentions = extract_mentions(ad["text"])
    listed = normalize(ad["company_national_id"] or "")
    subject = listed if listed.isdigit() else next((m.entity_id for m in mentions if m.kind == "company"), None)
    found = {}
    for m in mentions:
        if m.kind == "company" and m.entity_id == subject:
            continue
        known = found.setdefault((m.kind, m.entity_id or "~" + _key(m.name)), [m.name, set(), False, None, None])
        known[1] |= _roles(m.role)
        known[2] |= LEFT in (m.role or "").split(",")
        known[3] = known[3] or m.represents
        known[4] = known[4] or m.stake
    return {"m": found, "e": set(notice_events(ad["text"], ad["type"], [m.role for m in mentions])),
            "cap": notice_capital(ad["text"], ad["type"])}


def _ratio(hits, total):
    return hits / total if total else 1.0


def scores():
    """{measure: (precision, recall, gold count)} over the labelled ads."""
    ads = {ad["code"]: ad for ad in json.loads((HERE / "ads.json").read_text(encoding="utf-8"))}
    labels = [json.loads(line) for line in (HERE / "labels.jsonl").read_text(encoding="utf-8").splitlines() if line]
    tally = defaultdict(Counter)  # measure -> hits, read (parser), labelled (gold)

    def count(measure, want, got):
        tally[measure].update(hit=len(want & got), read=len(got), labelled=len(want))

    for label in labels:
        want, reading = gold(label), read(ads[label["code"]])
        got = reading["m"]
        numbered = {e for e in want if not e[1].startswith("~")}
        count("numbered", numbered, {e for e in got if not e[1].startswith("~")})
        count("unnumbered people", {e for e in want if e[0] == "person" and e[1].startswith("~")},
              {e for e in got if e[0] == "person" and e[1].startswith("~")})
        both = want.keys() & got.keys()
        named = {e for e in both & numbered if e[0] != "address" and want[e][0]}
        count("names", {(e, _key(want[e][0])) for e in named}, {(e, _key(got[e][0])) for e in named})
        count("roles", {(e, r) for e in both for r in want[e][1]}, {(e, r) for e in both for r in got[e][1]})
        count("left", {e for e in both if want[e][2]}, {e for e in both if got[e][2]})
        count("represents", {(e, want[e][3]) for e in both if want[e][3]}, {(e, got[e][3]) for e in both if got[e][3]})
        count("stakes", {(e, want[e][4]) for e in both if want[e][4]}, {(e, got[e][4]) for e in both if got[e][4]})
        count("events", set(label["e"]), reading["e"])
        count("capital", {tuple(label["cap"])} if label["cap"] else set(),
              {tuple(reading["cap"])} if reading["cap"] else set())
    return {m: (_ratio(t["hit"], t["read"]), _ratio(t["hit"], t["labelled"]), t["labelled"]) for m, t in tally.items()}


@pytest.mark.skipif(not (HERE / "labels.jsonl").exists(), reason="the labelled set is not on this machine")
def test_the_parser_reads_the_labelled_ads_at_least_as_well_as_before():
    got = scores()
    worse = {m: (round(p, 3), round(r, 3)) for m, (p, r, _) in got.items() if p < FLOORS[m][0] or r < FLOORS[m][1]}
    assert not worse, f"below {FLOORS}: {worse}"


if __name__ == "__main__":
    print(f"{'':20} precision  recall  labelled")
    for measure, (p, r, n) in scores().items():
        print(f"{measure:20} {p:9.2f} {r:7.2f} {n:9}")

"""Diff the request this client builds against one captured from the browser.

1. In the browser, open the open-data page and trigger the report request
   (search / next page).  DevTools -> Network -> the POST to wwv_flow.ajax
   -> Payload -> p_json -> "view source" -> copy it into browser_pjson.json
2. Run:  python compare.py browser_pjson.json

Session-bound values (salt, protected, instance) differ by design.
"""
import json
import sys

from rrs import open_data
from rrs.client import RRSClient

EXPECTED_TO_DIFFER = {"salt", "pageItems.protected"}


def norm(pj: dict) -> dict:
    """Flatten p_json to {dotted.path: value}; itemsToSubmit becomes name->value."""
    out: dict = {}

    def walk(o, prefix=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(o, list) and o and all(isinstance(i, dict) and "n" in i for i in o):
            for i in o:
                out[f"{prefix}[{i['n']}]"] = i.get("v")
        elif isinstance(o, list):
            for n, v in enumerate(o):
                walk(v, f"{prefix}[{n}]")
        else:
            out[prefix] = o

    walk(pj)
    return out


def short(v, n=60):
    t = repr(v)
    return t if len(t) <= n else t[:n] + f"...({len(t)} chars)"


browser = json.load(open(sys.argv[1], encoding="utf-8"))
reg = browser["regions"][0]
fd = reg.get("fetchData", {})
items = {i["n"]: i.get("v") for i in browser.get("pageItems", {}).get("itemsToSubmit", [])}

with RRSClient() as client:
    form = client.build_report_form(
        noe_agahi=items.get("P199_NOE_AGAHI"),
        first_row=fd.get("firstRow", 1),
        page_size=fd.get("maxRows", 100),
    )
ours = json.loads(form["p_json"])

a, b = norm(ours), norm(browser)
print("== only in OURS ==")
for k in sorted(a.keys() - b.keys()):
    print(f"  {k} = {short(a[k])}")
print("== only in BROWSER ==")
for k in sorted(b.keys() - a.keys()):
    print(f"  {k} = {short(b[k])}")
print("== DIFFERENT VALUES ==")
for k in sorted(a.keys() & b.keys()):
    if a[k] != b[k]:
        tag = "  (expected)" if k in EXPECTED_TO_DIFFER else ""
        print(f"  {k}{tag}\n      ours   : {short(a[k])}\n      browser: {short(b[k])}")
        if k.endswith("ajaxIdentifier"):
            print("      ours   decodes to:", open_data.decode_identifier(a[k]))
            print("      browser decodes to:", open_data.decode_identifier(b[k]))
print("== same ==", sum(1 for k in a.keys() & b.keys() if a[k] == b[k]), "values")

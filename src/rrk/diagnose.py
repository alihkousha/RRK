"""Show exactly what is sent to the server, to compare with the browser.

Run from the rrk/ folder:  python diagnose.py
Values are shortened; nothing is saved except page.html.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs

from rrs.client import RRSClient, RRSConfigError


def short(v, n=14):
    v = "" if v is None else str(v)
    return v if len(v) <= n else v[:n] + f"...({len(v)} chars)"


def show(resp, *_, **__):
    req = resp.request
    print(f"\n>>> {req.method} {resp.url}  ->  {resp.status_code}")
    cookie = req.headers.get("Cookie", "")
    print("    cookies sent:", [c.split("=")[0].strip() for c in cookie.split(";")] if cookie else "NONE")
    print("    redirects:", [(r.status_code, r.url) for r in resp.history])
    if req.method == "POST":
        form = parse_qs(req.body if isinstance(req.body, str) else req.body.decode())
        for k in ("p_flow_id", "p_flow_step_id", "p_instance", "p_request"):
            print(f"    {k} =", short(form.get(k, ["<missing>"])[0]))
        pj = json.loads(form["p_json"][0])
        print("    p_json keys:", list(pj))
        print("    pageItems keys:", list(pj.get("pageItems", {})))
        print("    salt:", short(pj.get("salt", "<missing>")))
        if pj.get("regions"):
            print("    region keys:", list(pj["regions"][0]))
    print("    response:", resp.headers.get("Content-Type"), resp.text[:200].replace("\n", " "))


with RRSClient() as client:
    client.session._http.hooks["response"].append(show)
    try:
        client.connect()
    except RRSConfigError as exc:
        last = client.page or client.home
        if last is None:
            raise
        Path("page.html").write_text(last.text, encoding="utf-8")
        raise SystemExit(f"{exc}\n(saved the last page as page.html)")

    html = client.page.text
    print("\nhidden inputs present on page:")
    for hid in ("pInstance", "pFlowId", "pFlowStepId", "pSalt",
                "pPageItemsProtected", "pPageItemsRowVersion", "pPageChecksum"):
        m = re.search(rf'<input[^>]*id="{hid}"[^>]*>', html)
        print(f"  {hid:22}", "yes" if m else "NO")

    from rrs import open_data
    print("\nall ajaxIdentifiers on the page (nearest the region id first):")
    ids = open_data.list_ajax_identifiers(html)
    for e in sorted(ids, key=lambda e: (e["distance"] is None, e["distance"])):
        print(f"  distance={e['distance']}  {e['label']}")
    rc = client._report_client.config
    print("\nregion config in use:")
    print("  identifier:", open_data.decode_identifier(rc.ajax_identifier))
    print("  ajax_columns:", short(rc.ajax_columns, 80))
    print("  report_id:", rc.report_id, " region_id:", rc.region_id)

    print("\nstate:", {k: short(getattr(client.session.state, k))
                       for k in ("instance", "flow_id", "flow_step_id", "salt",
                                 "page_items_protected")})
    print("\ncookie jar:")
    for ck in client.session._http.cookies:
        print(f"  {ck.name:20} domain={ck.domain} path={ck.path} secure={ck.secure}")

    text = client.fetch_report(page_size=5)
    print("\nresponse:", text[:300])

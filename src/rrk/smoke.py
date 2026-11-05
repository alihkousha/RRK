import re
from rrs.client import RRSClient

with RRSClient() as client:
    page = client.open_session()
    print("status:", page.status_code)
    print("final url:", page.url)
    print("state:", client.session.state)
    print("cookies:", client._http.cookies.get_dict() if hasattr(client, "_http") else client.session._http.cookies.get_dict())

    html = page.text
    open("page.html", "w", encoding="utf-8").write(html)
    for key in ("pInstance", "ajaxIdentifier", "ajaxColumns", "pFlowStepId"):
        print(key, "->", [m.group(0)[:120] for m in re.finditer(key + r".{0,80}", html)][:2])

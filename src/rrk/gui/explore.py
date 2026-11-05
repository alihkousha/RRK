"""What the explorer shows: read-only views of the scraper's database, and the graph
of who is linked to which company.

Every ad links the people, companies and addresses its text names (``mentions``)
to the company it is about, and a representative to the company they sit for
("به نمایندگی").  Node keys are "<kind letter>:<id>" (p: person, c: company,
a: address): a postal code can also be someone's national code; a person named
without a valid code is "p:~<company>~<name>".  The graph lives in the explorer's
database (``g_nodes``, ``g_edges``), rebuilt by :func:`build_graph`: millions of
mentions don't fit in memory beside a running scrape.
"""
from __future__ import annotations

import heapq
import json
import os
import re
import time
from array import array
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path

from database.connection import connect_explorer
from database.models import DAY, NOTICE
from database.repository import AdRepository
from parsing.entities import LEFT, VERSION, ad_mentions, normalize
from models.issuer import office
from parsing.events import notice_capital, notice_events
from scraper.windows import CAP

KEY = {"person": "p:", "company": "c:", "address": "a:"}
KEY_OF = "(CASE {0}.kind WHEN 'person' THEN 'p:' WHEN 'company' THEN 'c:' ELSE 'a:' END || {0}.entity_id)"  # KEY in SQL
AD_COLUMNS = (f"ads.detail_code AS code, ads.ad_type AS type, ads.title, {DAY} AS day,"
              " ads.newspaper_number, ads.newspaper_date, ads.page_number")
NODE = "id, key, kind, name, notices, links"


def _read(row: tuple) -> tuple:
    """One stored text, read for the explorer (in a worker process when there is a backlog)."""
    rowid, code, text, cid, name, version, notice, day, issuer, ad_type = row
    norm = normalize(text or "")
    rows = ad_mentions(norm, cid, name)
    subject = next((r[1] for r in rows if r[7]), None)
    events = notice_events(norm, ad_type, [r[5] for r in rows])
    return (rowid, code, None if version is not None else norm.replace("<br>", " "), rows, notice, day, subject, issuer,
            f",{','.join(events)}," if events else None, *(notice_capital(norm, ad_type) or (None, None)), office(issuer))


def index_texts(repo: AdRepository, batch: int = 2000, workers: int = min(4, (os.cpu_count() or 2) - 1),
                after: int = 0) -> int:
    """Read the stored full texts past details rowid *after* that the explorer hasn't read
    with this VERSION into ``mentions`` and ``ad_text``; returns how many.  A backlog (the
    first start, a new VERSION) is read by *workers* processes while the batch before is written."""
    done, pool, waiting = 0, None, None
    try:
        while True:
            rows = repo.unread_texts(VERSION, batch, after)
            if pool is None and len(rows) == batch and workers > 1:
                # spawn: a fork of this threaded server could copy a lock another thread holds
                pool = ProcessPoolExecutor(workers, mp_context=get_context("spawn"))
            read = pool.map(_read, rows, chunksize=50) if pool else map(_read, rows)
            if waiting is not None:
                repo.save_mentions(list(waiting), VERSION)
            if not rows:
                break
            waiting, done, after = read, done + len(rows), rows[-1][0]
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    return done


def resolve_people(conn) -> None:
    """Give each person an ad names without a valid national code (named without one, or
    with a wrong check digit) the code the same company's other ads give that name, when
    they give exactly one; else "~<company>~<name>" (a wrong code as written stays)."""
    conn.execute("DROP TABLE IF EXISTS temp.known")
    conn.execute("""CREATE TEMP TABLE known AS
        SELECT i.subject AS company, replace(m.name, ' ', '') AS name, min(m.written) AS code
        FROM mentions m JOIN indexed i USING (detail_code)
        WHERE m.kind = 'person' AND m.valid = 1 AND m.name IS NOT NULL AND i.subject IS NOT NULL
        GROUP BY 1, 2 HAVING count(DISTINCT m.written) = 1""")
    conn.execute("CREATE UNIQUE INDEX temp.known_name ON known (company, name)")
    conn.execute("""UPDATE mentions SET entity_id = r.new FROM (
            SELECT m.rowid AS id, coalesce(k.code, m.written, '~' || i.subject || '~' || replace(m.name, ' ', '')) AS new
            FROM mentions m JOIN indexed i USING (detail_code)
            LEFT JOIN temp.known k ON k.company = i.subject AND k.name = replace(m.name, ' ', '')
            WHERE m.kind = 'person' AND m.valid IS NOT 1 AND m.name IS NOT NULL AND i.subject IS NOT NULL) AS r
        WHERE mentions.rowid = r.id AND mentions.entity_id != r.new""")
    conn.execute("DROP TABLE temp.known")


def graph_outdated(conn) -> bool:
    """Whether ``mentions`` changed since :func:`build_graph` last ran."""
    return bool(conn.execute("SELECT (SELECT coalesce(max(rowid), 0) FROM mentions)"
                             " IS NOT (SELECT mentions FROM g_meta)").fetchone()[0])


def build_notices(conn) -> None:
    """Rebuild ``notices`` from ``ads``, ``indexed`` and ``mentions``, in the caller's transaction."""
    conn.execute("DROP INDEX IF EXISTS notices_day")  # faster to build once filled
    conn.execute("DROP INDEX IF EXISTS notices_notice")
    conn.execute("DROP INDEX IF EXISTS notices_subject")
    conn.create_function("office", 1, office, deterministic=True)  # for the texts read before indexed had it
    conn.execute("UPDATE indexed SET office = office(issuer) WHERE office IS NULL AND issuer IS NOT NULL")
    conn.execute("DELETE FROM notices")
    # The roles each text names someone in: the role filter without a scan of mentions (14 s a page).
    conn.execute("CREATE TEMP TABLE text_roles (detail_code TEXT PRIMARY KEY, roles TEXT) WITHOUT ROWID")
    conn.execute("INSERT INTO text_roles SELECT detail_code, ',' || group_concat(DISTINCT role) || ','"
                 " FROM mentions WHERE role > '' GROUP BY 1")
    conn.execute(f"""INSERT INTO notices (notice, code, text, day, types, issuer, issues, events, capital_from, capital_to,
                                          office, roles, subject)
        SELECT notice, code, text, day, types, issuer, issues, events, capital_from, capital_to, office,
               (SELECT roles FROM text_roles WHERE detail_code = code), subject FROM (
        SELECT notice, substr(first, instr(first, '|') + 1) AS code, text, day, types, issuer, issues, events,
               capital_from, capital_to, office, subject FROM (
            SELECT {NOTICE} AS notice, min({DAY}) AS day, group_concat(DISTINCT ads.ad_type) AS types,
                   max(i.issuer) AS issuer, count(*) AS issues, max(i.text) AS text, max(i.events) AS events,
                   max(i.capital_from) AS capital_from, max(i.capital_to) AS capital_to, max(i.office) AS office,
                   max(i.subject) AS subject,
                   min((i.detail_code IS NULL) || coalesce(ads.newspaper_date, '~') || '|' || ads.detail_code) AS first
            FROM ads LEFT JOIN indexed i USING (detail_code) GROUP BY 1))""")
    conn.execute("DROP TABLE text_roles")
    conn.execute("CREATE INDEX notices_day ON notices (day, code)")
    conn.execute("CREATE INDEX notices_notice ON notices (notice)")
    conn.execute("CREATE INDEX notices_subject ON notices (subject)")  # not in EXPLORE_SCHEMA: older tables lack it


def build_graph(conn) -> None:
    """:func:`resolve_people`, then rebuild ``g_nodes``, ``g_edges``, ``notices`` and the leads
    from ``mentions`` in one transaction: pages read the graph before until it commits."""
    key = KEY_OF.format("m")
    with conn:
        conn.execute("BEGIN")
        top = conn.execute("SELECT coalesce(max(rowid), 0) FROM mentions").fetchone()[0]
        resolve_people(conn)
        conn.execute("DELETE FROM g_edges")
        conn.execute("DELETE FROM g_nodes")
        conn.execute("DROP INDEX IF EXISTS g_edges_a")  # faster to build once filled
        conn.execute("DROP INDEX IF EXISTS g_edges_b")
        # Every link a notice makes: whom it names to the company it is about, a
        # representative to the company they sit for.
        conn.execute("DROP TABLE IF EXISTS temp.links")
        conn.execute(f"""CREATE TEMP TABLE links AS
            SELECT {key} AS a, 'c:' || i.subject AS b, i.notice AS notice, m.role AS role, i.day AS day
            FROM mentions m JOIN indexed i USING (detail_code) WHERE m.subject = 0 AND i.subject IS NOT NULL
            UNION ALL
            SELECT {key}, 'c:' || m.represents, i.notice, 'representative', i.day
            FROM mentions m JOIN indexed i USING (detail_code) WHERE m.represents IS NOT NULL""")
        conn.execute(f"""INSERT INTO g_nodes (key, kind, name, names, notices, about, first, last, roles)
            SELECT key, kind, name, names, notices, about, first, last, roles FROM (
                SELECT {key} AS key, m.kind AS kind, count(DISTINCT i.notice) AS notices,
                       count(DISTINCT CASE WHEN m.subject THEN i.notice END) AS about, min(i.day) AS first,
                       max(i.day) AS last, ',' || group_concat(DISTINCT CASE WHEN m.subject = 0 THEN coalesce(m.role, '')
                           || iif(m.represents IS NULL, '', ',representative') END) || ',' AS roles
                FROM mentions m JOIN indexed i USING (detail_code) GROUP BY 1)
            LEFT JOIN (  -- its own notices' name, else the one written most
                SELECT key, name, max(own * 1000000 + n), group_concat(name, '|') AS names FROM (
                    SELECT {key} AS key, m.name AS name, max(m.subject) AS own, count(*) AS n
                    FROM mentions m WHERE m.name IS NOT NULL GROUP BY 1, 2) GROUP BY key) USING (key)""")
        conn.execute(f"""INSERT INTO g_edges (a, b, dirs, notices, roles, first, last, ended, service, kind)
            SELECT min(x.id, y.id) AS lo, max(x.id, y.id) AS hi, max(x.id < y.id) + 2 * max(x.id > y.id),
                   count(DISTINCT l.notice), ',' || group_concat(l.role) || ',', min(l.day), max(l.day),
                   instr(max(coalesce(l.day, '') || '|' || coalesce(l.role, '')), '{LEFT}') > 0,
                   max(coalesce(l.role, '') != '') AND min(replace(replace(replace(replace(coalesce(l.role, ''),
                       'alternate_inspector', ''), 'inspector', ''), '{LEFT}', ''), ',', '') = ''),
                   max(iif(x.kind = 'company', y.kind, x.kind))
            FROM temp.links l JOIN g_nodes x ON x.key = l.a JOIN g_nodes y ON y.key = l.b
            WHERE l.a != l.b GROUP BY lo, hi""")
        conn.execute("DROP TABLE temp.links")
        conn.execute("CREATE INDEX g_edges_a ON g_edges (a)")
        conn.execute("CREATE INDEX g_edges_b ON g_edges (b)")
        conn.execute("""UPDATE g_nodes SET links = d.n, current = d.c FROM (SELECT id, count(*) AS n, sum(NOT ended) AS c
                            FROM (SELECT a AS id, ended FROM g_edges UNION ALL SELECT b, ended FROM g_edges) GROUP BY id) AS d
                        WHERE d.id = g_nodes.id""")
        build_notices(conn)
        leads = json.dumps(_leads(conn), ensure_ascii=False)
        conn.execute("DELETE FROM g_meta")
        conn.execute("INSERT INTO g_meta VALUES (?, ?, ?)", (datetime.now().isoformat(timespec="seconds"), top, leads))


def _node(row) -> dict:
    key = row["key"]
    return {"key": key, "kind": row["kind"], "id": "" if key[2] == "~" else key[2:], "name": row["name"],
            "ads": row["notices"], "links": row["links"]}


def _edge(row, a: str, b: str) -> dict:
    roles = Counter(r for r in (row["roles"] or "").split(",") if r)
    return {"a": a, "b": b, "ads": row["notices"], "roles": [r for r, _ in roles.most_common()],
            "first": row["first"], "last": row["last"], "ended": bool(row["ended"]), "service": bool(row["service"])}


def _nodes(conn, ids) -> dict[int, dict]:
    """:func:`_node` of each of the g_nodes *ids*, by id."""
    ids, found = list(dict.fromkeys(ids)), {}
    for at in range(0, len(ids), 900):
        chunk = ids[at:at + 900]
        found.update((row["id"], _node(row)) for row in conn.execute(
            f"SELECT {NODE} FROM g_nodes WHERE id IN ({', '.join('?' * len(chunk))})", chunk))
    return found


def _leads(conn, top: int = 50, since: str | None = None, until: str | None = None,
           current: bool = False) -> dict:
    """Rankings to start from: no conclusions, only where to look.  Seats leave inspectors
    out: an audit firm or auditor sits on hundreds of unrelated companies.  Given *since* or
    *until* ("1395", "1395/06"), only the links and notices of those years count; given
    *current*, only the links no notice ended."""
    args = {"since": since or "", "until": (until or "9999") + "~", "top": top}
    only = "service = 0 AND coalesce(last, '') >= :since AND coalesce(first, '') <= :until" + (
        " AND ended = 0" if current else "")

    def roles(i: int) -> list[tuple[str, int]]:  # of the companies *i* sits on
        rows = conn.execute(f"SELECT roles FROM g_edges WHERE a = :i AND dirs & 1 AND {only} UNION ALL"
                            f" SELECT roles FROM g_edges WHERE b = :i AND dirs & 2 AND {only}", dict(args, i=i))
        return Counter(r for (rs,) in rows for r in set((rs or "").split(",")) if r).most_common()

    def most_seats(kind: str) -> list:  # the companies whose notices name it (an address too)
        return conn.execute(f"""SELECT me, count(*) AS n FROM (SELECT a AS me FROM g_edges WHERE dirs & 1 AND {only}
                               UNION ALL SELECT b FROM g_edges WHERE dirs & 2 AND {only}) JOIN g_nodes ON id = me
                               WHERE kind = :kind GROUP BY me HAVING n >= 2 ORDER BY n DESC LIMIT :top""",
                            dict(args, kind=kind)).fetchall()

    hubs, seats, addresses = most_seats("person"), most_seats("company"), most_seats("address")
    # People who keep sitting on the same boards; a cooperative's hundreds of members would drown the rest.
    conn.execute("DROP TABLE IF EXISTS temp.seats")
    conn.execute(f"""CREATE TEMP TABLE seats AS
        SELECT e.a AS person, e.b AS company FROM (SELECT a, b FROM g_edges WHERE {only}) e
            JOIN g_nodes p ON p.id = e.a WHERE p.kind = 'person'
        UNION ALL SELECT e.b, e.a FROM (SELECT a, b FROM g_edges WHERE {only}) e
            JOIN g_nodes p ON p.id = e.b WHERE p.kind = 'person'""", args)
    conn.execute("DELETE FROM temp.seats WHERE company IN"
                 " (SELECT company FROM temp.seats GROUP BY company HAVING count(*) > 40)")
    conn.execute("DELETE FROM temp.seats WHERE person IN"
                 " (SELECT person FROM temp.seats GROUP BY person HAVING count(*) < 2)")
    conn.execute("CREATE INDEX temp.seats_company ON seats (company)")
    conn.execute("CREATE INDEX temp.seats_person ON seats (person)")
    pairs = conn.execute("""SELECT x.person, y.person, count(*) AS n FROM temp.seats x
        JOIN temp.seats y ON y.company = x.company AND y.person > x.person GROUP BY 1, 2 HAVING n >= 2
        ORDER BY n DESC LIMIT 1000""").fetchall()
    groups = _groups(conn, pairs, top)
    conn.execute("DROP TABLE temp.seats")
    busiest = conn.execute("""SELECT g.id, n FROM (SELECT subject, count(DISTINCT notice) AS n FROM indexed
                              WHERE subject IS NOT NULL AND coalesce(day, '') >= :since AND coalesce(day, '') <= :until
                              GROUP BY subject HAVING n >= 2 ORDER BY n DESC LIMIT :top)
                              JOIN g_nodes g ON g.key = 'c:' || subject ORDER BY n DESC""", args).fetchall()
    clusters = [] if since or until or current else _clusters(conn)
    nodes = _nodes(conn, [r[0] for rows in (hubs, seats, addresses, busiest) for r in rows]
                   + [i for _, people, shared in groups for i in people[:30] + shared[:30]]
                   + [i for c in clusters for _, i, _ in c["members"][:4]])
    return {
        "hubs": [dict(nodes[i], companies=n, roles=roles(i)) for i, n in hubs],
        "groups": [{"members": [nodes[i] for i in people[:8]], "size": len(people), "companies": len(shared),
                    "keys": [nodes[i]["key"] for i in people[:30] + shared[:30]]} for _, people, shared in groups],
        "seats": [dict(nodes[i], companies=n, roles=roles(i)) for i, n in seats],
        "addresses": [dict(nodes[i], companies=n) for i, n in addresses],
        "busiest": [dict(nodes[i], about=n) for i, n in busiest],
        "clusters": [{"size": c["size"], "count": c["count"], "top": [nodes[i] for _, i, _ in c["members"][:4]],
                      "keys": [key for _, _, key in c["members"]]} for c in clusters],
    }


def _groups(conn, pairs, top: int) -> list[tuple[int, list[int], list[int]]]:
    """The *pairs* of people (a, b, companies they share) joined into groups: (how many
    companies two or more of them sit on, the people (the most shared first), those
    companies), the most companies first.  Reads ``temp.seats``."""
    parent: dict[int, int] = {}

    def root(i: int) -> int:
        parent.setdefault(i, i)
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    shares = Counter()
    for a, b, n in pairs:
        if (ra := root(a)) != (rb := root(b)):
            parent[ra] = rb
        shares[a] += n
        shares[b] += n
    members: dict[int, list[int]] = {}
    for i in shares:
        members.setdefault(root(i), []).append(i)
    groups = []
    for people in members.values():
        people.sort(key=lambda i: -shares[i])
        shared = [c for (c,) in conn.execute(
            f"SELECT company FROM temp.seats WHERE person IN ({', '.join('?' * len(people))})"
            " GROUP BY company HAVING count(*) >= 2 ORDER BY count(*) DESC", people)]
        groups.append((len(shared), people, shared))
    groups.sort(key=lambda g: (-g[0], -len(g[1])))
    return groups[:top]


def _clusters(conn, top: int = 20, keep: int = 120) -> list[dict]:
    """The biggest groups of companies linked through their people (inspectors' links left
    out), each with its *keep* members with the most links."""
    size = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM g_nodes").fetchone()[0]
    parent = array("i", range(size))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in conn.execute("SELECT a, b FROM g_edges WHERE service = 0"):
        if (ra := root(a)) != (rb := root(b)):
            parent[ra] = rb
    members, companies = array("i", bytes(4 * size)), array("i", bytes(4 * size))
    for i, kind in conn.execute("SELECT id, kind FROM g_nodes"):
        r = root(i)
        members[r] += 1
        companies[r] += kind == "company"
    best = heapq.nlargest(top, (r for r in range(size) if companies[r] >= 3), key=lambda r: (companies[r], members[r]))
    count = {r: Counter({"person": 0, "company": 0, "address": 0}) for r in best}
    biggest: dict[int, list] = {r: [] for r in best}
    for i, key, kind, links in conn.execute("SELECT id, key, kind, links FROM g_nodes"):
        if (r := root(i)) in count:
            count[r][kind] += 1
            heap = biggest[r]
            (heapq.heappush if len(heap) < keep else heapq.heappushpop)(heap, (links, i, key))
    return [{"size": members[r], "count": count[r], "members": sorted(biggest[r], reverse=True)} for r in best]


def text_query(text: str) -> str | None:
    """``ad_text`` MATCH query: the words of *text* in this order, the last one as a prefix."""
    words = re.findall(r"\w+", normalize(text))
    return '"' + " ".join(words) + '"*' if words else None


def _escape(text: str) -> str:
    """*text* as a literal in a LIKE ... ESCAPE '\\' pattern."""
    return re.sub(r"([\\%_])", r"\\\1", text)


def text_pattern(text: str) -> str:
    """LIKE pattern finding *text* in a stored, unnormalized column: ``_`` for
    the letters the gazette spells two ways (ی ي, ک ك) and for spaces (or ZWNJ)."""
    return "%" + re.sub(r"[یک ]", "_", _escape(" ".join(normalize(text).split()))) + "%"


def _snippet(text: str | None, query: str, width: int = 90) -> str | None:
    if not text:
        return None
    text = " ".join(re.sub(r"<[^>]*>", " ", text).split())  # <br>, <BR>, <P>, ... in the gazette's texts
    at = max(0, normalize(text).find((re.findall(r"\w+", normalize(query)) or [""])[0]))
    start = max(0, at - width // 2)
    return ("…" if start else "") + text[start:start + width] + "…"


# The lists' columns to sort by, and to group by: an SQL expression, and whether it holds a comma separated list.
NODE_SORTS = ("links", "current", "notices", "first", "last", "name")
LINK_SORTS = ("notices", "first", "last")
NOTICE_GROUPS = {"type": ("types", True), "year": ("substr(day, 1, 4)", False), "month": ("substr(day, 1, 7)", False),
                 "office": ("office", False), "event": ("events", True), "role": ("roles", True)}
NODE_GROUPS = {"role": ("roles", True), "first": ("substr(first, 1, 4)", False), "last": ("substr(last, 1, 4)", False)}
# A node's links: some still on, some ended, all ended.
NODE_STATUS = {"current": "current > 0", "ended": "current < links", "gone": "current = 0 AND links > 0"}
LINK_GROUPS = {"role": ("roles", True), "kind": ("kind", False), "status": ("iif(ended, 'ended', 'current')", False),
               "first": ("substr(first, 1, 4)", False), "last": ("substr(last, 1, 4)", False)}


def _events(stored: str | None) -> list[str]:
    return list(filter(None, (stored or "").split(",")))


def _range(where: list, params: list, column: str, since: str | None, until: str | None) -> None:
    """*column* (a day) from *since* to *until* ("1395" or "1395/06/01"; either may be empty)."""
    if since:
        where.append(f"{column} >= ?")
        params.append(since)
    if until:
        where.append(f"{column} <= ?")
        params.append(until + "~")  # "1402" or "1402/05" takes in all of its days


def _between(where: list, params: list, column: str, low: str | None, high: str | None) -> None:
    """*column* (a number) from *low* to *high* (either may be empty)."""
    for op, value in ((">=", low), ("<=", high)):
        if value:
            where.append(f"{column} {op} ?")
            params.append(int(value))


def _active(where: list, params: list, since: str | None, until: str | None) -> None:
    """Rows whose span (first, last) overlaps the years *since* to *until*."""
    _range(where, params, "last", since, None)
    _range(where, params, "first", None, until)


def _any_event(events: list[str], every: bool = False) -> str:
    """SQL: the *events* column records any of *events* (*every*: all of them), each given as ",e,"."""
    return "(" + (" AND " if every else " OR ").join(["instr(events, ?)"] * len(events)) + ")"


def _named(text: str, prefixes) -> tuple[str, list]:
    """SQL picking the g_nodes with a key starting with one of *prefixes* by a name (every word,
    any spelling) or a number (its first digits)."""
    q = " ".join(normalize(text).split())
    if q.isdigit():
        return "(" + " OR ".join(["key GLOB ?"] * len(prefixes)) + ")", [f"{p}{q}*" for p in prefixes]
    words = q.split()[:6]
    return "(" + " AND ".join(["names LIKE ? ESCAPE '\\'"] * len(words)) + ")", [f"%{_escape(w)}%" for w in words]


def _role(where: list, params: list, role: str | None) -> None:
    """A *role* among the roles (",a,b,") of the rows; "none": named without one."""
    if role == "none":
        where.append("coalesce(replace(roles, ',', ''), '') = ''")
    elif role:
        where.append("instr(roles, ?)")
        params.append(f",{role},")


def _order(column: str, desc: bool, then: str) -> str:
    """ORDER BY *column*, the rows without a value last, then *then* for pages that don't overlap."""
    return f"{column} IS NULL, {column} {'DESC' if desc else 'ASC'}, {then}"


def _listing(conn, source: str, where: list, params: list, order: str, group: tuple[str, bool] | None,
             offset: int, limit: int) -> dict:
    """How many rows of *source* the *where* picks and a page of them in *order*; or given *group*
    (an SQL expression, and whether its value is a comma separated list), how many have each value
    (a row with several counts under each)."""
    tables = f" FROM {source} WHERE {' AND '.join(where) or '1'}"
    if group is None:
        return {"total": conn.execute(f"SELECT count(*){tables}", params).fetchone()[0],
                "rows": conn.execute(f"SELECT *{tables} ORDER BY {order} LIMIT ? OFFSET ?",
                                     [*params, limit, offset]).fetchall()}
    expr, many = group
    counts, total = Counter(), 0
    for value, n in conn.execute(f"SELECT {expr}, count(*){tables} GROUP BY 1", params):
        total += n
        for v in set((value or "").split(",")) - {""} or {""} if many else [value or ""]:
            counts[v] += n
    return {"total": total, "groups": [{"value": v, "n": n} for v, n in counts.most_common(1000)]}


def _matching(conn, leads: dict, text: str) -> dict:
    """The entries of *leads* with someone whose name or number matches *text*, each with its rank
    in the whole list and (up to 5 of) those it matched."""
    keys_of = lambda item: item.get("keys") or [item["key"]]  # a group's or cluster's members, else the node
    sql, args = _named(text, list(KEY.values()))
    keys, found = list({k for items in leads.values() for item in items for k in keys_of(item)}), {}
    for at in range(0, len(keys), 900):
        chunk = keys[at:at + 900]
        found.update((row["key"], _node(row)) for row in conn.execute(
            f"SELECT {NODE} FROM g_nodes WHERE key IN ({', '.join('?' * len(chunk))}) AND {sql}", chunk + args))
    out = {}
    for name, items in leads.items():
        out[name] = [dict(item, rank=rank, matched=hits[:5]) for rank, item in enumerate(items, 1)
                     if (hits := [found[k] for k in keys_of(item) if k in found])]
    return out


class Explorer:
    def __init__(self, db: Path) -> None:
        self.db = db
        self._overview: tuple[tuple, dict, float] | None = None
        self._leads: dict[tuple, dict] = {}  # (graph built, period, current only) -> leads
        self._fetched = (0, 0)  # (details rowid, stored pages up to it)
        connect_explorer(db).close()  # creates the explorer's database

    def _conn(self):
        return closing(connect_explorer(self.db))

    @staticmethod
    def _find(conn, keys) -> dict[str, dict]:
        """g_nodes rows of *keys*, by key (unknown keys left out)."""
        keys, found = list(dict.fromkeys(keys)), {}
        for at in range(0, len(keys), 900):
            chunk = keys[at:at + 900]
            found.update((row["key"], dict(row)) for row in conn.execute(
                f"SELECT {NODE} FROM g_nodes WHERE key IN ({', '.join('?' * len(chunk))})", chunk))
        return found

    @staticmethod
    def _edges(conn, ids, among=None) -> list:
        """g_edges rows touching *ids* (given *among*, only those with both ends in it)."""
        ids, rows = list(ids), {}
        for at in range(0, len(ids), 450):
            chunk = ids[at:at + 450]
            marks = ", ".join("?" * len(chunk))
            for row in conn.execute(f"SELECT * FROM g_edges WHERE a IN ({marks})"
                                    f" UNION ALL SELECT * FROM g_edges WHERE b IN ({marks})", chunk + chunk):
                if among is None or (row["a"] in among and row["b"] in among):
                    rows[row["a"], row["b"]] = row
        return list(rows.values())

    def index_status(self) -> dict:
        with self._conn() as conn:
            seen, fetched = self._fetched
            top = conn.execute("SELECT coalesce(max(rowid), 0) FROM details").fetchone()[0]
            if top != seen:  # shortcut: only new rows are counted; a page fetched on a retry is missed till a restart
                fetched += conn.execute("SELECT count(*) FROM details WHERE rowid > ? AND rowid <= ?"
                                        " AND fetched_at IS NOT NULL", (seen, top)).fetchone()[0]
                self._fetched = (top, fetched)
            read = conn.execute("SELECT count(*) FROM indexed WHERE version = ?", (VERSION,)).fetchone()[0]
            built = conn.execute("SELECT built FROM g_meta").fetchone()
        return {"read": read, "total": fetched, "graph": built[0] if built else None}

    def types(self) -> list[dict]:
        """The ad types, with how many ads of each are stored (one scan, unlike the overview)."""
        with self._conn() as conn:
            counts = dict(conn.execute("SELECT ad_type, count(*) FROM ads GROUP BY ad_type").fetchall())
            types = [dict(r) for r in conn.execute("SELECT code, label FROM ad_types ORDER BY CAST(code AS INTEGER)")]
            # The scraper creates windows_left: an older rrk.db has none until its next run.
            left = dict(conn.execute("SELECT ad_type, count(*) FROM windows_left GROUP BY ad_type").fetchall()) \
                if conn.execute("SELECT 1 FROM src.sqlite_master WHERE name = 'windows_left'").fetchone() else {}
        # Without a full text: from the overview's counts if they're cached (a join of every ad is too slow here).
        texts = {t["code"]: t["ads"] - t["details"] for t in self._overview[1]["types"]} if self._overview else {}
        for t in types:
            t["ads"] = counts.get(t["code"], 0)
            t["capped"] = t["ads"] == CAP
            t["left"] = left.get(t["code"], 0)  # date windows a stopped run left (--resume)
            t["missing"] = texts.get(t["code"])
        return types

    def overview(self, fresh: float = 300) -> dict:
        """What the database holds, per ad type and year (cached: the per-type counts take
        ~8 s on 2.4M ads, so they're redone only once ads, details or the graph changed,
        and during a run at most every *fresh* seconds)."""
        with self._conn() as conn:
            at = (*conn.execute("SELECT (SELECT max(rowid) FROM ads), (SELECT max(rowid) FROM details),"
                                " (SELECT built FROM g_meta)").fetchone(),)
            if self._overview and (self._overview[0] == at or (
                    self._overview[0][2] == at[2] and time.monotonic() - self._overview[2] < fresh)):
                return dict(self._overview[1], index=self.index_status())
            # One scan of ads (joined per type it was one per type); fetched_at from details_fetched.
            types = [dict(r) for r in conn.execute(
                "SELECT code, label, coalesce(n.ads, 0) AS ads, coalesce(n.details, 0) AS details, n.first, n.last"
                " FROM ad_types LEFT JOIN (SELECT ad_type, count(*) AS ads, count(fetched_at) AS details,"
                f" min({DAY}) AS first, max({DAY}) AS last FROM ads LEFT JOIN details USING (detail_code)"
                " GROUP BY ad_type) n ON n.ad_type = code ORDER BY CAST(code AS INTEGER)")]
            years = [tuple(r) for r in conn.execute(
                f"SELECT substr({DAY}, 1, 4), ad_type, count(*) FROM ads GROUP BY 1, 2 ORDER BY 1")]
            entities = dict(conn.execute("SELECT kind, count(*) FROM g_nodes GROUP BY kind").fetchall())
            unnumbered = conn.execute("SELECT count(*) FROM g_nodes WHERE key GLOB 'p:~*'").fetchone()[0]
            links = conn.execute("SELECT count(*) FROM g_edges").fetchone()[0]
            placeholder = conn.execute("SELECT count(*) FROM ads WHERE letter_date < '1301'").fetchone()[0]
        for t in types:
            t["capped"] = t["ads"] == CAP  # listed before date windows: the first 999 only
        overview = {"types": types, "years": years, "entities": entities, "unnumbered": unnumbered,
                    "links": links, "placeholder_dates": placeholder}
        self._overview = (at, overview, time.monotonic())
        return dict(overview, index=self.index_status())

    def search(self, query: str, ads: bool = True) -> dict:
        """People, companies and addresses by name (any spelling) or number, and (given *ads*)
        ads by tracking code, detail code or registration number."""
        q = " ".join(normalize(query).split())
        rows, found = [], []
        if q:
            with self._conn() as conn:
                if q.isdigit():
                    rows = list(self._find(conn, [k + q for k in KEY.values()]).values())
                    # by rrk.db's indexes (ads_tracking, details_registration): also ads without a detail page
                    found = [dict(r) for r in conn.execute(
                        f"SELECT {AD_COLUMNS} FROM ads WHERE ads.detail_code IN (SELECT detail_code FROM ads"
                        " WHERE tracking_code = :q UNION SELECT :q UNION SELECT detail_code FROM details"
                        " WHERE registration_number = :q) ORDER BY day DESC LIMIT 100", {"q": q})] if ads else []
                else:
                    words = q.split()[:6]
                    rows = [dict(r) for r in conn.execute(
                        f"SELECT {NODE} FROM g_nodes WHERE " + " AND ".join(["names LIKE ? ESCAPE '\\'"] * len(words))
                        + " ORDER BY links DESC, notices DESC LIMIT 501", [f"%{_escape(w)}%" for w in words])]
        rows.sort(key=lambda r: (-r["links"], -r["notices"]))
        return {"entities": [_node(r) for r in rows[:60]], "more": max(0, len(rows) - 60), "ads": found}

    def search_text(self, query: str, limit: int = 200) -> dict:
        """The newest ads whose full text has the words of *query*, and how many there are."""
        if (match := text_query(query)) is None:
            return {"ads": [], "total": 0}
        with self._conn() as conn:
            total = conn.execute("SELECT count(*) FROM ad_text WHERE ad_text MATCH ?", (match,)).fetchone()[0]
            rows = [dict(r) for r in conn.execute(
                f"SELECT {AD_COLUMNS}, unzip(full_text) AS full_text FROM details JOIN ads USING (detail_code) WHERE details.rowid IN"
                " (SELECT rowid FROM ad_text WHERE ad_text MATCH ?) ORDER BY day DESC LIMIT ?", (match, limit))]
        for row in rows:
            row["snippet"] = _snippet(row.pop("full_text"), query)
        return {"ads": rows, "total": total}

    def entity(self, key: str, most: int = 1000) -> dict | None:
        """A node's profile: its spellings, notices (one per notice however many issues
        printed it), its *most* strongest links, and the nodes it shares the most with."""
        with self._conn() as conn:
            if (me := self._find(conn, [key]).get(key)) is None:
                return None
            kind, number = me["kind"], key[2:]
            names = conn.execute("SELECT name, count(*) FROM mentions WHERE entity_id = ? AND kind = ?"
                                 " AND name IS NOT NULL GROUP BY name ORDER BY 2 DESC", (number, kind)).fetchall()
            ads = [dict(r) for r in conn.execute(
                f"SELECT {AD_COLUMNS}, group_concat(role) AS roles, max(subject) AS subject, max(stake) AS stake,"
                " count(DISTINCT ads.detail_code) AS issues FROM mentions JOIN ads USING (detail_code)"
                f" WHERE entity_id = ? AND kind = ? GROUP BY {NOTICE} ORDER BY day", (number, kind))]
            registry = [dict(r) for r in conn.execute(
                "SELECT registration_number, issuer, count(*) AS ads FROM mentions JOIN details USING (detail_code)"
                " WHERE entity_id = ? AND kind = 'company' AND subject = 1 GROUP BY 1, 2 ORDER BY 3 DESC",
                (number,))] if kind == "company" else []
            edges = conn.execute("SELECT * FROM (SELECT *, b AS other FROM g_edges WHERE a = :id UNION ALL"
                                 " SELECT *, a FROM g_edges WHERE b = :id) ORDER BY notices DESC LIMIT :most",
                                 {"id": me["id"], "most": most}).fetchall()
            others = _nodes(conn, [e["other"] for e in edges])
            co = self._co(conn, me)
        for ad in ads:
            ad["roles"] = sorted(set(filter(None, (ad["roles"] or "").split(","))))
        links = [dict(_edge(e, key, others[e["other"]]["key"]), node=others[e["other"]]) for e in edges]
        links.sort(key=lambda link: (-link["ads"], link["first"] or ""))
        return {"node": _node(me), "names": [tuple(n) for n in names], "ads": ads, "links": links,
                "more": max(0, me["links"] - len(links)), "co": co, "registry": registry}

    @staticmethod
    def _co(conn, me: dict, limit: int = 40) -> list[dict]:
        """Same-kind nodes two steps away: people sharing companies, companies sharing people
        (not through inspectors: an auditor's clients share nothing)."""
        rows = conn.execute("""
            WITH mine AS (SELECT b AS mid FROM g_edges WHERE a = :id AND service = 0
                          UNION ALL SELECT a FROM g_edges WHERE b = :id AND service = 0 LIMIT 3000),
            two AS (SELECT m.mid, e.b AS other FROM mine m JOIN g_edges e ON e.a = m.mid AND e.service = 0
                    UNION ALL SELECT m.mid, e.a FROM mine m JOIN g_edges e ON e.b = m.mid AND e.service = 0)
            SELECT other, count(*) AS shared, group_concat(mid) AS via FROM two JOIN g_nodes n ON n.id = two.other
            WHERE other != :id AND n.kind = :kind GROUP BY other ORDER BY shared DESC, n.links DESC LIMIT :limit""",
                            {"id": me["id"], "kind": me["kind"], "limit": limit}).fetchall()
        via = {other: [int(v) for v in mids.split(",")[:4]] for other, _, mids in rows}
        nodes = _nodes(conn, [r[0] for r in rows] + [v for vs in via.values() for v in vs])
        return [dict(nodes[other], shared=shared, via=[nodes[v] for v in via[other]]) for other, shared, _ in rows]

    def ad(self, code: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ads.*, unzip(full_text) AS full_text, company_name, registration_number, publication_count, issuer,"
                f" fetched_at, label AS type_label, {DAY} AS day, {NOTICE} AS notice FROM ads LEFT JOIN details USING (detail_code)"
                " LEFT JOIN ad_types ON ad_types.code = ads.ad_type WHERE ads.detail_code = ?", (code,)).fetchone()
            if row is None:
                return None
            mentions = [dict(r, key=KEY[r["kind"]] + r["entity_id"]) for r in conn.execute(
                "SELECT kind, entity_id, written, valid, name, role, represents, subject, text_start, text_end, stake"
                " FROM mentions WHERE detail_code = ? ORDER BY text_start", (code,))]
            read = conn.execute("SELECT events, capital_from, capital_to FROM indexed WHERE detail_code = ?",
                                (code,)).fetchone()
            siblings = [dict(r) for r in conn.execute(
                "SELECT detail_code AS code, newspaper_number, newspaper_date, page_number FROM ads"
                f" WHERE tracking_code = ? AND {NOTICE} = ? AND detail_code != ? ORDER BY newspaper_date",
                (row["tracking_code"], row["notice"], code))]
            known = self._find(conn, [m["key"] for m in mentions])
        for m in mentions:
            m["label"] = known[m["key"]]["name"] if m["key"] in known else m["name"]
        return {"ad": dict(row, events=_events(read and read["events"]), capital_from=read and read["capital_from"],
                           capital_to=read and read["capital_to"]), "mentions": mentions, "siblings": siblings}

    def ads(self, *, types: list[str] = (), start: str | None = None, end: str | None = None,
            text: str | None = None, title: str | None = None, company: str | None = None, issue: str | None = None,
            office: str | None = None, events: list[str] = (), every: bool = False, named: str | None = None,
            who: str | None = None, role: str | None = None, capital_min: str | None = None,
            capital_max: str | None = None, issues_min: str | None = None, issues_max: str | None = None,
            sort: str | None = None, desc: bool = True, group: str | None = None, offset: int = 0, limit: int = 50,
            full: bool = False) -> dict:
        """A page of notices, the newest first (or the most printed: *sort* "issues"): one row each,
        however many issues printed it (the first printing with a text read), with whom it names;
        given *full*, its text; its *subject*: the company it is about (the listing's, else the first one
        its text gives a national id).  Filters: words of its *title*, the *company* it is about (a name or
        national id), printed in the newspaper *issue*, recording any of *events* (*every*: all),
        naming the node *named* (a KEY), naming *who* (a name or number) in *role* (any, if none),
        the capital it leaves from *capital_min* to *capital_max* (rials), printed from *issues_min*
        to *issues_max* times.  Given *group* (a NOTICE_GROUPS key), how many notices per value."""
        where, params = [], []
        if types:
            where.append("(" + " OR ".join(["instr(',' || types || ',', ?)"] * len(types)) + ")")
            params += [f",{t}," for t in types]
        _range(where, params, "day", start, end)
        if text and (match := text_query(text)):
            where.append("text IN (SELECT rowid FROM ad_text WHERE ad_text MATCH ?)")
            params.append(match)
        if words := (title or "").split()[:6]:  # in the listing's whole title (a scan of ads: 4-9 s)
            where.append("code IN (SELECT detail_code FROM ads WHERE "
                         + " AND ".join(["coalesce(full_title, title) LIKE ? ESCAPE '\\'"] * len(words)) + ")")
            params += [text_pattern(w) for w in words]
        if company := (company or "").strip():
            sql, args = _named(company, [KEY["company"]])
            where.append(f"subject IN (SELECT substr(key, 3) FROM g_nodes WHERE kind = 'company' AND {sql})")
            params += args
        if issue := (issue or "").strip():  # any of its printings
            where.append(f"notice IN (SELECT {NOTICE} FROM ads WHERE newspaper_number = ?)")
            params.append(issue)
        if office:  # a group's ("مشهد", "~ip") or words of the issuer's line
            where.append("(office = ? OR issuer LIKE ? ESCAPE '\\')")
            params += [office, text_pattern(office)]
        if events:
            where.append(_any_event(events, every))
            params += [f",{e}," for e in events]
        if who := (who or "").strip():  # that one in that role, not any one named and any one in it
            sql, args = _named(who, list(KEY.values()))
            in_role = "instr(',' || role || ',', ?) AND " if role else ""
            where.append(f"code IN (SELECT detail_code FROM mentions WHERE {in_role}entity_id IN"
                         f" (SELECT substr(key, 3) FROM g_nodes WHERE {sql}))")
            params += ([f",{role},"] if role else []) + args
        else:
            _role(where, params, role)
        _between(where, params, "capital_to", capital_min, capital_max)
        _between(where, params, "issues", issues_min, issues_max)
        if named:  # a KEY: the notices naming that person, company or address
            where.append("notice IN (SELECT notice FROM mentions JOIN indexed USING (detail_code)"
                         " WHERE entity_id = ? AND kind = ?)")
            params += [named[2:], {v: k for k, v in KEY.items()}.get(named[:2])]
        way = "DESC" if desc else "ASC"
        order = f"{'issues' if sort == 'issues' else 'day'} {way}, code {way}"  # by day: the notices_day index
        with self._conn() as conn:
            found = _listing(conn, "notices", where, params, order, NOTICE_GROUPS.get(group), offset, limit)
            if "groups" in found:
                return found
            shown = found["rows"]
            codes = [row["code"] for row in shown]
            marks = ", ".join("?" * len(codes))
            ads = {row["code"]: dict(row) for row in conn.execute(
                f"SELECT {AD_COLUMNS}, ads.full_title, ads.tracking_code, ads.letter_date, ads.indicator_number,"
                " ads.company_national_id, company_name, registration_number, issuer"
                + (", unzip(full_text) AS full_text, summary" if full else "")
                + f" FROM ads LEFT JOIN details USING (detail_code) WHERE ads.detail_code IN ({marks})", codes)}
            named: dict[str, dict[str, dict]] = {}
            for code, kind, number, name, role, stake in conn.execute(
                    "SELECT detail_code, kind, entity_id, name, role, stake FROM mentions"
                    f" WHERE detail_code IN ({marks}) AND subject = 0 ORDER BY text_start", codes):
                key = KEY[kind] + number
                m = named.setdefault(code, {}).setdefault(key, {
                    "key": key, "kind": kind, "id": "" if number[0] == "~" else number, "name": name, "roles": {},
                    "stake": None})
                m["roles"].update(dict.fromkeys(filter(None, (role or "").split(","))))
                m["stake"] = m["stake"] or stake
            known = self._find(conn, [key for keys in named.values() for key in keys]
                               + [f"c:{row['subject']}" for row in shown if row["subject"]])
        for row in shown:
            ad = ads[row["code"]]
            ad.update(types=row["types"].split(","), issues=row["issues"], events=_events(row["events"]),
                      capital_from=row["capital_from"], capital_to=row["capital_to"], subject=row["subject"],
                      subject_name=(known.get(f"c:{row['subject']}") or {}).get("name"), named=[
                dict(m, name=(known.get(m["key"]) or {}).get("name") or m["name"], roles=list(m["roles"]))
                for m in named.get(row["code"], {}).values()])
        return {"total": found["total"], "rows": [ads[row["code"]] for row in shown]}

    def nodes(self, kind: str, f: dict, sort: str | None = None, desc: bool = True, group: str | None = None,
              offset: int = 0, limit: int = 50) -> dict:
        """A page of the people, companies or addresses (*kind*) the filters *f* pick: q (a name or
        number), role, code ("yes", "no": with or without a national code), first_from/first_to and
        last_from/last_to (the years of the first and the latest notice naming them), active_from/
        active_to (named over years overlapping those), min/max (links), notices_min/notices_max,
        current_min/current_max (links no notice ended), status (NODE_STATUS); companies also by the notices about them, each filter met by any one: from
        an office, recording any of events, stating a capital from capital_min to capital_max.  The most linked first, or by
        *sort* (NODE_SORTS).  Given *group* (a NODE_GROUPS key), how many of them per value."""
        if kind not in KEY:
            raise ValueError(f"unknown kind {kind!r}")
        where, params = ["kind = ?"], [kind]
        if q := (f.get("q") or "").strip():
            sql, args = _named(q, [KEY[kind]])
            where.append(sql)
            params += args
        _role(where, params, f.get("role"))
        if f.get("code") in ("yes", "no"):
            where.append(("NOT " if f["code"] == "yes" else "") + "key GLOB 'p:~*'")
        _range(where, params, "first", f.get("first_from"), f.get("first_to"))
        _range(where, params, "last", f.get("last_from"), f.get("last_to"))
        _active(where, params, f.get("active_from"), f.get("active_to"))
        _between(where, params, "links", f.get("min"), f.get("max"))
        _between(where, params, "notices", f.get("notices_min"), f.get("notices_max"))
        _between(where, params, "current", f.get("current_min"), f.get("current_max"))
        if f.get("status") in NODE_STATUS:
            where.append(NODE_STATUS[f["status"]])
        if kind == "company":  # by the notices about it: each filter met by one of them, not all by the same
            about = "key IN (SELECT 'c:' || subject FROM indexed WHERE {})"
            if place := (f.get("office") or "").strip():
                where.append(about.format("(office = ? OR issuer LIKE ? ESCAPE '\\')"))
                params += [place, text_pattern(place)]
            if events := _events(f.get("events")):
                where.append(about.format(_any_event(events)))
                params += [f",{e}," for e in events]
            capital, rials = [], []
            _between(capital, rials, "capital_to", f.get("capital_min"), f.get("capital_max"))
            if capital:
                where.append(about.format(" AND ".join(capital)))
                params += rials
        order = _order(sort if sort in NODE_SORTS else "links", desc, "id")
        with self._conn() as conn:
            found = _listing(conn, "g_nodes", where, params, order, NODE_GROUPS.get(group), offset, limit)
        if "rows" in found:
            found["rows"] = [dict(_node(row), current=row["current"], first=row["first"], last=row["last"],
                                  about=row["about"], roles=sorted(set((row["roles"] or "").split(",")) - {""}))
                             for row in found["rows"]]
        return found

    def links(self, f: dict, sort: str | None = None, desc: bool = True, group: str | None = None,
              offset: int = 0, limit: int = 50) -> dict:
        """A page of the links the filters *f* pick: q (either end's name or number; with q2, one
        end each), kind (what they link to a company), role, status ("current", "ended"), service
        ("0": no inspector-only links), first_from/first_to and last_from/last_to (the years of their
        first and latest notice), active_from/active_to (spans overlapping those years), min/max
        (notices), years (at least this many from the first notice to the latest); the most notices
        first, or by *sort* (LINK_SORTS).  Each row's *a* is the one named in *b*'s notices (of two
        companies naming each other, either).  Given *group* (a LINK_GROUPS key), how many links
        per value."""
        where, params = [], []
        ends = [_named(q, list(KEY.values())) for q in (f.get("q"), f.get("q2")) if (q or "").strip()]
        if len(ends) == 1:
            sql, args = ends[0]
            where.append(f"rowid IN (WITH hit AS MATERIALIZED (SELECT id FROM g_nodes WHERE {sql})"
                         " SELECT rowid FROM g_edges WHERE a IN hit UNION SELECT rowid FROM g_edges WHERE b IN hit)")
            params += args
        elif ends:
            (sql, args), (sql2, args2) = ends
            where.append(f"rowid IN (WITH one AS MATERIALIZED (SELECT id FROM g_nodes WHERE {sql}),"
                         f" two AS MATERIALIZED (SELECT id FROM g_nodes WHERE {sql2})"
                         " SELECT rowid FROM g_edges WHERE a IN one AND b IN two"
                         " UNION SELECT rowid FROM g_edges WHERE a IN two AND b IN one)")
            params += args + args2
        if f.get("kind") in KEY:
            where.append("kind = ?")
            params.append(f["kind"])
        _role(where, params, f.get("role"))
        if f.get("status") in ("current", "ended"):
            where.append(f"ended = {int(f['status'] == 'ended')}")
        if f.get("service") == "0":
            where.append("service = 0")
        _range(where, params, "first", f.get("first_from"), f.get("first_to"))
        _range(where, params, "last", f.get("last_from"), f.get("last_to"))
        _active(where, params, f.get("active_from"), f.get("active_to"))
        _between(where, params, "notices", f.get("min"), f.get("max"))
        if f.get("years"):
            where.append("CAST(substr(last, 1, 4) AS INT) - CAST(substr(first, 1, 4) AS INT) >= ?")
            params.append(int(f["years"]))
        order = _order(sort if sort in LINK_SORTS else "notices", desc, "rowid")
        with self._conn() as conn:
            found = _listing(conn, "g_edges", where, params, order, LINK_GROUPS.get(group), offset, limit)
            if "rows" in found:
                nodes = _nodes(conn, [i for row in found["rows"] for i in (row["a"], row["b"])])
                ends = [(row["b"], row["a"]) if row["dirs"] == 2 else (row["a"], row["b"]) for row in found["rows"]]
                found["rows"] = [dict(_edge(row, nodes[a]["key"], nodes[b]["key"]), a=nodes[a], b=nodes[b])
                                 for row, (a, b) in zip(found["rows"], ends)]
        return found

    def expand(self, key: str, have: list[str], limit: int = 30) -> dict:
        """*key*'s strongest *limit* links, and every link between them and the nodes the view *has*."""
        with self._conn() as conn:
            if (me := self._find(conn, [key]).get(key)) is None:
                return {"nodes": [], "edges": [], "more": 0}
            around = conn.execute("""SELECT n.key FROM (SELECT b AS other, notices FROM g_edges WHERE a = :id
                                     UNION ALL SELECT a, notices FROM g_edges WHERE b = :id) e
                                     JOIN g_nodes n ON n.id = e.other ORDER BY e.notices DESC, n.links DESC LIMIT :limit""",
                                  {"id": me["id"], "limit": limit}).fetchall()
            return dict(self._subgraph(conn, [key, *(r[0] for r in around)], have),
                        more=max(0, me["links"] - len(around)))

    def subgraph(self, keys: list[str], have=()) -> dict:
        """*keys* and every link between them and the nodes the view *has*."""
        with self._conn() as conn:
            return self._subgraph(conn, keys, have)

    def common(self, keys: list[str], have=(), service: bool = False, limit: int = 40) -> dict:
        """What 2 to 5 nodes have in common: the nodes linked to at least two of them (the
        most first; through inspectors' links only given *service*), as a subgraph with them,
        and to how many of them each is linked."""
        with self._conn() as conn:
            given = [row["id"] for row in self._find(conn, keys[:5]).values()]
            if len(given) < 2:
                return {"nodes": [], "edges": [], "shared": {}}
            marks, only = ", ".join("?" * len(given)), "" if service else " AND service = 0"
            rows = conn.execute(f"""SELECT g.key, count(DISTINCT e.me) AS shared FROM (
                    SELECT a AS me, b AS other FROM g_edges WHERE a IN ({marks}){only}
                    UNION ALL SELECT b, a FROM g_edges WHERE b IN ({marks}){only}) e JOIN g_nodes g ON g.id = e.other
                WHERE e.other NOT IN ({marks}) GROUP BY e.other HAVING shared >= 2
                ORDER BY shared DESC, g.links DESC LIMIT ?""",
                                [*given, *given, *given, limit]).fetchall()
            shared = {key: n for key, n in rows}
            return dict(self._subgraph(conn, [*keys[:5], *shared], have), shared=shared)

    def _subgraph(self, conn, keys: list[str], have) -> dict:
        found = self._find(conn, [*keys[:400], *have])
        mine = [found[k] for k in dict.fromkeys(keys) if k in found][:400]
        by_id = {row["id"]: row for row in found.values()}
        edges = self._edges(conn, [row["id"] for row in mine], among=by_id)
        return {"nodes": [_node(row) for row in mine],
                "edges": [_edge(e, by_id[e["a"]]["key"], by_id[e["b"]]["key"]) for e in edges]}

    def path(self, a: str, b: str, avoid: set[str] = frozenset(), service: bool = False,
             most: int = 300_000) -> dict | None:
        """The shortest chain of links from *a* to *b*, around the nodes to *avoid* (and, unless
        *service*, inspectors' links): searched from both ends, the smaller side first, until
        *most* nodes were seen."""
        with self._conn() as conn:
            found = self._find(conn, [a, b, *avoid])
            if a not in found or b not in found:
                return None
            skip = {found[k]["id"] for k in avoid if k in found}
            start, goal = found[a]["id"], found[b]["id"]
            only = "" if service else " AND service = 0"
            came = [{start: None}, {goal: None}]  # each side: node -> the one it was reached from
            fronts = [[start], [goal]]
            meet = start if start == goal else None
            while meet is None and all(fronts) and len(came[0]) + len(came[1]) < most:
                s = 0 if len(fronts[0]) <= len(fronts[1]) else 1
                step = []
                for at in range(0, len(fronts[s]), 450):
                    chunk = fronts[s][at:at + 450]
                    marks = ", ".join("?" * len(chunk))
                    for here, there in conn.execute(f"SELECT a, b FROM g_edges WHERE a IN ({marks}){only}"
                                                    f" UNION ALL SELECT b, a FROM g_edges WHERE b IN ({marks}){only}",
                                                    chunk + chunk):
                        if there not in came[s] and there not in skip:
                            came[s][there] = here
                            step.append(there)
                            if there in came[1 - s]:
                                meet = there
                                break
                    if meet is not None:
                        break
                fronts[s] = step
            if meet is None:
                return None
            chain = [meet]
            while came[0][chain[0]] is not None:
                chain.insert(0, came[0][chain[0]])
            while came[1][chain[-1]] is not None:
                chain.append(came[1][chain[-1]])
            nodes = _nodes(conn, chain)
            edges = [_edge(conn.execute("SELECT * FROM g_edges WHERE a = ? AND b = ?", (min(x, y), max(x, y))).fetchone(),
                           nodes[x]["key"], nodes[y]["key"]) for x, y in zip(chain, chain[1:])]
        return {"nodes": [nodes[i] for i in chain], "edges": edges}

    def link(self, a: str, b: str) -> dict | None:
        """The notices behind the link between *a* and *b* (one ad for each), with the roles they give."""
        with self._conn() as conn:
            found = self._find(conn, [a, b])
            if a not in found or b not in found:
                return None
            notices: dict[str, tuple[str, set[str]]] = {}
            for x, y in ((a, b), (b, a)):
                if not y.startswith(KEY["company"]):  # links only ever lead to companies
                    continue
                for code, role, notice, subject, represents in conn.execute(
                        "SELECT m.detail_code, m.role, i.notice, i.subject, m.represents FROM mentions m"
                        " JOIN indexed i USING (detail_code) WHERE m.entity_id = ? AND m.kind = ? AND m.subject = 0"
                        " AND (i.subject = ? OR m.represents = ?)", (x[2:], found[x]["kind"], y[2:], y[2:])):
                    roles = notices.setdefault(notice, (code, set()))[1]
                    if subject == y[2:]:
                        roles.update(filter(None, (role or "").split(",")))
                    if represents == y[2:]:
                        roles.add("representative")
            if not notices:
                return None
            codes = {code: roles for code, roles in notices.values()}
            rows = [dict(r) for r in conn.execute(
                f"SELECT {AD_COLUMNS} FROM ads WHERE detail_code IN ({', '.join('?' * len(codes))}) ORDER BY day",
                list(codes))]
        for row in rows:
            row["roles"] = sorted(codes[row["code"]])
        return {"a": _node(found[a]), "b": _node(found[b]), "ads": rows}

    def leads(self, since: str | None = None, until: str | None = None, current: bool = False,
              q: str | None = None) -> dict:
        """The leads of the last graph build; for a period or only current links, worked out
        then (seconds) and kept until the next build.  Given *q*, only the entries with someone
        of that name or number (:func:`_matching`)."""
        with self._conn() as conn:
            row = conn.execute("SELECT built, leads FROM g_meta").fetchone()
            if row is None:
                return {}
            if not (since or until or current):
                leads = json.loads(row["leads"])
            else:
                key = (row["built"], since, until, current)
                if key not in self._leads:
                    if len(self._leads) >= 20:
                        self._leads.clear()
                    self._leads[key] = _leads(conn, since=since, until=until, current=current)
                leads = self._leads[key]
            return _matching(conn, leads, q) if q and q.strip() else leads

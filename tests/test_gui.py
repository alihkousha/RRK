from time import sleep  # before conftest's `sleeps` replaces time.sleep
from types import SimpleNamespace

import pytest

from database.connection import connect, connect_explorer, explore_path
from database.repository import AdRepository
from gui import server
from gui.explore import Explorer, build_graph, graph_outdated, index_texts
from test_entities import CHANGES

SECOND = ("آگهي تغييرات شركت دوم نمونه به شماره ثبت 9 و شناسه ملي 10200000002 تصميمات ذيل اتخاذ شد:"
          " علي رضايي به شماره ملي 1111111112 به سمت مديرعامل انتخاب شد.")
BOARD = ("5- مدیران و صاحبان امضاء: آقای اصغر نمونه به سمت مدیر عامل و رئیس هیئت مدیره ـ آقای قربان نمونه"
         " به سمت نائب رئیس هیئت مدیره انتخاب شدند.")


def make_explorer(tmp_path, ads):
    """An Explorer over *ads* (code, tracking code, letter date, company national id, text, and
    optionally the letter number: else one per tracking code), read and graphed."""
    db = tmp_path / "rrk.db"
    repo = AdRepository(connect(db))
    for code, notice, day, company, text, *letter in ads:
        repo.conn.execute("INSERT INTO ads (tracking_code, title, letter_date, detail_code, ad_type, company_national_id,"
                          " indicator_number, newspaper_date, first_seen, last_seen)"
                          " VALUES (?, 'آگهی', ?, ?, '2', ?, ?, ?, 't', 't')",
                          (notice, day, code, company, letter[0] if letter else f"{notice}/ت32", f"{day[:8]}1{code}"))
        repo.conn.execute("INSERT INTO details (detail_code, full_text, fetched_at) VALUES (?, ?, 't')", (code, text))
    repo.conn.commit()
    reader = AdRepository(connect_explorer(db))
    assert index_texts(reader) == len(ads)
    assert index_texts(reader) == 0  # read once
    assert graph_outdated(reader.conn)
    build_graph(reader.conn)
    assert not graph_outdated(reader.conn)
    assert explore_path(db).exists() and not repo.conn.execute(  # the scraper's database only read
        "SELECT name FROM sqlite_master WHERE name IN ('mentions', 'indexed', 'ad_text')").fetchall()
    return Explorer(db)


@pytest.fixture
def explorer(tmp_path):
    return make_explorer(tmp_path, [("1", "1", "1402/01/01", "10200000001", CHANGES),
                                    ("2", "2", "1402/02/01", "10200000002", SECOND)])


def test_a_person_links_two_companies(explorer):
    [person] = explorer.search("رضايي")["entities"]  # Arabic ي in the query too
    assert (person["key"], person["name"], person["links"]) == ("p:1111111112", "علی رضایی", 2)
    roles = {link["node"]["key"]: link["roles"] for link in explorer.entity("p:1111111112")["links"]}
    assert roles == {"c:10200000001": ["board"], "c:10200000002": ["ceo"]}
    path = explorer.path("c:10200000001", "c:10200000002")
    assert [n["key"] for n in path["nodes"]] == ["c:10200000001", "p:1111111112", "c:10200000002"]
    assert explorer.path("c:10200000001", "c:10200000002", avoid={"p:1111111112"}) is None
    assert [hub["key"] for hub in explorer.leads()["hubs"]] == ["p:1111111112"]


def test_what_two_companies_have_in_common(explorer):
    common = explorer.common(["c:10200000001", "c:10200000002"])
    assert common["shared"] == {"p:1111111112": 2}
    assert {n["key"] for n in common["nodes"]} == {"c:10200000001", "c:10200000002", "p:1111111112"}
    assert len(common["edges"]) == 2


def test_people_without_a_code_notices_once_and_who_left(tmp_path):
    explorer = make_explorer(tmp_path, [
        ("1", "1", "1402/01/01", "10200000001", "آقای اصغر نمونه به کد ملی 0061555746 به سمت مدیر عامل انتخاب شد."),
        ("2", "2", "1402/02/01", "10200000001", BOARD),
        ("3", "2", "1402/02/01", "10200000001", BOARD),  # the same notice in another issue
        ("4", "4", "1402/03/01", "10200000001",
         "قبول استعفای آقای اصغر نمونه به کد ملی 0061555746 از سمت مدیرعامل پذیرفته شد.")])
    asghar = explorer.entity("p:0061555746")  # the code his other notices give him
    assert [ad["issues"] for ad in asghar["ads"]] == [1, 2, 1]
    [link] = asghar["links"]
    assert (link["ads"], link["roles"], link["ended"], link["first"], link["last"]) == (
        3, ["ceo", "chair", "left"], True, "1402/01/01", "1402/03/01")
    assert len(explorer.link("p:0061555746", "c:10200000001")["ads"]) == 3
    [ghorban] = explorer.search("قربان")["entities"]  # no code anywhere: known by name, in this company
    assert (ghorban["key"], ghorban["id"], ghorban["ads"]) == ("p:~10200000001~قرباننمونه", "", 1)
    assert explorer.overview()["unnumbered"] == 1


def test_notices_list_each_notice_once_with_whom_it_names(tmp_path):
    explorer = make_explorer(tmp_path, [
        ("1", "1", "1402/01/01", "10200000001", "آقای اصغر نمونه به کد ملی 0061555746 به سمت مدیر عامل انتخاب شد."),
        ("2", "2", "1402/02/01", "10200000001", BOARD),
        ("3", "2", "1402/02/01", "10200000001", BOARD),  # the same notice in another issue
        ("4", "333", "1388/01/01", "10200000002", SECOND, "75/ت32"),  # older ads reuse tracking codes
        ("5", "333", "1389/01/01", "10200000002", SECOND, "96/ت32")])
    page = explorer.ads()
    assert page["total"] == 4
    assert [(ad["code"], ad["issues"]) for ad in page["rows"]] == [("2", 2), ("1", 1), ("5", 1), ("4", 1)]
    [asghar, ghorban] = page["rows"][0]["named"]
    assert (asghar["key"], asghar["name"], sorted(asghar["roles"])) == ("p:0061555746", "اصغر نمونه", ["ceo", "chair"])
    assert (ghorban["id"], ghorban["roles"]) == ("", ["vice_chair"])
    assert "full_text" not in page["rows"][0] and explorer.ads(full=True)["rows"][0]["full_text"] == BOARD
    assert [ad["code"] for ad in explorer.ads(start="1389", end="1402/01")["rows"]] == ["1", "5"]
    assert [ad["code"] for ad in explorer.ads(sort="day", desc=False)["rows"]] == ["4", "5", "1", "2"]
    assert explorer.ads(sort="issues")["rows"][0]["code"] == "2"
    years = explorer.ads(group="year")
    assert (years["total"], {g["value"]: g["n"] for g in years["groups"]}) == (4, {"1402": 2, "1388": 1, "1389": 1})
    assert explorer.ads(group="type", start="1400")["groups"] == [{"value": "2", "n": 2}]
    assert explorer.ads(types=["3"])["total"] == 0 and explorer.ads(types=["3", "2"])["total"] == 4
    assert [s["code"] for s in explorer.ad("2")["siblings"]] == ["3"]
    assert explorer.ad("4")["siblings"] == []  # not the other notice coded 333
    assert sorted(ad["code"] for ad in explorer.search("2")["ads"]) == ["2", "3"]  # by its tracking code

    # By what the list shows: the title, the company it is about, an issue that printed it, printings.
    with connect(explorer.db) as conn:
        conn.execute("UPDATE ads SET full_title = 'آگهي انحلال شركت دوم' WHERE detail_code = '4'")
        conn.execute("UPDATE ads SET newspaper_number = '21871' WHERE detail_code = '3'")
    codes = lambda **filters: [ad["code"] for ad in explorer.ads(**filters)["rows"]]
    assert codes(title="انحلال شرکت") == ["4"]  # the gazette's Arabic letters
    assert codes(company="10200000002") == codes(company="دوم") == ["5", "4"] and len(codes(company="1020000000")) == 4
    assert codes(issue="21871") == ["2"]  # its second printing's issue
    assert codes(issues_min="2") == ["2"] and codes(issues_max="1") == ["1", "5", "4"]


def test_notices_record_events_capital_and_stakes(tmp_path):
    raised = ("سرمایه شرکت از مبلغ 1.000.000 ریال به مبلغ 5.000.000 ریال افزایش یافت و آقای علی نمونه به کد ملی"
              " 0061555746 با پرداخت مبلغ 4.000.000 ریال به صندوق شرکت در زمره شرکا قرار گرفت.")
    explorer = make_explorer(tmp_path, [("1", "1", "1402/01/01", "10200000001", raised),
                                        ("2", "2", "1402/02/01", "10200000001", BOARD)])
    [ad] = explorer.ads(events=["capital_up"])["rows"]
    assert (ad["code"], ad["events"], ad["capital_from"], ad["capital_to"]) == ("1", ["capital_up"], 1000000, 5000000)
    assert [(m["key"], m["stake"]) for m in ad["named"]] == [("p:0061555746", 4000000)]
    assert {g["value"]: g["n"] for g in explorer.ads(group="event")["groups"]} == {"capital_up": 1, "board": 1}
    reader = explorer.ad("1")
    assert (reader["ad"]["events"], reader["ad"]["capital_to"]) == (["capital_up"], 5000000)
    assert [m["stake"] for m in reader["mentions"] if m["kind"] == "person"] == [4000000]
    assert explorer.entity("p:0061555746")["ads"][0]["stake"] == 4000000
    assert [ad["code"] for ad in explorer.ads(named="p:0061555746")["rows"]] == ["1"]  # the notices naming him
    assert explorer.ads(named="c:10200000001")["total"] == 2 and explorer.ads(named="x:1")["total"] == 0

    codes = lambda **filters: [ad["code"] for ad in explorer.ads(**filters)["rows"]]
    assert codes(events=["capital_up", "board"]) == ["2", "1"] and codes(events=["capital_up", "board"], every=True) == []
    assert codes(role="ceo") == codes(who="اصغر") == codes(who="اصغر", role="ceo") == ["2"]
    assert codes(who="اصغر", role="vice_chair") == []  # another one there is
    assert codes(who="0061555746") == codes(capital_min="5000000") == ["1"] and codes(capital_max="4999999") == []
    assert codes(issues_min="2") == []
    assert {g["value"]: g["n"] for g in explorer.ads(group="role")["groups"]} == {
        "chair": 1, "ceo": 1, "vice_chair": 1, "partner": 1}
    assert [n["key"] for n in explorer.nodes("company", {"capital_min": "5000000"})["rows"]] == ["c:10200000001"]
    assert explorer.nodes("company", {"events": "board", "capital_min": "5000000"})["total"] == 1  # by two notices


def test_leads_for_a_period_and_groups(tmp_path):
    both = " مريم احمدي به شماره ملي 2222222223 به سمت رئيس هيئت مديره انتخاب شد."
    third = SECOND.replace("دوم", "سوم").replace("10200000002", "10200000003")
    explorer = make_explorer(tmp_path, [
        ("1", "1", "1395/01/01", "10200000001", CHANGES),
        ("2", "2", "1402/02/01", "10200000002", SECOND + both),
        ("3", "3", "1402/03/01", "10200000003", third + both)])
    hubs = lambda **period: sorted((h["key"], h["companies"]) for h in explorer.leads(**period)["hubs"])
    assert hubs() == [("p:1111111112", 3), ("p:2222222223", 3)]
    assert hubs(since="1400") == [("p:1111111112", 2), ("p:2222222223", 2)]
    assert hubs(until="1399") == [] and explorer.leads(since="1400")["clusters"] == []
    assert explorer.leads(since="1400") is explorer.leads(since="1400")  # kept till the next build
    [group] = explorer.leads()["groups"]
    assert (group["size"], group["companies"], len(group["keys"])) == (2, 3, 5)
    assert explorer.leads(since="1400")["groups"][0]["companies"] == 2


def test_lists_of_people_companies_and_links(tmp_path):
    both = " مريم احمدي به شماره ملي 2222222223 به سمت رئيس هيئت مديره انتخاب شد."
    third = SECOND.replace("دوم", "سوم").replace("10200000002", "10200000003")
    explorer = make_explorer(tmp_path, [
        ("1", "1", "1395/01/01", "10200000001", CHANGES),  # two board members and two inspectors
        ("2", "2", "1402/02/01", "10200000002", SECOND + both),
        ("3", "3", "1402/03/01", "10200000003", third + both)])
    keys = lambda found: [row["key"] for row in found["rows"]]
    counts = lambda found: {g["value"]: g["n"] for g in found["groups"]}
    people = explorer.nodes("person", {})
    assert people["total"] == 4 and {n["key"] for n in people["rows"][:2]} == {"p:1111111112", "p:2222222223"}
    rezaei = people["rows"][0] if people["rows"][0]["key"] == "p:1111111112" else people["rows"][1]
    assert (rezaei["links"], rezaei["current"], rezaei["first"], rezaei["last"], rezaei["roles"]) == (
        3, 3, "1395/01/01", "1402/03/01", ["board", "ceo"])
    assert [n["name"] for n in explorer.nodes("person", {}, sort="name", desc=False)["rows"]] == [
        "حسن کریمی", "سارا نوری", "علی رضایی", "مریم احمدی"]
    assert keys(explorer.nodes("person", {"role": "inspector"})) == ["p:3333333334"]  # not the alternate one
    assert keys(explorer.nodes("person", {"q": "رضايي"})) == keys(explorer.nodes("person", {"q": "11111"})) == [
        "p:1111111112"]
    assert explorer.nodes("person", {"last_from": "1400"})["total"] == 2
    assert explorer.nodes("person", {"first_from": "1400"})["total"] == explorer.nodes("person", {"code": "no"})["total"] == 0
    assert counts(explorer.nodes("person", {}, group="role")) == {
        "board": 2, "ceo": 1, "chair": 1, "inspector": 1, "alternate_inspector": 1}
    assert counts(explorer.nodes("person", {}, group="first")) == {"1395": 4}
    total = lambda found: found["total"]
    assert total(explorer.nodes("person", {"active_from": "1396", "active_to": "1399"})) == 2  # named before and after
    assert total(explorer.nodes("person", {"active_to": "1395"})) == 4
    assert total(explorer.nodes("person", {"max": "1"})) == total(explorer.nodes("person", {"notices_min": "2"})) == 2
    assert [total(explorer.nodes("person", {"status": s})) for s in ("current", "ended", "gone")] == [4, 0, 0]
    assert keys(explorer.nodes("company", {"events": "inspector,signatory"})) == ["c:10200000001"]
    assert total(explorer.nodes("company", {"events": "board", "office": "تهران"})) == 0
    assert keys(explorer.nodes("company", {}))[0] == "c:10200000001"  # four people
    with pytest.raises(ValueError):
        explorer.nodes("ship", {})

    links = explorer.links({})
    assert links["total"] == 8 and links["rows"][0]["ads"] == 1
    ceo = explorer.links({"role": "ceo"})
    assert {(r["a"]["key"], r["b"]["key"]) for r in ceo["rows"]} == {("p:1111111112", "c:10200000002"),
                                                                     ("p:1111111112", "c:10200000003")}
    [karimi] = explorer.links({"q": "کريمي"})["rows"]
    assert (karimi["a"]["name"], karimi["b"]["key"], karimi["roles"], karimi["service"]) == (
        "حسن کریمی", "c:10200000001", ["inspector"], True)
    assert explorer.links({"service": "0"})["total"] == 6 and explorer.links({"first_from": "1400"})["total"] == 4
    assert explorer.links({"status": "ended"})["total"] == 0 and explorer.links({"kind": "company"})["total"] == 0
    assert counts(explorer.links({}, group="role")) == {
        "board": 2, "ceo": 2, "chair": 2, "inspector": 1, "alternate_inspector": 1}
    assert counts(explorer.links({}, group="kind")) == {"person": 8}
    assert explorer.links({}, sort="first", desc=False)["rows"][0]["first"] == "1395/01/01"
    assert total(explorer.links({"q": "رضايي", "q2": "10200000002"})) == 1  # one end each
    assert total(explorer.links({"q2": "رضايي"})) == 3 and total(explorer.links({"q": "رضايي", "q2": "احمدي"})) == 0
    assert total(explorer.links({"active_from": "1396", "active_to": "1399"})) == total(explorer.links({"years": "1"})) == 0
    assert total(explorer.links({"max": "1"})) == 8 and total(explorer.links({"min": "2"})) == 0

    assert explorer.leads(q="کريمي") == {name: [] for name in explorer.leads()}  # in no lead: one company
    found = explorer.leads(q="احمدي")
    assert [(h["key"], h["matched"][0]["key"]) for h in found["hubs"]] == [("p:2222222223", "p:2222222223")]
    assert found["hubs"][0]["rank"] in (1, 2) and len(found["groups"]) == 1
    [group] = explorer.leads(q="10200000002")["groups"]  # a company the group shares
    assert (group["rank"], [n["key"] for n in group["matched"]]) == (1, ["c:10200000002"])


def test_a_notice_the_listing_gives_no_company_is_about_the_one_its_text_names(tmp_path):
    explorer = make_explorer(tmp_path, [("1", "1", "1402/01/01", "-", SECOND)])
    [ad] = explorer.ads(company="دوم")["rows"]
    assert (ad["company_national_id"], ad["subject"]) == ("-", "10200000002") and "دوم" in ad["subject_name"]
    assert explorer.ads(company="10200000002")["total"] == 1


def test_the_notices_api_takes_every_filter(explorer):
    ads = lambda **q: server.ROUTES[("GET", "/api/ads")](SimpleNamespace(explorer=explorer), q, None)
    assert [ad["code"] for ad in ads(events="board,inspector", every="1", who="رضايي", role="board")["rows"]] == ["1"]
    assert ads(events="board,signatory", every="1")["total"] == 0 and ads(capital_min="1", issues_min="1")["total"] == 0


def test_text_search_ignores_arabic_letters(explorer):
    found = explorer.search_text("تغییرات شرکت دوم")
    assert (found["total"], [ad["code"] for ad in found["ads"]]) == (1, ["2"])
    assert explorer.ads(text="بازرس")["total"] == 1
    assert [ad["code"] for ad in explorer.search("2")["ads"]] == ["2"]  # its tracking code
    assert explorer.search("2", ads=False)["ads"] == []


def test_the_run_form_sees_the_texts_each_type_misses(explorer, tmp_path):
    repo = AdRepository(connect(tmp_path / "rrk.db"))
    repo.save_ad_types([("2", "تغییر")], "t")
    repo.conn.execute("INSERT INTO ads (tracking_code, title, letter_date, detail_code, ad_type, first_seen, last_seen)"
                      " VALUES ('3', 'آگهی', '1402/03/01', '3', '2', 't', 't')")
    repo.conn.commit()
    [two] = explorer.types()
    assert (two["ads"], two["missing"]) == (3, None)  # not counted until the overview is
    explorer.overview()
    assert explorer.types()[0]["missing"] == 1


def test_run_options_are_checked():
    assert server.check_args(["--type", "2,3", "--workers", "6", "--resume", "--missing-only"]) == [
        "--type", "2,3", "--workers", "6", "--resume", "--missing-only"]
    assert server.check_args(["--new-only"]) == ["--new-only"]
    assert server.check_args(["--years", "1395-1405", "--resume"]) == ["--years", "1395-1405", "--resume"]
    assert server.check_args(["--page-delay", "0.5"]) == ["--page-delay", "0.5"]
    for bad in (["--page-size", "500"], ["--workers", "0"], ["--type", "2; ls"], ["--debug", "--debug"],
                ["--years", "1395"], ["--years", "1395-1405;ls"], ["--page-delay", "-1"],
                ["--timeout"], "--debug", ["--no-tcp-keepalive"], ["--human-pacing"]):
        with pytest.raises(ValueError):
            server.check_args(bad)


def wait_for(runs, run_id, *states):
    for _ in range(100):
        if (run := runs.get(run_id, 0))["state"] in states:
            return run
        sleep(0.05)
    raise AssertionError(runs.get(run_id, 0))


def test_runs_keep_their_log_and_exit_code(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("import sys, time\nprint('args', *sys.argv[1:])\ntime.sleep(float(sys.argv[2]))\n")
    monkeypatch.setattr(server, "SCRAPER", tmp_path)
    monkeypatch.setattr(server, "RUNS", tmp_path / "runs")
    runs = server.Runs(tmp_path / "rrk.db")
    connect(tmp_path / "rrk.db").close()

    first = runs.start(["--timeout", "0"])
    with pytest.raises(RuntimeError):  # one at a time
        runs.start(["--timeout", "0"])
    done = wait_for(runs, first["id"], "finished", "failed")
    assert (done["state"], done["exit"], done["listed"]) == ("finished", 0, 0)
    assert done["log"].startswith("args --timeout 0 --db ")

    sleep(1)  # run ids are per second
    slow = runs.start(["--timeout", "30"])
    wait_for(runs, slow["id"], "running")
    runs.stop(slow["id"])
    assert wait_for(runs, slow["id"], "stopped", "failed")["state"] == "stopped"

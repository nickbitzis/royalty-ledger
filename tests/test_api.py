"""API end to end on a fresh copy of the small data set."""

import shutil

import pytest
from fastapi.testclient import TestClient

from royaltyledger.api import create_app


@pytest.fixture(scope="module")
def client(small_data, tmp_path_factory):
    d = tmp_path_factory.mktemp("api") / "data"
    shutil.copytree(small_data, d)
    with TestClient(create_app(d)) as c:
        yield c


def test_healthz_and_metrics(client):
    assert client.get("/healthz").json()["status"] == "ok"
    m = client.get("/metrics").text
    assert "rl_match_rate" in m and 'rl_period_difference_cents{period="2026Q2"} 0' in m
    assert "rl_http_requests_total" in m


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Royalty Ledger" in r.text


def test_summary_and_payees(client):
    s = client.get("/api/summary").json()
    assert all(p["ties_out"] for p in s["periods"])
    payees = client.get("/api/payees", params={"type": "artist"}).json()["payees"]
    assert payees and all(p["type"] == "artist" for p in payees)


def test_statement_json_csv_html(client):
    pid = client.get("/api/payees", params={"type": "artist"}).json()["payees"][0]["id"]
    st = client.get(f"/api/statements/{pid}", params={"period": "2026Q1"}).json()
    assert st["payee_id"] == pid and st["period"] == "2026Q1"
    r = client.get(f"/api/statements/{pid}", params={"period": "2026Q1", "format": "csv"})
    assert r.headers["content-type"].startswith("text/csv") and "opening_balance" in r.text
    r = client.get(f"/api/statements/{pid}", params={"period": "2026Q1", "format": "html"})
    assert "Closing balance" in r.text
    assert client.get(f"/api/statements/{pid}", params={"period": "2019Q1"}).status_code == 404


def test_audit_endpoint(client):
    payees = client.get("/api/payees", params={"type": "artist"}).json()["payees"]
    for p in payees:
        st = client.get(f"/api/statements/{p['id']}", params={"period": "2026Q2"}).json()
        lines = [e for a in st["accounts"] for e in a["earnings"]]
        if lines:
            au = client.get("/api/audit", params={"line": lines[0]["line_id"]}).json()
            assert au["contributions_micros_total"] == lines[0]["micros"]
            assert au["contributions"][0]["raw"]
            break
    assert client.get("/api/audit", params={"line": "2026Q2|nope|x|y|z"}).status_code == 404


def test_recoupment_and_analytics(client):
    accts = client.get("/api/recoupment").json()["accounts"]
    one = client.get(f"/api/recoupment/{accts[0]['account_id']}").json()
    assert one["series"] and one["account_id"] == accts[0]["account_id"]
    assert client.get("/api/recoupment/NOPE").status_code == 404
    an = client.get("/api/analytics").json()
    assert an["top_tracks"] and an["per_stream"] and an["recoup_projection"]


def test_review_accept_moves_money_out_of_suspense(client):
    review = client.get("/api/review").json()
    assert review["items"], "the small data set should have something to review"
    item = review["items"][0]
    before = next(p for p in client.get("/api/summary").json()["periods"] if p["period"] == "2026Q2")
    r = client.post(
        f"/api/review/{item['key_id']}", json={"action": "accept", "target": item["candidates"][0]["target"]}
    )
    assert r.status_code == 200
    after = client.get("/api/summary").json()
    assert all(p["ties_out"] for p in after["periods"])
    assert item["key_id"] not in {i["key_id"] for i in client.get("/api/review").json()["items"]}
    q2 = next(p for p in after["periods"] if p["period"] == "2026Q2")
    assert q2["by_kind"].get("suspense", 0) <= before["by_kind"].get("suspense", 0)


def test_review_validation(client):
    item = client.get("/api/review").json()["items"]
    if item:
        r = client.post(f"/api/review/{item[0]['key_id']}", json={"action": "accept", "target": "T99999"})
        assert r.status_code == 422
    assert client.post("/api/review/nope", json={"action": "reject"}).status_code == 404


def test_ingest_new_report(client):
    body = (
        "Reporting Month,ISRC,Track Title,Artist Name,Country,Streams,Net Revenue,Currency\n"
        "2026-06,ZZOTH2699999,Unknown Song,Nobody Known,US,1000,4.00000000,USD\n"
    )
    before = client.get("/api/summary").json()["lines"]
    r = client.post(
        "/api/ingest", params={"name": "wavelet_2026-06_late.csv"}, content=body, headers={"content-type": "text/csv"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == {"unmatched": 1}
    s = client.get("/api/summary").json()
    assert s["lines"] == before + 1 and all(p["ties_out"] for p in s["periods"])
    dup = client.post("/api/ingest", params={"name": "wavelet_2026-06_late.csv"}, content=body)
    assert dup.status_code == 422


def test_ingest_rejects_bad_report(client):
    r = client.post("/api/ingest", params={"name": "bad.csv"}, content="a,b\n1,2\n")
    assert r.status_code == 422
    assert client.post("/api/ingest", params={"name": "../evil.csv"}, content="x").status_code == 422

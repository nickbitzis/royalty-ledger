"""The CI gate passes on unchanged data and fails when a contract term changes a statement."""

import json
import shutil

from royaltyledger.cli import main


def test_gate_detects_a_changed_statement(small_data, tmp_path, capsys):
    d = tmp_path / "data"
    shutil.copytree(small_data, d)
    expected = tmp_path / "expected.json"
    args = ["--data", str(d), "gate", "--period", "2026Q1", "--expected", str(expected), "--seed", "3"]
    assert main([*args, "--update"]) == 0
    snap = json.loads(expected.read_text())
    assert snap["statements"] and snap["received_cents"] > 0

    cat = json.loads((d / "catalog.json").read_text())
    cat["contracts"][0]["rates_bps"]["stream"] += 100  # one point more for one artist (royalty deal)
    cat["contracts"][0]["profit_share_bps"] += 100  # or one point more of profit (net profit deal)
    (d / "catalog.json").write_text(json.dumps(cat))
    assert main(args) == 1
    out = capsys.readouterr().out
    assert f"statement changed for {cat['contracts'][0]['artist_id']}" in out

"""B3: example_shots.csv must match engine-expected CSV format."""

import csv
import os
import sys

EXAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "example_shots.csv",
)


def _read_rows():
    with open(EXAMPLE, encoding="utf-8-sig") as f:
        return [r for r in csv.reader(f) if any(c for c in r)]


def test_example_csv_has_id_header():
    rows = _read_rows()
    header = next(r for r in rows if r and r[0] == "ID")
    assert "prompt" in header
    assert "duration" in header
    assert "seed" in header
    assert "export_name" in header


def test_example_csv_has_at_least_one_shot():
    rows = _read_rows()
    id_idx = next(i for i, r in enumerate(rows) if r and r[0] == "ID")
    shot_rows = [r for r in rows[id_idx + 1:] if r and r[0]]
    assert len(shot_rows) >= 1
    shot = shot_rows[0]
    prompt = shot[1]
    assert "<Picture 1>" in prompt, \
        "example prompt must reference a material slot to demonstrate the tag feature"


def test_example_csv_parses_through_engine():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from batch_core import H3BatchEngine
    eng = H3BatchEngine(
        mode="storyboard",
        workflow_path="/tmp/_wf.json",
        csv_path=EXAMPLE,
        input_dir="/tmp",
        output_dir="/tmp",
        server_input_dir="/tmp",
        port=8188,
    )
    raw = eng._parse_rows()
    shots = eng._parse_shots(raw)
    assert len(shots) >= 1
    s = shots[0]
    assert s["ID"]
    assert s["prompt"]
    assert "<Picture 1>" in s["prompt"]
"""B2: MiniMax H3 node preflight check."""

import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch_core import H3BatchEngine, ShotError


REQUIRED = {
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3ImageToVideo",
    "MiniMaxH3TurboLoRA",
    "MiniMaxH3MultiRateSamplerEXPT8",
}


def _make_engine():
    return H3BatchEngine(
        mode="storyboard",
        workflow_path="/tmp/_wf.json",
        csv_path="/tmp/_fake.csv",
        input_dir="/tmp",
        output_dir="/tmp",
        server_input_dir="/tmp",
        port=8188,
    )


def test_check_passes_when_all_h3_nodes_present():
    eng = _make_engine()
    with mock.patch.object(eng, "_get", return_value={k: {} for k in REQUIRED}):
        eng._check_h3_nodes()  # must not raise


def test_check_raises_with_missing_list_when_nodes_absent():
    eng = _make_engine()
    only_one = {"MiniMaxH3ReferenceToVideo": {}}
    with mock.patch.object(eng, "_get", return_value=only_one):
        try:
            eng._check_h3_nodes()
        except ShotError as e:
            msg = str(e)
            assert "MiniMaxH3ImageToVideo" in msg
            assert "MiniMaxH3TurboLoRA" in msg
            assert "MiniMaxH3MultiRateSamplerEXPT8" in msg
        else:
            raise AssertionError("expected ShotError")


def test_check_raises_clear_error_when_object_info_unreachable():
    eng = _make_engine()
    with mock.patch.object(eng, "_get", side_effect=Exception("connection refused")):
        try:
            eng._check_h3_nodes()
        except ShotError as e:
            assert "MiniMax H3" in str(e) or "ComfyUI" in str(e)
        else:
            raise AssertionError("expected ShotError")

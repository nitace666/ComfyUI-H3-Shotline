"""V1.2 回移植修复的回归测试: C1 拼接模板 / C2 种子注入 / C3+C4 产物收集 / I1 端口崩溃 / I2 时长解析."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import batch_core
from batch_core import H3BatchEngine, ShotError


def _make_engine(tmp_path: Path, wf_name="my_workflow.json") -> H3BatchEngine:
    (tmp_path / wf_name).write_text("{}", encoding="utf-8")
    return H3BatchEngine(
        mode="concat",
        workflow_path=str(tmp_path / wf_name),
        csv_path=str(tmp_path / "shots.csv"),
        input_dir=str(tmp_path),
        output_dir=str(tmp_path / "output"),
        server_input_dir=str(tmp_path),
        port=8188,
    )


# ---------------- C1: 拼接模板 ----------------

def test_concat_workflow_prefers_sibling_template(tmp_path):
    (tmp_path / "h3_t2v_template.json").write_text("{}", encoding="utf-8")
    eng = _make_engine(tmp_path)
    wf = eng._concat_workflow("t2v")
    assert Path(wf).name == "h3_t2v_template.json"


def test_concat_workflow_i2v_prefers_fl2v_sibling(tmp_path):
    (tmp_path / "a_i2v.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b_fl2v.json").write_text("{}", encoding="utf-8")
    eng = _make_engine(tmp_path)
    assert Path(eng._concat_workflow("i2v")).name == "b_fl2v.json"


def test_concat_workflow_falls_back_to_user_workflow(tmp_path):
    # 没有兄弟模板时回落用户原工作流, 而不是空串
    eng = _make_engine(tmp_path)
    wf = eng._concat_workflow("t2v")
    assert wf == eng.workflow_path
    assert wf


# ---------------- C2: 种子注入 ----------------

def test_inject_seed_overrides_all_random_noise():
    eng = _make_engine(Path(os.environ.get("TEMP", ".")))
    prompt = {
        "1": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "x"}},
    }
    eng._inject_seed(prompt, 424242)
    assert prompt["1"]["inputs"]["noise_seed"] == 424242


# ---------------- C4: _collect_files 保留 subfolder ----------------

def test_collect_files_keeps_subfolder():
    eng = _make_engine(Path(os.environ.get("TEMP", ".")))
    item = {"outputs": {"9": {"gifs": [
        {"filename": "s01.mp4", "subfolder": "video/shots", "type": "output"},
    ]}}}
    assert eng._collect_files(item) == ["video/shots/s01.mp4"]


# ---------------- C3: _collect_output 候选解析并移动 ----------------

def test_collect_output_moves_file_to_run_dir(tmp_path):
    eng = _make_engine(tmp_path)
    out_dir = Path(eng.output_dir)
    (out_dir / "video" / "shots").mkdir(parents=True, exist_ok=True)
    (out_dir / "video" / "shots" / "s01.mp4").write_bytes(b"v")
    eng.run_dir = str(tmp_path / "run")
    os.makedirs(eng.run_dir)

    moved = eng._collect_output(["video/shots/s01.mp4"], "s01")
    assert moved == ["s01.mp4"]
    assert (Path(eng.run_dir) / "s01.mp4").exists()
    assert not (out_dir / "video" / "shots" / "s01.mp4").exists()


def test_collect_output_handles_missing_file(tmp_path):
    eng = _make_engine(tmp_path)
    eng.run_dir = str(tmp_path / "run")
    os.makedirs(eng.run_dir)
    moved = eng._collect_output(["video/shots/gone.mp4"], "s01")
    assert moved == ["gone.mp4"]  # 未找到也保留文件名, 记日志不崩溃


# ---------------- I2: 时长解析 ----------------

def test_invalid_duration_falls_back_to_five_seconds():
    eng = _make_engine(Path(os.environ.get("TEMP", ".")))
    prompt = {"3": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a*24", "values.a": 1.0}}}
    out = eng._apply_shot_common(prompt, {"duration": "3秒", "prompt": ""})
    assert out["3"]["inputs"]["values.a"] == 5.0


def test_valid_duration_still_applies():
    eng = _make_engine(Path(os.environ.get("TEMP", ".")))
    prompt = {"3": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a*24", "values.a": 5.0}}}
    out = eng._apply_shot_common(prompt, {"duration": "3.5", "prompt": ""})
    assert out["3"]["inputs"]["values.a"] == 3.5


# ---------------- I1: start() 端口探测不崩溃 ----------------

def test_start_returns_error_string_when_port_probe_fails(tmp_path):
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("h3_node_init_i1", os.path.join(here, "__init__.py"))
    node_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node_mod)

    wf = tmp_path / "wf.json"
    wf.write_text("{}", encoding="utf-8")
    csvp = tmp_path / "shots.csv"
    csvp.write_text("ID\n", encoding="utf-8")
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()

    with mock.patch.object(H3BatchEngine, "__init__",
                           side_effect=ShotError("未找到运行中的 ComfyUI")):
        result = node_mod.H3ShotBatchRenderer().start(
            mode="storyboard", workflow_path=str(wf), csv_path=str(csvp),
            input_dir=str(tmp_path / "in"), output_dir=str(tmp_path / "out"),
            server_input_dir=str(tmp_path / "in"),
            port=0, start_from=0, retries=0, skip_done=True,
        )
    assert isinstance(result, tuple)
    assert "ComfyUI" in result[0]


# ---------------- ffmpeg 智能回落 ----------------

def _stub_imageio(monkeypatch, tmp_path, fake_exe):
    fake = str(tmp_path / fake_exe)
    Path(fake).write_bytes(b"")
    stub = mock.MagicMock()
    stub.get_ffmpeg_exe.return_value = fake
    monkeypatch.setattr(batch_core.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", stub)
    return fake


def test_ffmpeg_exe_falls_back_to_imageio(tmp_path, monkeypatch):
    fake = _stub_imageio(monkeypatch, tmp_path, "bundled_ffmpeg.exe")
    assert batch_core._ffmpeg_exe() == fake


def test_extract_last_frame_uses_resolved_ffmpeg(tmp_path, monkeypatch):
    fake = _stub_imageio(monkeypatch, tmp_path, "bundled_ffmpeg.exe")
    eng = _make_engine(tmp_path)
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"v")

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"png")
        return mock.MagicMock(returncode=0, stderr=b"")

    with mock.patch.object(batch_core.subprocess, "run", side_effect=fake_run) as run:
        name = eng._extract_last_frame(str(video))
    assert name == "shot_tail.png"
    assert run.call_args.args[0][0] == fake


def test_concat_videos_uses_resolved_ffmpeg(tmp_path, monkeypatch):
    fake = _stub_imageio(monkeypatch, tmp_path, "bundled_ffmpeg.exe")
    eng = _make_engine(tmp_path)
    os.makedirs(eng.output_dir, exist_ok=True)
    part = tmp_path / "shot.mp4"
    part.write_bytes(b"v")
    out_mp4 = tmp_path / "out.mp4"

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"mp4")
        return mock.MagicMock(returncode=0, stderr=b"")

    with mock.patch.object(batch_core.subprocess, "run", side_effect=fake_run) as run:
        assert eng._concat_videos([str(part)], str(out_mp4))
    assert run.call_args.args[0][0] == fake

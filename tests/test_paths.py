"""B1: path fallback to ComfyUI folder_paths."""

import importlib
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_engine_accepts_empty_defaults():
    from batch_core import H3BatchEngine
    eng = H3BatchEngine(
        mode="storyboard",
        workflow_path="",
        csv_path="",
        input_dir="",
        output_dir="",
        server_input_dir="",
    )
    assert eng.workflow_path == ""
    assert eng.input_dir == ""
    assert eng.output_dir == ""


def test_init_populates_from_folder_paths_when_empty(tmp_csv_files, folder_paths_mock):
    wf_path, csv_path = tmp_csv_files

    fake_input = tempfile.mkdtemp()
    fake_output = tempfile.mkdtemp()

    folder_paths_mock.get_input_directory.return_value = fake_input
    folder_paths_mock.get_output_directory.return_value = fake_output

    # `V1.0/__init__.py` was already imported by pytest during collection
    # (it lives next to its package `__init__.py`, which makes pytest
    # treat `V1.0/` as a package). At that point the conftest mock still
    # returned its initial empty-string defaults, so the module-level
    # `_DEFAULT_INPUT_DIR` / `_DEFAULT_OUTPUT_DIR` constants were
    # bound to "". Reloading re-runs the module-level code with our
    # per-test return values in place.
    # `__init__.py` 在 pytest 收集期已被导入,但其模块对象名随目录名变化,
    # 不能依赖 sys.modules['__init__'] —— 按文件路径重载,让模块级常量
    # 用本测试设置的 folder_paths 返回值重新求值。
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("h3_node_init", os.path.join(here, "__init__.py"))
    node_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node_mod)
    H3ShotBatchRenderer = node_mod.H3ShotBatchRenderer

    from batch_core import H3BatchEngine

    captured = {}

    def capture_init(self, mode, workflow_path, csv_path, input_dir, output_dir, **kw):
        captured.update(workflow_path=workflow_path, input_dir=input_dir, output_dir=output_dir)
        raise SystemExit("captured")

    with mock.patch.object(H3BatchEngine, "__init__", capture_init):
        try:
            H3ShotBatchRenderer().start(
                mode="storyboard",
                workflow_path=wf_path,
                csv_path=csv_path,
                input_dir="",
                output_dir="",
                server_input_dir="",
                port=0, start_from=0, retries=0, skip_done=True,
            )
        except SystemExit as e:
            assert str(e) == "captured"

    assert captured["input_dir"] == fake_input
    assert captured["output_dir"] == fake_output
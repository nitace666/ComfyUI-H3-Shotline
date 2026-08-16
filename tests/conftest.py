"""Test fixtures for V1.0 tests.

`comfy_aimdo` isn't installed in this environment, so `V1.0/__init__.py`'s
`try: import comfy_aimdo.folder_paths ... except Exception: _DEFAULT_INPUT_DIR = ""`
silently falls back to empty strings. To exercise the real fallback path we
patch `sys.modules['comfy_aimdo.folder_paths']` with a MagicMock.

Pytest imports `V1.0/__init__.py` during test collection (because it treats
`V1.0/` as a package via its `__init__.py`), so the module-level
`_DEFAULT_INPUT_DIR` / `_DEFAULT_OUTPUT_DIR` constants are evaluated before
any per-test fixture runs. The fixtures below let each test re-configure the
mock's return values and then reload `__init__` to re-evaluate the constants
with the new values — see `folder_paths_mock` for details.
"""

import os
import shutil
import sys
import tempfile
from unittest import mock


# Install the mock at conftest-import time so that `import comfy_aimdo.folder_paths`
# inside the production code resolves to our mock rather than raising ImportError.
_FP_PARENT = mock.MagicMock(name="comfy_aimdo")
_FP_MODULE = mock.MagicMock(name="comfy_aimdo.folder_paths")
_FP_MODULE.get_input_directory.return_value = ""
_FP_MODULE.get_output_directory.return_value = ""
_FP_PARENT.folder_paths = _FP_MODULE
sys.modules["comfy_aimdo"] = _FP_PARENT
sys.modules["comfy_aimdo.folder_paths"] = _FP_MODULE


import pytest  # noqa: E402  (must come after sys.modules patching)


@pytest.fixture
def folder_paths_mock():
    """Handle on the `comfy_aimdo.folder_paths` mock.

    Resets call history and return values before each test. The caller
    sets the desired return values, then reloads `__init__` so the
    production code's module-level `_DEFAULT_INPUT_DIR` /
    `_DEFAULT_OUTPUT_DIR` constants pick up the new values:

        def test_foo(folder_paths_mock):
            folder_paths_mock.get_input_directory.return_value = "/x"
            folder_paths_mock.get_output_directory.return_value = "/y"
            import importlib
            importlib.reload(sys.modules['__init__'])

    Why reload is necessary: `V1.0/__init__.py` defines
    `_DEFAULT_INPUT_DIR = _folder_paths.get_input_directory()` at module
    scope. Pytest imports `V1.0/__init__.py` during collection (because
    of `V1.0/__init__.py`), which happens before this fixture runs and
    with the mock still returning its initial empty-string values. The
    reload re-runs the module-level code with the per-test return
    values now in place.
    """
    _FP_MODULE.get_input_directory.return_value = ""
    _FP_MODULE.get_output_directory.return_value = ""
    _FP_MODULE.reset_mock()
    return _FP_MODULE


@pytest.fixture
def tmp_csv_files():
    """Creates a temp workflow_path and csv_path, yields (workflow, csv).

    Cleans up the temp directory after the test.
    """
    td = tempfile.mkdtemp()
    wf = os.path.join(td, "wf.json")
    csv = os.path.join(td, "shots.csv")
    open(wf, "a").close()
    open(csv, "a").close()
    try:
        yield wf, csv
    finally:
        shutil.rmtree(td, ignore_errors=True)
import os
import threading

from batch_core import H3BatchEngine, ShotError

try:
    import comfy_aimdo.folder_paths as _folder_paths
    _DEFAULT_INPUT_DIR = _folder_paths.get_input_directory()
    _DEFAULT_OUTPUT_DIR = _folder_paths.get_output_directory()
except Exception:
    _DEFAULT_INPUT_DIR = ""
    _DEFAULT_OUTPUT_DIR = ""

_active_lock = threading.Lock()
_active = None


class H3ShotBatchRenderer:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["storyboard", "concat"], {"default": "storyboard"}),
                "workflow_path": ("STRING", {"default": ""}),
                "csv_path": ("STRING", {"default": ""}),
                "input_dir": ("STRING", {"default": _DEFAULT_INPUT_DIR}),
                "output_dir": ("STRING", {"default": _DEFAULT_OUTPUT_DIR}),
                "server_input_dir": ("STRING", {"default": _DEFAULT_INPUT_DIR}),
                "port": ("INT", {"default": 0, "min": 0, "max": 65535, "tooltip": "0 = auto-detect (8188-8200); set a port to pin it"}),
                "start_from": ("INT", {"default": 0, "min": 0}),
                "retries": ("INT", {"default": 2, "min": 0, "max": 10}),
                "skip_done": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("info",)
    FUNCTION = "start"
    CATEGORY = "H3/Batch"
    OUTPUT_NODE = True

    def start(self, mode, workflow_path, csv_path, input_dir, output_dir, server_input_dir, port,
              start_from, retries, skip_done):
        global _active
        if not input_dir:
            input_dir = _DEFAULT_INPUT_DIR
        if not output_dir:
            output_dir = _DEFAULT_OUTPUT_DIR
        if not server_input_dir:
            server_input_dir = _DEFAULT_INPUT_DIR
        if not os.path.exists(csv_path):
            return ("CSV does not exist: %s" % csv_path,)
        if not os.path.exists(workflow_path):
            return ("workflow does not exist: %s" % workflow_path,)
        if not os.path.isdir(input_dir):
            return ("input directory does not exist: %s" % input_dir,)
        if not os.path.isdir(server_input_dir):
            return ("server input directory does not exist: %s" % server_input_dir,)
        try:
            import urllib.request, json
            with urllib.request.urlopen("http://127.0.0.1:%d/object_info" % (port or 8188), timeout=2) as r:
                known = set(json.loads(r.read().decode()).keys())
            missing = [n for n in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo",
                                   "MiniMaxH3TurboLoRA", "MiniMaxH3MultiRateSamplerEXPT8")
                       if n not in known]
            if missing:
                return ("MiniMax H3 custom nodes not installed; missing: %s\n"
                        "install the MiniMax H3 nodes first" % ", ".join(missing),)
        except Exception:
            pass  # engine.run() will do a stricter check
        with _active_lock:
            if _active is not None and _active.is_alive():
                return ("a batch task is already running; wait or restart ComfyUI",)
            try:
                engine = H3BatchEngine(
                    mode, workflow_path, csv_path, input_dir, output_dir,
                    port=port, start_from=start_from, retries=retries, skip_done=skip_done,
                    server_input_dir=server_input_dir,
                )
            except ShotError as e:
                return (str(e),)
            _active = threading.Thread(target=engine.run, daemon=True)
            _active.start()
        return ("batch started [%s]: %s\nprogress: [H3Batch] console log" % (mode, csv_path),)


NODE_CLASS_MAPPINGS = {
    "H3ShotBatchRenderer": H3ShotBatchRenderer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3ShotBatchRenderer": "H3 Shot Batch Renderer",
}

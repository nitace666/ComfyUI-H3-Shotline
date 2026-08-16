import csv
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

DEFAULT_INPUT_DIR = ""

TAG_RE = re.compile(r"<(?P<kind>Picture|Video|Audio)\s*(?P<idx>\d+)>", re.IGNORECASE)


def _ffmpeg_exe():
    """PATH 里的 ffmpeg 优先; 没有则回落 ComfyUI 自带的 imageio-ffmpeg。"""
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # 让 subprocess 报出可读的 FileNotFoundError


ASPECT_OPTIONS = ["1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)", "3:4 (Portrait Standard)",
                  "4:3 (Standard)", "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)"]
ASPECT_MAP = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}


class ShotError(Exception):
    pass


class H3BatchEngine:
    def __init__(self, mode, workflow_path, csv_path, input_dir, output_dir, port=0,
                 start_from=0, retries=2, skip_done=True, client_id="h3-batch",
                 server_input_dir=DEFAULT_INPUT_DIR):
        self.mode = mode
        self.workflow_path = os.path.abspath(workflow_path) if workflow_path else workflow_path
        self.csv_path = os.path.abspath(csv_path) if csv_path else csv_path
        self.input_dir = os.path.abspath(input_dir) if input_dir else input_dir
        self.output_dir = os.path.abspath(output_dir) if output_dir else output_dir
        self.server_input_dir = os.path.abspath(server_input_dir) if server_input_dir else server_input_dir
        self.port = port or self._detect_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.start_from = start_from
        self.retries = retries
        self.skip_done = skip_done
        self.client_id = client_id
        stem = os.path.splitext(os.path.basename(csv_path))[0]
        self.manifest_path = os.path.join(self.output_dir, "h3_batch_manifest_%s.json" % stem)

    @staticmethod
    def _detect_port():
        for p in range(8188, 8201):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/system_stats" % p, timeout=1) as r:
                    if r.status == 200:
                        return p
            except Exception:
                continue
        raise ShotError("ComfyUI not found (no response on 8188-8200); set port explicitly")

    _REQUIRED_H3_NODES = (
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3TurboLoRA",
        "MiniMaxH3MultiRateSamplerEXPT8",
    )

    def _check_h3_nodes(self):
        try:
            known = set(self._get("/object_info").keys())
        except Exception as e:
            raise ShotError("cannot reach ComfyUI (%s): is ComfyUI running?" % e)
        missing = [n for n in self._REQUIRED_H3_NODES if n not in known]
        if missing:
            raise ShotError(
                "MiniMax H3 custom nodes missing (%d): %s\n"
                "install the MiniMax H3 nodes first (required dependency)\n"
                "install: https://github.com/MiniMaxAI/MiniMax-ComfyUI-H3"
                % (len(missing), ", ".join(missing))
            )

    # ---------------- API ----------------
    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return json.loads(r.read().decode())

    def _post(self, path, payload):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    # ---------------- CSV 解析 ----------------
    def _parse_rows(self):
        raw = []
        with open(self.csv_path, encoding="utf-8-sig") as f:
            for row in csv.reader(f):
                row = [c.strip() if isinstance(c, str) else c for c in row]
                if any(c not in ("", None) for c in row):
                    raw.append(row)
        return raw

    def _parse_pool(self, raw):
        pool = {"Picture": {}, "Video": {}, "Audio": {}}
        for row in raw:
            key = row[0] if row else ""
            if key in pool:
                for i, cell in enumerate(row[1:], start=1):
                    if cell:
                        pool[key][i] = cell
        return pool

    def _parse_global(self, raw):
        g = {}
        for row in raw:
            if row and row[0] in ("aspect", "quality", "steps") and len(row) > 1 and row[1]:
                g[row[0]] = row[1]
        return g

    def _parse_shots(self, raw):
        shots = []
        idx = None
        for i, row in enumerate(raw):
            if row and row[0] == "ID":
                idx = i + 1
                break
        if idx is None:
            raise ShotError("cannot find shot header row (ID,prompt,...)")
        headers = raw[idx - 1]
        col = {h: j for j, h in enumerate(headers) if h}
        for row in raw[idx:]:
            if not row or not row[0]:
                continue
            shots.append({h: (row[j] if j < len(row) else "") for h, j in col.items()})
        return shots

    # ---------------- 模板 -> API ----------------
    def convert(self, seed):
        d = json.load(open(self.workflow_path, encoding="utf-8"))
        try:
            obj_info = self._get("/object_info")
        except Exception as e:
            raise ShotError("cannot reach ComfyUI server (%s): check port parameter (%s)" % (self.base, e))
        known = set(obj_info.keys())
        self._template = d
        self._links = {l[0]: l for l in d["links"]}
        self._nodes = {n["id"]: n for n in d["nodes"]}
        self._next_nid = max([n["id"] for n in d["nodes"]] + [l[0] for l in d["links"]] + [0]) + 1

        primitives = {}
        for n in d["nodes"]:
            if n["type"] in ("PrimitiveFloat", "PrimitiveInt", "PrimitiveString", "PrimitiveBoolean"):
                primitives[n["id"]] = n.get("widgets_values", [None])[0]

        prompt = {}
        for n in d["nodes"]:
            t = n["type"]
            if t.startswith("Primitive") or t not in known:
                continue
            inputs = {}
            wv = n.get("widgets_values", [])
            wi = 0
            for inp in n.get("inputs", []):
                name = inp["name"]
                lk = inp.get("link")
                if lk is not None:
                    src = self._links.get(lk)
                    if src is None:
                        continue
                    origin, oidx = src[1], src[2]
                    if origin in primitives:
                        inputs[name] = primitives[origin]
                    else:
                        inputs[name] = [str(origin), oidx]
                elif inp.get("widget"):
                    if wi < len(wv):
                        inputs[name] = wv[wi]
                    wi += 1
            if t == "RandomNoise":
                inputs["noise_seed"] = seed
            elif t == "VAELoader":
                inputs["vae_name"] = wv[0]
            elif t == "CLIPLoader":
                inputs["clip_name"] = wv[0]; inputs["type"] = wv[1]; inputs["device"] = wv[2]
            elif t == "UNETLoader":
                inputs["unet_name"] = wv[0]; inputs["weight_dtype"] = wv[1]
            elif t == "LoadImage":
                inputs["image"] = wv[0]
            elif t == "LoadVideo":
                inputs["file"] = wv[0]
            elif t == "ResolutionSelector":
                inputs["aspect_ratio"] = wv[0]; inputs["megapixels"] = wv[1]; inputs["multiple"] = wv[2]
            elif t == "ComfyMathExpression":
                if "expression" not in inputs:
                    inputs["expression"] = wv[0]
                if "values.a" not in inputs:
                    inputs["values.a"] = 5.0
            elif t == "MiniMaxH3TurboLoRA":
                inputs["lora_name"] = wv[0]; inputs["strength"] = wv[1]; inputs["low_vram"] = wv[2]
            elif t == "SaveVideo":
                inputs["filename_prefix"] = wv[0]; inputs["format"] = wv[1]; inputs["codec"] = wv[2]
            elif t == "CreateVideo":
                inputs["fps"] = wv[0]; inputs["bit_depth"] = wv[1]
            elif t == "BasicScheduler":
                if "scheduler" not in inputs:
                    inputs["scheduler"] = wv[0]; inputs["steps"] = wv[1]; inputs["denoise"] = wv[2]
            elif t == "KSamplerSelect":
                if "sampler_name" not in inputs:
                    inputs["sampler_name"] = wv[0]
            elif t == "MiniMaxH3ReferenceToVideo":
                inputs["ref_image_size"] = "match"
            elif t == "MiniMaxH3ImageToVideo":
                pass
            elif t == "ModelAttentionBackend":
                inputs["attention"] = wv[0]
            prompt[str(n["id"])] = {"class_type": t, "inputs": inputs}
        self._prompt = prompt
        return prompt

    def _fresh_id(self):
        nid = self._next_nid
        self._next_nid += 1
        return nid

    def _resolve_file(self, name):
        if not name:
            return None
        p = os.path.join(self.input_dir, name)
        if not os.path.exists(p):
            raise ShotError("asset not found: %s (in %s)" % (name, self.input_dir))
        # 服务器只认自己的 input 目录; 素材在别处就复制过去
        if os.path.abspath(self.input_dir) != self.server_input_dir:
            os.makedirs(self.server_input_dir, exist_ok=True)
            dst = os.path.join(self.server_input_dir, os.path.basename(name))
            if os.path.abspath(p) != os.path.abspath(dst):
                shutil.copy2(p, dst)
            return os.path.basename(name)
        return name

    def _apply_global(self, prompt, g):
        for node in prompt.values():
            t = node["class_type"]
            if t == "ResolutionSelector":
                if g.get("aspect"):
                    val = ASPECT_MAP.get(g["aspect"], g["aspect"])
                    if val not in ASPECT_OPTIONS:
                        m = re.match(r"^(\d+):0(\d)$", g["aspect"])
                        if m and (m.group(1) + ":" + m.group(2)) in ASPECT_MAP:
                            val = ASPECT_MAP[m.group(1) + ":" + m.group(2)]
                            print("[H3Batch] aspect '%s' looks like an Excel time, corrected to %s" % (g["aspect"], m.group(1) + ":" + m.group(2)), flush=True)
                    if val in ASPECT_OPTIONS:
                        node["inputs"]["aspect_ratio"] = val
                    else:
                        print("[H3Batch] aspect '%s' unrecognized, using template default" % g["aspect"], flush=True)
                if g.get("quality"):
                    try:
                        node["inputs"]["megapixels"] = float(g["quality"])
                    except ValueError:
                        print("[H3Batch] quality '%s' unrecognized, using template default" % g["quality"], flush=True)
            elif t == "MiniMaxH3MultiRateSamplerEXPT8":
                if g.get("steps"):
                    m = re.match(r"(\d+)\s*([VA])\s*(\d+)\s*([VA])", g["steps"], re.IGNORECASE)
                    if m:
                        v = int(m.group(1)) if m.group(2).upper() == "V" else int(m.group(3))
                        a = int(m.group(3)) if m.group(2).upper() == "V" else int(m.group(1))
                        node["inputs"]["video_steps"] = v
                        node["inputs"]["audio_steps"] = a
                    else:
                        print("[H3Batch] steps '%s' unrecognized, using template default" % g["steps"], flush=True)
        return prompt

    def _apply_shot_common(self, prompt, row):
        text = row.get("prompt")
        if text:
            ref_nid = self._ref_node_id(prompt)
            prompt[ref_nid]["inputs"]["prompt"] = text
        dur = row.get("duration")
        # 留空或非法值 = 5 秒
        seconds = 5.0
        if dur:
            try:
                seconds = float(dur)
            except ValueError:
                print("[H3Batch] duration '%s' invalid, falling back to 5s" % dur, flush=True)
        for node in prompt.values():
            for k, v in node["inputs"].items():
                if k == "values.a" and isinstance(v, (int, float)):
                    node["inputs"][k] = seconds
        if row.get("export_name"):
            for node in prompt.values():
                if node["class_type"] == "SaveVideo":
                    node["inputs"]["filename_prefix"] = "video/shots/" + row["export_name"]
        return prompt

    def _ref_node_id(self, prompt):
        for nid, node in prompt.items():
            if node["class_type"] in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"):
                return nid
        raise ShotError("template has no MiniMaxH3 conditioning node")

    # ---------------- 分镜模式 ----------------
    def apply_shot_pool(self, prompt, row, pool):
        prompt = json.loads(json.dumps(prompt))
        ref_nid = self._ref_node_id(prompt)
        ref_node = prompt[ref_nid]
        for key in list(ref_node["inputs"].keys()):
            if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
                del ref_node["inputs"][key]
        text = row.get("prompt") or ""
        used = set()
        for m in TAG_RE.finditer(text):
            kind = m.group("kind").lower()
            idx = int(m.group("idx"))
            used.add((kind, idx))
        for kind, idx in sorted(used):
            slot = pool[kind.title()].get(idx)
            if not slot:
                raise ShotError("shot references <%(k)s %(i)d> but the pool has no such asset" % {"k": kind.title(), "i": idx})
            fname = self._resolve_file(slot)
            if kind == "picture":
                nid = str(self._fresh_id())
                prompt[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
                ref_node["inputs"]["ref_images.ref_image_%d" % (idx - 1)] = [nid, 0]
            elif kind == "video":
                lv = str(self._fresh_id())
                gc = str(self._fresh_id())
                prompt[lv] = {"class_type": "LoadVideo", "inputs": {"file": fname}}
                prompt[gc] = {"class_type": "GetVideoComponents", "inputs": {"video": [lv, 0]}}
                ref_node["inputs"]["ref_videos.ref_video_%d" % (idx - 1)] = [gc, 0]
            elif kind == "audio":
                la = str(self._fresh_id())
                prompt[la] = {"class_type": "LoadAudio", "inputs": {"audio": fname}}
                ref_node["inputs"]["ref_audios.ref_audio_%d" % (idx - 1)] = [la, 0]
        return self._apply_shot_common(prompt, row)

    # ---------------- 拼接模式 ----------------
    def _detect_concat_mode(self, pool):
        pics = [v for v in pool["Picture"].values()]
        vids = [v for v in pool["Video"].values()]
        auds = [v for v in pool["Audio"].values()]
        if not pics and not vids and not auds:
            return "t2v"
        if len(pics) == 1 and not vids and not auds:
            return "i2v"
        return "ref2v"

    def _concat_workflow(self, cmode):
        """拼接模式模板不写死: 在用户所选工作流的同文件夹按模式名找兄弟 json,
        找不到回落用户原工作流。i2v 优先 fl2v(尾帧衔接), 其次 i2v。"""
        pats = {"t2v": ("t2v",), "i2v": ("fl2v", "i2v"), "ref2v": ("ref2v",)}[cmode]
        chosen = os.path.basename(self.workflow_path).lower()
        folder = os.path.dirname(self.workflow_path)
        try:
            files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".json"))
        except OSError:
            files = []
        for pat in pats:
            hits = [f for f in files if pat in f.lower()]
            if hits:
                if pat in chosen:
                    return self.workflow_path
                return os.path.join(folder, hits[0])
        return self.workflow_path

    def apply_shot_concat(self, prompt, row, pool, prev_frame):
        prompt = json.loads(json.dumps(prompt))
        ref_nid = self._ref_node_id(prompt)
        ref_node = prompt[ref_nid]
        cmode = self._detect_concat_mode(pool)
        # 清空模板自带参考/首尾帧槽
        for key in list(ref_node["inputs"].keys()):
            if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.",
                               "first_frame", "last_frame")):
                del ref_node["inputs"][key]
        if cmode == "i2v" or cmode == "t2v":
            if prev_frame:
                nid = str(self._fresh_id())
                prompt[nid] = {"class_type": "LoadImage", "inputs": {"image": prev_frame}}
                ref_node["inputs"]["first_frame"] = [nid, 0]
            elif cmode == "i2v":
                fname = self._resolve_file(list(pool["Picture"].values())[0])
                nid = str(self._fresh_id())
                prompt[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
                ref_node["inputs"]["first_frame"] = [nid, 0]
        elif cmode == "ref2v":
            for kind, slotkey, prefix in (("Picture", "Picture", "ref_images.ref_image_%d"),
                                          ("Video", "Video", "ref_videos.ref_video_%d"),
                                          ("Audio", "Audio", "ref_audios.ref_audio_%d")):
                for idx, fname in sorted(pool[slotkey].items()):
                    fname = self._resolve_file(fname)
                    if kind == "Picture":
                        nid = str(self._fresh_id())
                        prompt[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
                        ref_node["inputs"][prefix % (idx - 1)] = [nid, 0]
                    elif kind == "Video":
                        lv = str(self._fresh_id())
                        gc = str(self._fresh_id())
                        prompt[lv] = {"class_type": "LoadVideo", "inputs": {"file": fname}}
                        prompt[gc] = {"class_type": "GetVideoComponents", "inputs": {"video": [lv, 0]}}
                        ref_node["inputs"][prefix % (idx - 1)] = [gc, 0]
                    elif kind == "Audio":
                        la = str(self._fresh_id())
                        prompt[la] = {"class_type": "LoadAudio", "inputs": {"audio": fname}}
                        ref_node["inputs"][prefix % (idx - 1)] = [la, 0]
        return self._apply_shot_common(prompt, row)

    def _extract_last_frame(self, video_path):
        name = os.path.splitext(os.path.basename(video_path))[0] + "_tail.png"
        out = os.path.join(self.server_input_dir, name)
        cmd = [_ffmpeg_exe(), "-y", "-sseof", "-0.1", "-i", video_path,
               "-frames:v", "1", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("[H3Batch] tail-frame extract failed (%s): %s" % (video_path, e), flush=True)
            return None
        if r.returncode != 0 or not os.path.exists(out):
            print("[H3Batch] tail-frame extract failed (%s): %s"
                  % (video_path, r.stderr.decode("utf-8", "ignore")[-200:]), flush=True)
            return None
        return name

    def _concat_videos(self, parts, out_mp4):
        lst = os.path.join(self.output_dir, "_concat_list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                # concat demuxer 把 '\' 当转义, Windows 路径统一正斜杠
                p = p.replace("\\", "/")
                f.write("file '%s'\n" % p.replace("'", "'\\''"))
        cmd = [_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", lst,
               "-c", "copy", out_mp4]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as e:
            print("[H3Batch] concat failed (%s): %s" % (out_mp4, e), flush=True)
            return False
        if r.returncode != 0 or not os.path.exists(out_mp4):
            print("[H3Batch] concat failed (%s): %s"
                  % (out_mp4, r.stderr.decode("utf-8", "ignore")[-200:]), flush=True)
            return False
        return True

    # ---------------- 执行 ----------------
    def _prune_orphans(self, prompt):
        keep = set()
        for nid, node in prompt.items():
            if node.get("class_type") == "SaveVideo":
                keep.add(nid)
        changed = True
        while changed:
            new = set()
            for nid in keep:
                for v in prompt[nid]["inputs"].values():
                    if isinstance(v, list) and len(v) == 2:
                        new.add(str(v[0]))
            before = len(keep)
            keep |= new
            changed = len(keep) > before
        return {nid: node for nid, node in prompt.items() if nid in keep}

    def _load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                return json.load(open(self.manifest_path, encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_manifest(self, manifest):
        os.makedirs(self.output_dir, exist_ok=True)
        json.dump(manifest, open(self.manifest_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    def _wait_shot(self, pid, timeout=3600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(10)
            q = self._get("/queue")
            # 队列条目 (number, prompt_id, ...) → prompt_id 在 x[1] (ComfyUI server.py delete_queue_item 用 a[1])
            running = [str(x[1]) for x in q.get("queue_running", [])]
            pending = [str(x[1]) for x in q.get("queue_pending", [])]
            if pid in running or pid in pending:
                continue
            h = self._get("/history/" + pid)
            if pid in h:
                return h[pid].get("status", {}).get("status_str"), self._collect_files(h[pid])
        return "timeout", []

    def _collect_files(self, item):
        files = []
        for o in item.get("outputs", {}).values():
            for v in o.values():
                if isinstance(v, list):
                    for f in v:
                        if isinstance(f, dict) and f.get("filename"):
                            sub = f.get("subfolder", "")
                            files.append(sub + "/" + f["filename"] if sub else f["filename"])
        return files

    def _inject_seed(self, prompt, seed):
        for node in prompt.values():
            if node["class_type"] == "RandomNoise":
                node["inputs"]["noise_seed"] = seed

    def _parse_seed(self, raw):
        """把 CSV 种子列解析成整数种子。空/0/"0" → 0(随机); "1.0" 归一化为 1;
        非数字 → 0(随机), 不崩溃。"""
        if raw is None or raw == "":
            return 0
        try:
            val = int(float(str(raw).strip()))
        except (ValueError, TypeError):
            return 0
        return val if val else 0

    def run(self):
        self._check_h3_nodes()
        print("[H3Batch] mode=%s start: %s" % (self.mode, self.csv_path), flush=True)
        raw = self._parse_rows()
        pool = self._parse_pool(raw)
        g = self._parse_global(raw)
        shots = self._parse_shots(raw)
        manifest = self._load_manifest()
        # 每次运行独立时间戳文件夹, 防止同名覆盖
        self.run_dir = os.path.join(self.output_dir, time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(self.run_dir, exist_ok=True)
        print("[H3Batch] output folder: %s" % self.run_dir, flush=True)
        if self.mode == "concat":
            cmode = self._detect_concat_mode(pool)
            wf = self._concat_workflow(cmode)
            print("[H3Batch] concat-mode: %s, template: %s" % (cmode, os.path.basename(wf)), flush=True)
            self.workflow_path = wf
        self.convert(seed=0)
        self._prompt = self._apply_global(self._prompt, g)
        prev_frame = None
        concat_parts = []
        done = 0
        for i, row in enumerate(shots):
            sid = row.get("ID") or ("shot_%03d" % (i + 1))
            if i < self.start_from:
                continue
            if self.skip_done and manifest.get(sid, {}).get("status") == "success":
                print("[H3Batch] %s already done, skip" % sid, flush=True)
                if self.mode == "concat":
                    prev_frame = manifest.get(sid, {}).get("tail_frame") or prev_frame
                    vpath = self._video_path_old(manifest.get(sid, {}).get("files", []),
                                                 manifest.get(sid, {}).get("run_dir"))
                    if vpath:
                        concat_parts.append(vpath)
                continue
            seed = self._parse_seed(row.get("seed"))
            if not seed:
                seed = int(time.time() * 1000) % (2 ** 31)
            ok = False
            last_err = ""
            for attempt in range(self.retries + 1):
                try:
                    if self.mode == "concat":
                        prompt = self.apply_shot_concat(self._prompt, row, pool, prev_frame)
                    else:
                        prompt = self.apply_shot_pool(self._prompt, row, pool)
                    prompt = self._prune_orphans(prompt)
                    self._inject_seed(prompt, seed)
                    resp = self._post("/prompt", {"prompt": prompt, "client_id": self.client_id})
                    if "prompt_id" not in resp:
                        last_err = json.dumps(resp, ensure_ascii=False)[:300]
                        print("[H3Batch] %s validation failed (try %d): %s" % (sid, attempt + 1, last_err), flush=True)
                        continue
                    pid = resp["prompt_id"]
                    print("[H3Batch] %s submitted: %s" % (sid, pid), flush=True)
                    status, files = self._wait_shot(pid)
                    if status == "success":
                        moved = self._collect_output(files, sid)
                        manifest[sid] = {"status": "success", "files": moved, "prompt_id": pid, "seed": seed,
                                         "run_dir": self.run_dir}
                        ok = True
                        print("[H3Batch] %s done: %s" % (sid, moved), flush=True)
                        break
                    else:
                        last_err = "status=%s" % status
                        print("[H3Batch] %s failed (%s), retry %d/%d" % (sid, last_err, attempt + 1, self.retries), flush=True)
                except ShotError as e:
                    last_err = str(e)
                    print("[H3Batch] %s asset/param error: %s" % (sid, e), flush=True)
                    break
                except Exception as e:
                    last_err = str(e)
                    print("[H3Batch] %s exception (%s), retry %d/%d" % (sid, last_err, attempt + 1, self.retries), flush=True)
                    time.sleep(5)
            if not ok:
                manifest[sid] = {"status": "failed", "error": last_err}
            else:
                if self.mode == "concat":
                    vpath = self._video_path(manifest[sid].get("files", []))
                    if vpath:
                        concat_parts.append(vpath)
                        prev_frame = self._extract_last_frame(vpath)
                        manifest[sid]["tail_frame"] = prev_frame
            self._save_manifest(manifest)
            done += 1
        if self.mode == "concat" and concat_parts:
            out = os.path.join(self.run_dir, "concat_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
            ok = self._concat_videos(concat_parts, out)
            print("[H3Batch] concat done: %s (%s)" % (out, "OK" if ok else "FAILED"), flush=True)
            manifest["_concat"] = {"status": "success" if ok else "failed", "file": out}
            self._save_manifest(manifest)
        print("[H3Batch] batch finished: %d processed" % done, flush=True)

    def _video_path(self, files):
        # 当前运行的时间戳文件夹
        for fn in files or []:
            if fn.endswith(".mp4"):
                c = os.path.join(self.run_dir, fn)
                if os.path.exists(c):
                    return c
        return None

    def _video_path_old(self, files, run_dir):
        # 断点续跑时, 从旧运行的时间戳文件夹找文件
        for fn in files or []:
            if fn.endswith(".mp4"):
                for c in (os.path.join(run_dir, fn) if run_dir else None,
                          os.path.join(self.output_dir, "video", "shots", fn),
                          os.path.join(self.output_dir, fn)):
                    if c and os.path.exists(c):
                        return c
        return None

    def _collect_output(self, files, sid):
        """把本段产物从服务器 output 目录移到本次运行的时间戳文件夹。
        files 里的条目是 _collect_files 给出的 subfolder/filename 相对路径。"""
        moved = []
        for rel in files or []:
            if not rel.endswith(".mp4"):
                continue
            base = os.path.basename(rel)
            sub = os.path.dirname(rel)
            src = None
            for cand in (os.path.join(self.output_dir, sub, base) if sub else None,
                         os.path.join(self.output_dir, "video", "shots", base),
                         os.path.join(self.output_dir, base)):
                if cand and os.path.exists(cand):
                    src = cand
                    break
            if src:
                dst = os.path.join(self.run_dir, base)
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.move(src, dst)
                moved.append(base)
            else:
                print("[H3Batch] %s output file not found (maybe cleaned): %s" % (sid, rel), flush=True)
                moved.append(base)
        return moved

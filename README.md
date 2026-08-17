# H3 Shot Batch Renderer — V1.0

A ComfyUI custom node that turns the MiniMax H3 AI video model into a CSV-driven batch video production pipeline. Fill in one CSV (material pool + storyboard table) and the node batches out all shots automatically, saving hours of manual one-shot-at-a-time work.

> **V1.0** is the open-source archive release. Newer versions (V1.1, V1.2) add preflight checks, a path picker, a CSV form wizard, dual-anchored single-shot rerun, completion toasts, a template dropdown, and more. They supersede V1.0.

---

## ⚠️ What this is — and what it is NOT

**This node is a wrapper / orchestration tool only. It is not a model distributor.**

### What V1.0 IS:
- A Python custom node for ComfyUI that batch-renders video shots driven by a CSV file.
- Orchestration logic: CSV parsing, material-pool wiring, prompt injection, template selection, tail-frame linking, manifest-based resume.
- **Model-agnostic** — V1.0 calls MiniMax H3 custom nodes by `class_type`, not by model name. It does not care which H3 model variant, LoRA, VAE, CLIP, or audio model you load; configure those in your own `workflow_path` JSON and V1.0 passes them through transparently.
- AGPL-3.0-licensed source code (see `LICENSE`) — free for personal use; derivative distributions must stay open-source.

### What V1.0 is NOT:
- **Not** a model distribution channel. This repo contains **no model weights, no LoRA files, no VAE, no CLIP, no audio model files.**
- **Not** a fork or redistribution of the MiniMax H3 custom nodes. `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3TurboLoRA` / `MiniMaxH3MultiRateSamplerEXPT8` are separate third-party projects you must install yourself.
- **Not** opinionated about which H3 model variant or LoRA you use. Whatever you load in your workflow template is what gets used.
- **Not** bundled with `ffmpeg`. You must install `ffmpeg` on your system `PATH` yourself.
- **Not** affiliated with or endorsed by MiniMax, Stability, ComfyUI, or any model provider. This is an independent community tool.

### Your responsibility
Before installing V1.0, you are responsible for:
1. **Installing the MiniMax H3 custom nodes** (and their model weights) from their respective sources, and complying with their license terms.
2. **Installing `ffmpeg`** on your system and ensuring it is on `PATH`.
3. **Complying with the licenses of all third-party components** (MiniMax H3 nodes, ffmpeg, model weights, LoRAs, etc.) that you choose to use.
4. **Verifying that your use case is permitted** under all applicable upstream licenses and regional regulations.

If you have any doubt about whether your intended use is compliant, **stop and consult a legal professional**. The authors accept no liability for downstream license violations.

---

## Features

### Two modes

- **storyboard** — Material pool with `<Picture N>` / `<Video N>` / `<Audio N>` tag references in prompts. The engine wires only the slots you reference.
- **concat** — Auto-selects T2V / I2V / ref2v templates based on pool contents. Chains segments via last-frame linking, then stitches with ffmpeg into one long video.

### Core capabilities

- CSV-driven batch processing (one file = many shots)
- Resume (per-CSV manifest, skip-done, per-shot retries)
- Material pool tag references (`<Picture 1>`, `<Video 2>`, etc.)
- Auto template selection (T2V / I2V / ref2v) based on material pool contents
- Tail-frame linking between segments (last frame of segment N feeds segment N+1)
- ffmpeg concat into a single long video (concat mode)
- ComfyUI HTTP API driven (auto-detects port 8188–8200)
- Timestamped output archiving (previous runs are never overwritten)

---

## Installation

### Manual

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/nitace666/ComfyUI-H3-Shotline.git
# Restart ComfyUI
```

Or copy the contents of this directory into `ComfyUI/custom_nodes/ComfyUI-H3-Shotline/`.

> **Coexisting with V1.1 / V1.2:** this node registers as `H3ShotBatchRenderer`. If you also install the V1.1 / V1.2 node, the two share the same node name and the **last-installed (by load order) wins**; the other is replaced. Install only one. V1.1 / V1.2 supersede V1.0 (they include all V1.0 features plus more), so you don't need V1.0 if you use them.

---

## Prerequisites

**You must bring your own.** V1.0 ships with **zero models, zero nodes, zero binaries**. The items below are **external** to this repository and are **your responsibility** to install, source, and license-track.

1. **ComfyUI** (this node runs as a custom node inside it)
2. **MiniMax H3 custom nodes** — provides `MiniMaxH3ReferenceToVideo` / `MiniMaxH3ImageToVideo` / `MiniMaxH3TurboLoRA` / `MiniMaxH3MultiRateSamplerEXPT8`, plus their model weights (UNet / VAE / CLIP / audio / LoRA files, etc.).
3. **ffmpeg** (must be on `PATH`) — for last-frame extraction and video concatenation.
4. **Python 3.8+** (bundled with ComfyUI's venv — no separate install needed)

If the MiniMax H3 nodes are missing, the engine prints the missing-node list to the console and refuses to run. The preflight check does **not** download or install anything.

Each prerequisite is licensed by its own author. V1.0 makes no warranty about their availability, compatibility, or compliance with your use case.

---

## Node parameters

| Parameter | Description |
|---|---|
| `mode` | `storyboard` (material pool + tag references) or `concat` (auto template + last-frame chain) |
| `workflow_path` | Render template (used in storyboard mode; ignored in concat mode which auto-selects) |
| `csv_path` | CSV path (templates: `storyboard_template.csv` / `concat_template.csv`) |
| `input_dir` | Material root (reference images / videos / audio, supports subdirs) |
| `output_dir` | Output directory + manifest |
| `server_input_dir` | The directory ComfyUI actually reads material from (your `--input-directory`) |
| `port` | ComfyUI port (`0` = auto-detect 8188–8200) |
| `start_from` | Start from row N |
| `retries` | Per-shot retry count |
| `skip_done` | Skip already-successful shots (for resume) |

Path fields support drag-and-drop.

---

## CSV structure

```csv
,1,2,3,4,5,6,7,8,9
Picture,reference.png,outfit.png,,,,,,,,    ← Material pool: col 2 = slot 1, col 3 = slot 2 ...
Video,,gesture.mp4,,,,,,,,                 ← leave unused slots empty
Audio,,voice.wav,,,,,,,,                   ← material pool row
aspect,16:9                                ← global: 1:1 / 16:9 / 9:16 / 3:4 / 4:3 / 3:2 / 2:3 / 21:9
quality,0.4                                ← global: megapixels 0.1–1 (use ≥ 0.4)
steps,8V8A                                 ← global: video+audio steps (4V8A / 8V8A / 4V20A)
ID,prompt,duration,seed,export_name
s01,"<Picture 1> A woman walks through a rainy street at night",5,424242,s01
```

### storyboard mode

- Reference materials with `<Picture N>` / `<Video N>` / `<Audio N>` in the prompt.
- Unreferenced slots are not wired; the template's default chains are pruned.
- Empty `duration` = 5s; `seed` 0 = random.

### concat mode (auto template selection)

| Pool contents | Auto mode | Behavior |
|---|---|---|
| Empty | T2V | Segment 1 with no image, later segments use the previous tail-frame |
| 1 image only | I2V | Segment 1 with that image, later segments use the previous tail-frame |
| Multiple | ref2v | Every segment uses the material pool |

After all segments complete, ffmpeg concatenates into one long video (`output/concat_<timestamp>.mp4`). Audio is preserved as-is.

---

## Resume

The manifest at `output_dir/h3_batch_manifest_<csv-stem>.json` records per-shot status and tail-frames. Re-running the same CSV with `skip_done=True` automatically skips already-successful shots. `start_from` overrides for a hard resume from row N.

---

## Testing

```bash
cd <repo-root>
.venv\Scripts\python.exe -m pytest tests --import-mode=importlib -q
# 25 passed
```

The test suite covers the P0 blockers fixed in this release: workspace-path resolution via `folder_paths`, the MiniMax H3 node preflight check, CSV header compatibility, the concat sibling-template resolver, real per-shot seed injection, and output collection from history `subfolder`.

---

## License

AGPL-3.0 — see `LICENSE`. Personal / in-ComfyUI use is unrestricted; if you redistribute a modified version you must release your source under AGPL-3.0. A separate commercial license (closed-source integration, no AGPL obligations) is available on request. This node does **not** bundle the MiniMax H3 custom nodes or ffmpeg; those are separately licensed by their respective authors.

---

## Version history

- **V1.0** (2026-08-15, open-source archive release): CSV-driven batch engine, two modes (`storyboard` / `concat`), material pool tags, auto template selection, tail-frame linking, timestamped archive, port auto-detect, `folder_paths` path resolution, MiniMax H3 node preflight check. 25 pytest tests. `seed` normalization for Excel-style inputs (`"1.0"`) and fixed queue `prompt_id` parsing.

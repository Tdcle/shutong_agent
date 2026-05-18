"""图片 OCR 文字提取工具。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.tools.base import tool
from app.tools.workspace import resolve_readonly

_OCR_SCRIPT = r'''
import json, sys, os, logging
os.environ.setdefault("PADDLEOCR_HOME", os.path.expanduser("~/.paddleocr"))
logging.getLogger("paddleocr").setLevel(logging.WARNING)
logging.getLogger("paddlex").setLevel(logging.WARNING)
try:
    from paddleocr import PaddleOCR
    import numpy as np
    from PIL import Image
except ImportError as e:
    print(json.dumps({"error": f"PaddleOCR import failed: {e}"}))
    sys.stdout.flush()
    sys.exit(1)

try:
    img = Image.open("{image_path}").convert("RGB")
    w, h = img.size
    img_np = np.array(img)
    ocr = PaddleOCR(lang="ch")
    results = ocr.predict(img_np)
    items = []
    if results:
        for page in results:
            texts = page.get("rec_texts", []) or []
            scores = page.get("rec_scores", []) or []
            polys = page.get("rec_polys", []) or page.get("dt_polys", []) or []
            for i, (text, score) in enumerate(zip(texts, scores)):
                if not text:
                    continue
                cy = h / 2
                if i < len(polys) and len(polys[i]) >= 4:
                    pts = polys[i][:4]
                    cy = sum(p[1] for p in pts) / 4
                items.append({
                    "text": text,
                    "conf": round(float(score), 2),
                    "y": round(cy, 0),
                })
    # Sort in reading order (top to bottom; within same line, left to right)
    items.sort(key=lambda it: (it["y"], it.get("x", 0)))
    print(json.dumps({"width": w, "height": h, "items": items}, ensure_ascii=False))
    sys.stdout.flush()
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=False))
    sys.stdout.flush()
    sys.exit(1)
'''


def _run_paddleocr(image_path: Path) -> str:
    agent_python = str(
        Path(__file__).parent.parent.parent / ".agent_runtime" / "Scripts" / "python.exe"
    )
    if not Path(agent_python).exists():
        return "PaddleOCR 运行环境未找到（.agent_runtime 不存在）。请运行 setup 安装依赖。"

    script = _OCR_SCRIPT.replace("{image_path}", image_path.as_posix())

    # Build a clean environment — strip host Python paths that may
    # inject incompatible DLLs or site-packages into the agent runtime.
    clean_env = {}
    strip_prefixes = {
        "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV", "CONDA_PROMPT_MODIFIER",
    }
    for key, val in os.environ.items():
        if key in strip_prefixes:
            continue
        if key.startswith("CONDA_") or key.startswith("PIP_"):
            continue
        clean_env[key] = val
    clean_env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PADDLEOCR_HOME": str(Path.home() / ".paddleocr"),
    })

    try:
        proc = subprocess.run(
            [agent_python, "-u", "-c", script],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
            env=clean_env,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return "OCR 执行超时（超过 300 秒）。图片可能较大或模型正在下载中，请稍后重试。"
    except Exception as exc:
        return f"OCR 执行异常：{exc}"

    # Try to parse JSON from stdout; if empty, also check stderr for JSON
    data = None
    if stdout:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            pass

    if data is None and stderr:
        try:
            data = json.loads(stderr)
        except json.JSONDecodeError:
            pass

    if data is None:
        stderr_preview = stderr[:600] if stderr else "(no output)"
        # Try to decode garbled UTF-8 stderr as GBK for better diagnostics
        try:
            stderr_gbk = proc.stderr.encode("latin-1").decode("gbk", errors="replace")[:600]
        except Exception:
            stderr_gbk = ""
        diagnostic = stderr_gbk or stderr_preview
        return f"OCR 执行失败（无有效输出）。\nstderr: {diagnostic}"

    if "error" in data:
        return f"OCR 执行失败：{data['error']}"

    items = data.get("items", [])
    if not items:
        return "未检测到文字。图片可能为纯图、截图无文字区域，或文字过于模糊。"

    w, h = data["width"], data["height"]
    LOW_CONF = 0.70

    # Build clean, model-friendly output:
    # - Numbered list in reading order (top→bottom, left→right)
    # - Low-confidence items marked with [?] prefix (no per-line position noise)
    # - Confidence info separated at the bottom for the model's reference
    result = [f"[图片文字识别] {w}×{h}，共 {len(items)} 条文字", ""]
    low_conf_items: list[str] = []

    for i, item in enumerate(items, 1):
        text = item["text"]
        conf = item["conf"]
        if conf < LOW_CONF:
            result.append(f"{i}. [?] {text}")
            low_conf_items.append(f"  #{i}（{conf:.0%}）→ {text}")
        else:
            result.append(f"{i}. {text}")

    if low_conf_items:
        result.append("")
        result.append("---")
        result.append(f"以下 {len(low_conf_items)} 条置信度较低（<{LOW_CONF:.0%}），可能不准确：")
        result.extend(low_conf_items)

    return "\n".join(result)


@tool(
    name="analyze_image",
    description="对图片进行 OCR 文字识别，返回结构化的文字列表。结果可直接呈现给用户，无需再用 execute_python 二次处理。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "图片文件路径"},
        },
        "required": ["path"],
    },
    permission_level="read",
)
def analyze_image(path: str) -> str:
    p = resolve_readonly(path)
    if not p.exists():
        return f"Error: file does not exist: {path}"
    if not p.is_file():
        return f"Error: path is not a file: {path}"

    ext = p.suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico"}:
        return f"Error: unsupported image format '{ext}'. Supported: png, jpg, jpeg, gif, bmp, webp, tiff, ico"

    return f"图片 OCR 结果（{p.name}）：\n\n" + _run_paddleocr(p)

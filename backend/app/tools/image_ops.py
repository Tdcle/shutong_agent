"""图片分析工具：尺寸、颜色、OCR 文字提取。"""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

from app.tools.base import tool
from app.tools.workspace import resolve_readonly

VALID_MODES = {"info", "ocr", "colors", "all"}


# ---- Color analysis helpers ----

def _rgb_to_name(r: int, g: int, b: int) -> str:
    """Map an RGB triplet to the closest named color."""
    COLORS = {
        "红": (220, 50, 50), "深红": (160, 30, 30), "橙": (240, 140, 30),
        "黄": (240, 220, 40), "绿": (50, 180, 80), "深绿": (20, 100, 40),
        "青": (40, 200, 200), "蓝": (50, 100, 220), "深蓝": (20, 40, 120),
        "藏青": (26, 26, 46), "紫": (140, 60, 200), "粉": (240, 140, 180),
        "棕": (140, 100, 60), "灰": (150, 150, 150), "深灰": (80, 80, 80),
        "浅灰": (210, 210, 210), "白": (250, 250, 250), "黑": (20, 20, 20),
    }
    best, best_dist = "unknown", float("inf")
    for name, (cr, cg, cb) in COLORS.items():
        dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist < best_dist:
            best_dist = dist
            best = name
    return best


def _analyze_colors(img) -> str:
    """Extract dominant colors, brightness, contrast info from a PIL Image."""
    img_rgb = img.convert("RGB")
    w, h = img_rgb.size
    step = max(1, (w * h) // 2000)
    pixels = []
    for y in range(0, h, max(1, int(step ** 0.5))):
        for x in range(0, w, max(1, int(step ** 0.5))):
            r, g, b = img_rgb.getpixel((x, y))
            pixels.append((r, g, b))

    if not pixels:
        return "无法提取颜色信息（图片可能为空）。"

    quantised = [((r // 32) * 32, (g // 32) * 32, (b // 32) * 32) for r, g, b in pixels]
    counter = Counter(quantised)
    top = counter.most_common(6)

    avg_bright = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels) / len(pixels)
    brightnesses = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    avg = sum(brightnesses) / len(brightnesses)
    std = (sum((b - avg) ** 2 for b in brightnesses) / len(brightnesses)) ** 0.5

    theme = "亮色" if avg_bright > 128 else "暗色"

    lines = [
        f"整体为{theme}主题（平均亮度 {avg_bright:.0f}/255）。",
        "主色调分布：",
    ]
    for rgb, count in top:
        pct = count / len(quantised) * 100
        if pct < 3:
            break
        name = _rgb_to_name(*rgb)
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        lines.append(f"  {name} {hex_color}（约占 {pct:.0f}%）")

    if std > 70:
        lines.append(f"亮暗对比度较高（标准差 {std:.0f}），画面可能包含明显的明暗区域或 UI 元素。")
    elif std > 35:
        lines.append(f"亮暗对比度中等（标准差 {std:.0f}）。")
    else:
        lines.append(f"亮暗对比度较低（标准差 {std:.0f}），画面整体均匀。")

    return "\n".join(lines)


# ---- OCR via agent runtime Python ----

_OCR_SCRIPT = r'''
import json, sys
try:
    from paddleocr import PaddleOCR
    import numpy as np
    from PIL import Image
except ImportError as e:
    print(json.dumps({"error": f"PaddleOCR import failed: {e}"}))
    sys.exit(1)

try:
    img = Image.open("{image_path}").convert("RGB")
    w, h = img.size
    img_np = np.array(img)
    ocr = PaddleOCR(lang="ch")
    results = ocr.predict(img_np)
    lines = []
    if results:
        for page in results:
            texts = page.get("rec_texts", []) or []
            scores = page.get("rec_scores", []) or []
            polys = page.get("rec_polys", []) or page.get("dt_polys", []) or []
            for i, (text, score) in enumerate(zip(texts, scores)):
                if not text:
                    continue
                cx, cy = w / 2, h / 2
                if i < len(polys) and len(polys[i]) >= 4:
                    pts = polys[i][:4]
                    cx = sum(p[0] for p in pts) / 4
                    cy = sum(p[1] for p in pts) / 4
                xd = "左" if cx < w * 0.33 else ("右" if cx > w * 0.67 else "中央")
                yd = "上" if cy < h * 0.33 else ("下" if cy > h * 0.67 else "中间")
                pos = f"{yd}偏{xd}" if xd != "中央" or yd != "中间" else "中央"
                lines.append({"pos": pos, "text": text, "conf": round(float(score), 2)})
    print(json.dumps({"width": w, "height": h, "lines": lines}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(1)
'''


def _run_paddleocr(image_path: Path) -> str:
    """Run PaddleOCR via the agent runtime Python (supports Python 3.13)."""
    import sys

    agent_python = str(
        Path(__file__).parent.parent.parent / ".agent_runtime" / "Scripts" / "python.exe"
    )
    if not Path(agent_python).exists():
        return "PaddleOCR 运行环境未找到（.agent_runtime 不存在）。"

    script = _OCR_SCRIPT.replace("{image_path}", image_path.as_posix())

    try:
        proc = subprocess.run(
            [agent_python, "-c", script],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
            env={**os.environ},
        )
        stdout = proc.stdout.strip()
        if not stdout:
            stderr_info = proc.stderr.strip()[:500] if proc.stderr else "(no output)"
            return f"OCR 执行失败（无输出）。stderr: {stderr_info}"

        data = json.loads(stdout)
    except json.JSONDecodeError:
        return f"OCR 结果解析失败。raw output: {proc.stdout[:500] if proc.stdout else '(empty)'}"
    except subprocess.TimeoutExpired:
        return "OCR 执行超时（超过 300 秒）。"
    except Exception as exc:
        return f"OCR 执行异常：{exc}"

    if "error" in data:
        return f"OCR 执行失败：{data['error']}"

    lines = data.get("lines", [])
    if not lines:
        return "未检测到文字。"

    result_lines = []
    for i, line in enumerate(lines, 1):
        result_lines.append(
            f"  [{i}] {line['pos']}，置信度 {line['conf']:.2f}  \"{line['text']}\""
        )
    result_lines.append(f"共发现 {len(lines)} 个文字区域。")
    return "\n".join(result_lines)


# ---- Main tool ----

@tool(
    name="analyze_image",
    description=(
        "分析图片文件，提取结构化信息（尺寸/格式/颜色/文字OCR）。"
        "当多模态视觉模型不可用时，用这个工具获取图片内容让模型理解。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "图片文件路径"},
            "analysis": {
                "type": "string",
                "description": "分析模式：info（基本信息）、ocr（文字识别）、colors（颜色分析）、all（全部）",
            },
        },
        "required": ["path"],
    },
    permission_level="read",
)
def analyze_image(path: str, analysis: str = "all") -> str:
    p = resolve_readonly(path)
    if not p.exists():
        return f"Error: file does not exist: {path}"
    if not p.is_file():
        return f"Error: path is not a file: {path}"

    mode = analysis.strip().lower()
    if mode not in VALID_MODES:
        return f"Error: unknown analysis mode '{analysis}'. Valid modes: {', '.join(sorted(VALID_MODES))}"

    ext = p.suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico"}:
        return f"Error: unsupported image format '{ext}'. Supported: png, jpg, jpeg, gif, bmp, webp, tiff, ico"

    try:
        from PIL import Image
        img = Image.open(p)
    except Exception as exc:
        return f"Error: failed to open image: {exc}"

    try:
        img.load()
    except Exception as exc:
        return f"Error: failed to load image pixels: {exc}"

    sections: list[str] = [f"{'=' * 10} 图片分析：{p.name} {'=' * 10}", ""]

    # --- info ---
    if mode in ("info", "all"):
        lines = ["[基本信息]", f"尺寸：{img.size[0]} x {img.size[1]} 像素"]
        fmt = img.format or ext.lstrip(".").upper()
        lines.append(f"格式：{fmt}，模式：{img.mode}")
        try:
            size_kb = p.stat().st_size / 1024
            if size_kb >= 1024:
                lines.append(f"文件大小：{size_kb / 1024:.1f} MB")
            else:
                lines.append(f"文件大小：{size_kb:.0f} KB")
        except Exception:
            pass
        dpi = img.info.get("dpi")
        if dpi:
            lines.append(f"DPI：{dpi[0]:.0f} x {dpi[1]:.0f}")
        exif = img.getexif()
        if exif:
            for tag_id in (271, 272, 306, 36867, 36868):
                val = exif.get(tag_id)
                if val:
                    tag_names = {271: "设备", 272: "型号", 306: "拍摄时间", 36867: "原始时间", 36868: "数字化时间"}
                    lines.append(f"{tag_names.get(tag_id, tag_id)}：{val}")
        sections.append("\n".join(lines))
        sections.append("")

    # --- colors ---
    if mode in ("colors", "all"):
        sections.append("[颜色特征]")
        try:
            sections.append(_analyze_colors(img))
        except Exception as exc:
            sections.append(f"颜色分析失败：{exc}")
        sections.append("")

    # --- ocr ---
    if mode in ("ocr", "all"):
        sections.append("[文字内容] (PaddleOCR)")
        sections.append(_run_paddleocr(p))
        sections.append("")

    img.close()
    return "\n".join(sections)

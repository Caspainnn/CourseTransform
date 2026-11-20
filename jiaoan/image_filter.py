from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - 缺依赖时退化
    cv2 = None
    np = None


CODE_TOKENS = re.compile(
    r"(?im)^\s*(?:import\s+\w|from\s+\w+\s+import|class\s+\w+|def\s+\w+|return\b|print\s*\(|"
    r"for\s+.+\s+in\s+|while\s+|if\s+__name__|torch\.|np\.|plt\.|model\.|optimizer\.|"
    r"Traceback \(|>>> |\$\s+\w)"
)
CHART_WORDS = re.compile(r"(?i)(epoch|accuracy|loss|准确率|损失|轮次|折线|曲线)")
RESULT_GRID_WORDS = re.compile(r"(?i)(true\s*[:：]|pred\s*[:：]|label\s*[:：]|预测|真实)")
UI_WORDS = re.compile(r"(?i)(http|https|浏览器|检测|识别|结果|上传|截图|face|liveness|验证)")


@dataclass
class ImageDecision:
    category: str
    keep: bool
    reason: str
    confidence: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "keep": self.keep,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
            "metrics": self.metrics,
        }


def _load_gray(path: str | Path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        width, height = im.size
        rgb = im.copy()
    if np is None:
        return width, height, None, None
    arr = np.asarray(rgb)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if cv2 is not None else arr.mean(axis=2).astype("uint8")
    return width, height, arr, gray


def _qr_decoded(gray) -> str:
    if cv2 is None or gray is None:
        return ""
    try:
        data, points, _ = cv2.QRCodeDetector().detectAndDecode(gray)
        return str(data or "")
    except Exception:
        return ""


def _dhash(path: str | Path, size: int = 8) -> str:
    with Image.open(path) as im:
        im = im.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        pixels = list(im.getdata())
    bits = []
    for y in range(size):
        for x in range(size):
            bits.append("1" if pixels[y * (size + 1) + x] > pixels[y * (size + 1) + x + 1] else "0")
    return "".join(bits)


def _hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def classify_image(
    path: str | Path,
    ocr_text: str = "",
    hint: str = "",
    alt: str = "",
) -> ImageDecision:
    """判断教材截图是否值得放进教案。

    保留：流程图/结构图、准确率/损失曲线、识别结果拼图、应用照片、结果界面。
    剔除：二维码、源代码/终端截图、纯装饰小图、空白图、重复图。
    """
    path = Path(path)
    hint = str(hint or "").strip()
    alt = str(alt or "").strip()
    text = f"{ocr_text}\n{alt}\n{hint}"
    if hint:
        if re.search(r"(不要|删除|剔除|不放|不用)", hint):
            return ImageDecision("user_reject", False, "用户提示：不放入教案", 1.0)
        if re.search(r"(保留|需要|放入|示意图|结果图|曲线图)", hint):
            return ImageDecision("user_keep", True, "用户提示：保留", 1.0)

    try:
        width, height, rgb, gray = _load_gray(path)
    except Exception as exc:
        return ImageDecision("unreadable", False, f"图片无法读取：{exc}", 1.0)
    if width < 100 or height < 60:
        return ImageDecision("too_small", False, f"尺寸过小（{width}×{height}）", 0.95)

    if np is None or rgb is None:
        return ImageDecision("unknown", False, "缺少 OpenCV/NumPy，无法判断", 0.2)

    dark = float((gray < 60).mean())
    white = float((gray > 220).mean())
    colorfulness = float(rgb.std(axis=(0, 1)).mean())
    edge = 0.0
    if cv2 is not None:
        edge = float((cv2.Canny(gray, 50, 150) > 0).mean())
    metrics = {
        "width": width,
        "height": height,
        "dark_ratio": round(dark, 3),
        "white_ratio": round(white, 3),
        "colorfulness": round(colorfulness, 1),
        "edge_ratio": round(edge, 3),
    }

    qr = _qr_decoded(gray)
    if qr:
        return ImageDecision("qr_code", False, "二维码 / 扫码链接，不适合放进教案", 0.99, metrics)

    # 结果拼图：MNIST 的 True/Pred 网格，是讲解投毒效果的证据图，必须保留。
    if RESULT_GRID_WORDS.search(text) and dark >= 0.2 and white >= 0.2:
        return ImageDecision("result_grid", True, "识别结果拼图，能支撑结果分析", 0.95, metrics)

    # 深色代码/终端截图：深色占比高，文字细密，通常是 PyCharm/终端。
    code_hits = len(CODE_TOKENS.findall(ocr_text or ""))
    if dark >= 0.82 and white <= 0.12 and colorfulness < 75:
        return ImageDecision("code_screenshot", False, "源代码/终端截图，教案不需要", 0.98, metrics)
    if code_hits >= 2 and dark >= 0.45:
        return ImageDecision("code_screenshot", False, "检测到代码/终端文本，教案不需要", 0.9, metrics)

    # 白底图：先区分曲线图、流程图和普通界面。
    if white >= 0.62 and dark <= 0.22:
        digits = sum(ch.isdigit() for ch in (ocr_text or ""))
        if CHART_WORDS.search(ocr_text or "") and digits >= 8 and 1.15 <= width / max(1, height) <= 2.4:
            return ImageDecision("chart", True, "准确率/损失曲线，支撑实验分析", 0.9, metrics)
        if edge >= 0.025 or len((ocr_text or "").strip()) >= 4:
            return ImageDecision("diagram", True, "流程图/结构图/表格，适合讲解", 0.85, metrics)
        return ImageDecision("blank_or_decorative", False, "白底且缺少有效信息", 0.7, metrics)

    if UI_WORDS.search(ocr_text or "") and white >= 0.45:
        return ImageDecision("result_screenshot", True, "识别结果/系统界面，能说明应用效果", 0.78, metrics)

    # 照片/应用场景：保留，但要求不是纯黑/纯噪声。
    if colorfulness >= 50 and 0.25 <= dark <= 0.8:
        return ImageDecision("photo", True, "教材应用照片/场景图", 0.72, metrics)

    if dark >= 0.92:
        return ImageDecision("dark_noise", False, "接近纯黑，无法判断教学用途", 0.85, metrics)
    return ImageDecision("unknown", False, "无法判断教学用途，默认剔除", 0.6, metrics)


def image_digest(path: str | Path) -> str:
    return _dhash(path)


def is_duplicate(a: str, b: str, threshold: int = 5) -> bool:
    return _hamming(a, b) <= threshold

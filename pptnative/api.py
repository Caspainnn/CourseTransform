"""Small OpenAI-compatible transport shared by both configured API models."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def settings():
    config = {}
    env = Path(os.environ.get("PPTNATIVE_ENV_FILE", ROOT / ".env"))
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.startswith("LLM_"):
                config[key] = value.strip().strip("\"'")
    config.update({k: v for k, v in os.environ.items() if k.startswith("LLM_")})
    return config


def call(model_key, messages, timeout=180, **options):
    config = settings()
    request = Request(config["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                      data=json.dumps({"model": config[model_key], "messages": messages, **options},
                                      ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + config["LLM_API_KEY"]})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            choice = payload["choices"][0]
            return {**choice["message"], "_finish_reason": choice.get("finish_reason"), "_usage": payload.get("usage")}
    except HTTPError as error:
        raise RuntimeError(f"{config[model_key]} HTTP {error.code}") from None


def image_content(path):
    buffer = BytesIO()
    with Image.open(path) as source:
        source.convert("RGB").save(buffer, format="JPEG", quality=90)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}

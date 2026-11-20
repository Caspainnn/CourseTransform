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


def role_for(model_key):
    return "vision" if model_key == "LLM_OUTLINE_JSON_MODEL" else "reasoning"


def user_model(role):
    """The signed-in account's override, if any; otherwise the server default."""
    try:
        import auth
    except ImportError:
        return None
    uid = auth.current_uid()
    if not uid:
        return None
    item = auth.active_model(uid, role)
    if not item:
        return None
    return {"base_url": item.get("baseUrl", "").rstrip("/"), "api_key": item.get("apiKey", ""),
            "model": item.get("model", ""), "features": item.get("features") or {}}


def model_name(model_key):
    override = user_model(role_for(model_key))
    if override and override["model"]:
        return override["model"]
    return settings().get(model_key, "")


def resolve(model_key):
    override = user_model(role_for(model_key))
    if override and override["base_url"] and override["api_key"] and override["model"]:
        return override["base_url"], override["api_key"], override["model"]
    config = settings()
    return config["LLM_BASE_URL"].rstrip("/"), config["LLM_API_KEY"], config[model_key]


def call(model_key, messages, timeout=180, **options):
    base_url, api_key, model = resolve(model_key)
    request = Request(base_url + "/chat/completions",
                      data=json.dumps({"model": model, "messages": messages, **options},
                                      ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + api_key})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            choice = payload["choices"][0]
            return {**choice["message"], "_finish_reason": choice.get("finish_reason"), "_usage": payload.get("usage")}
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{model} HTTP {error.code}: {detail}") from None


def probe(base_url, api_key, model, timeout=20):
    """One tiny round-trip used by the model-management form."""
    request = Request(base_url.rstrip("/") + "/chat/completions",
                      data=json.dumps({"model": model, "max_tokens": 8,
                                       "messages": [{"role": "user", "content": "ping"}]}).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + api_key})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from None
    if not payload.get("choices"):
        raise RuntimeError("接口返回中没有 choices")
    return True


def image_content(path):
    buffer = BytesIO()
    with Image.open(path) as source:
        source.convert("RGB").save(buffer, format="JPEG", quality=90)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}

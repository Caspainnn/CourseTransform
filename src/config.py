import copy
from pathlib import Path

import yaml

DEFAULT_CONFIG: dict = {
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "LLM_API_KEY",
        "temperature": 0.3,
        "timeout_seconds": 120,
        "max_retries": 3,
    },
    "vision": {
        "enabled": False,
        "base_url": "http://localhost:8000/v1",
        "model": "qwen2.5-vl",
        "api_key_env": "VISION_API_KEY",
        "timeout_seconds": 180,
        "max_retries": 2,
        "prompt": "请用中文描述这张图片的内容。如果是公式请用 LaTeX，如果是表格请用 Markdown 表格，如果是示意图请说明含义。",
    },
    "stage1": {
        "chapter_pattern": r"^第[0-9一二三四五六七八九十百]+章",
        "ignore_toc_lines": True,
        "image_dir": "images",
        "image_prefix": "image-",
        "image_format": "jpg",
        "caption_pattern": r"^图\s*[0-9]+[-–.][0-9]+",
        "min_image_size": 32,
        "use_vision": False,
    },
    "stage2": {
        "total_minutes": 45,
        "max_validation_rounds": 2,
        "fill_markers": ["____"],
        "east_asia_font": "",
        "table_retry_on_shape_mismatch": True,
        "summary_length": 300,
    },
    "validation": {
        "checkitems": [],
    },
    "paths": {
        "input_dir": "input",
        "output_dir": "output",
    },
}


def deep_merge(base: dict, override: dict | None) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path is not None:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        config = deep_merge(config, data)
    return config

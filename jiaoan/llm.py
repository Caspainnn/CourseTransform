from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import Console, extract_json, now_iso, rough_tokens, sha256_text

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

# 修改缓存键版本，避免继续命中旧版本写入的 text=null 缓存。
CACHE_VERSION = "v2"

PURPOSE_LABELS = {
    "plan": "阶段1 教学目标与课时大纲",
    "phases": "阶段2 师生活动详案",
    "finish": "阶段3 板书/作业/评价/引用",
    "figures": "教材配图分配",
    "repair": "自动修复",
    "review": "教研复核",
    "test": "接口测试",
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMSettings:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 240
    max_repair: int = 2
    max_context_chars: int = 24000
    cache_dir: str = ".jiaoan_cache"
    no_cache: bool = False
    offline: bool = False
    # Qwen3 / R1 等“思考模型”会先消耗 completion token 输出 reasoning_content，
    # 正文 content 可能为 null。默认对这类模型请求关闭思考模式。
    disable_thinking: bool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and not self.offline


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    cached: bool = False
    finish_reason: str = ""
    reasoning: str = ""

    @property
    def combined_text(self) -> str:
        return self.text or self.reasoning or ""


def _looks_like_reasoning_model(model: str) -> bool:
    name = str(model or "").lower()
    markers = ("qwen3", "qwen-3", "qwq", "reasoner", "deepseek-r1", "deepseek-reasoner", "thinking", "o1", "o3", "o4")
    return any(m in name for m in markers)


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


def _purpose_label(purpose: str) -> str:
    return PURPOSE_LABELS.get(purpose, purpose)


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def settings_from_env(overrides: dict[str, Any] | None = None) -> LLMSettings:
    load_dotenv()
    overrides = overrides or {}
    api_key = str(overrides.get("api_key") or os.getenv("JIAOAN_API_KEY") or "")
    base_url = str(overrides.get("base_url") or os.getenv("JIAOAN_BASE_URL") or "")
    model = str(overrides.get("model") or os.getenv("JIAOAN_MODEL") or "")

    if not api_key:
        if os.getenv("DEEPSEEK_API_KEY"):
            api_key = os.environ["DEEPSEEK_API_KEY"]
            base_url = base_url or "https://api.deepseek.com/v1"
        elif os.getenv("OPENAI_API_KEY"):
            api_key = os.environ["OPENAI_API_KEY"]
            base_url = base_url or "https://api.openai.com/v1"
            model = model or "gpt-4o-mini"
        elif os.getenv("DASHSCOPE_API_KEY"):
            api_key = os.environ["DASHSCOPE_API_KEY"]
            base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            model = model or "qwen-plus"

    settings = LLMSettings(
        api_key=api_key,
        base_url=base_url or DEFAULT_BASE_URL,
        model=model or DEFAULT_MODEL,
        temperature=_env_float("JIAOAN_TEMPERATURE", _pick_float(overrides, "temperature", 0.3)),
        max_tokens=_env_int("JIAOAN_MAX_TOKENS", _pick_int(overrides, "max_tokens", 4096)),
        timeout=_env_int("JIAOAN_TIMEOUT", _pick_int(overrides, "timeout", 240)),
        max_repair=_env_int("JIAOAN_MAX_REPAIR", _pick_int(overrides, "max_repair", 2)),
        max_context_chars=_env_int("JIAOAN_MAX_CONTEXT", _pick_int(overrides, "max_context_chars", 24000)),
        cache_dir=str(overrides.get("cache_dir") or os.getenv("JIAOAN_CACHE_DIR") or ".jiaoan_cache"),
        no_cache=bool(overrides.get("no_cache", False)),
        offline=bool(overrides.get("offline", False)),
        disable_thinking=_env_bool(
            "JIAOAN_DISABLE_THINKING",
            overrides.get("disable_thinking")
            if isinstance(overrides.get("disable_thinking"), bool)
            else (_looks_like_reasoning_model(model) or None),
        ),
    )
    settings.base_url = settings.base_url.rstrip("/")
    return settings


def _pick_int(overrides: dict[str, Any], key: str, default: int) -> int:
    value = overrides.get(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pick_float(overrides: dict[str, Any], key: str, default: float) -> float:
    value = overrides.get(key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


class LLMClient:
    def __init__(self, settings: LLMSettings, console: Console | None = None):
        self.settings = settings
        self.console = console or Console()
        self.cache_dir = Path(settings.cache_dir)
        self.stats: dict[str, dict[str, int]] = {}

    def chat(
        self,
        messages: list[dict[str, str]],
        purpose: str = "chat",
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = True,
    ) -> LLMResponse:
        if not self.settings.api_key:
            raise LLMError("未配置 API Key。请设置 JIAOAN_API_KEY（或 DEEPSEEK_API_KEY / OPENAI_API_KEY），或加 --offline 先跑通流程。")
        temp = self.settings.temperature if temperature is None else temperature
        tokens = self.settings.max_tokens if max_tokens is None else max_tokens
        disable_thinking = bool(self.settings.disable_thinking)
        cache_key = sha256_text(
            json.dumps(
                {
                    "cache_version": CACHE_VERSION,
                    "base_url": self.settings.base_url,
                    "model": self.settings.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": tokens,
                    "json_mode": json_mode,
                    "disable_thinking": disable_thinking,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if not self.settings.no_cache and self._cache_path(cache_key).exists():
            try:
                data = json.loads(self._cache_path(cache_key).read_text(encoding="utf-8"))
                cached_text = str(data.get("text") or "")
            except Exception:
                cached_text = ""
            if cached_text.strip():
                self.console.detail(f"缓存命中：{purpose} ({cache_key[:8]})")
                return LLMResponse(
                    text=cached_text,
                    model=data.get("model", self.settings.model),
                    usage=data.get("usage", {}),
                    latency_ms=0,
                    cached=True,
                    finish_reason=data.get("finish_reason", "stop"),
                )
            # 旧缓存里可能有 text=null 的失败响应，直接忽略并重新请求。
            self.console.detail(f"忽略空缓存：{purpose} ({cache_key[:8]})")

        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if disable_thinking:
            # vLLM / Qwen3 服务支持的关闭思考写法；不支持时 _request_with_retry 会自动去掉该字段重试。
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        started = time.time()
        data = self._request_with_retry(payload, purpose)
        parsed = self._parse_chat_response(data)
        text = parsed["text"]
        reasoning = parsed["reasoning"]
        finish_reason = parsed["finish_reason"]
        usage = parsed["usage"]

        # 有些思考模型即使请求了 enable_thinking=false 也会先输出 reasoning_content。
        # 如果思考内容里已经包含完整 JSON，可以兜底使用；否则明确报错，绝不把 None 下标切片。
        if not text.strip() and reasoning.strip():
            try:
                obj = extract_json(reasoning)
                text = json.dumps(obj, ensure_ascii=False)
                self.console.warn(f"{_purpose_label(purpose)}：正文为空，已从思考过程末尾提取到 JSON 结果。")
            except ValueError:
                pass

        if not text.strip():
            reason_detail = self._empty_content_message(purpose, tokens, finish_reason, usage, disable_thinking)
            # 未关闭思考时，自动关闭思考再试一次。
            if not disable_thinking:
                self.console.warn(f"{_purpose_label(purpose)}：模型没有返回正文，正在关闭思考模式重试…")
                payload2 = dict(payload)
                payload2["chat_template_kwargs"] = {"enable_thinking": False}
                data = self._request_with_retry(payload2, purpose)
                parsed = self._parse_chat_response(data)
                text = parsed["text"]
                reasoning = parsed["reasoning"]
                finish_reason = parsed["finish_reason"]
                usage = parsed["usage"]
                if not text.strip() and reasoning.strip():
                    try:
                        obj = extract_json(reasoning)
                        text = json.dumps(obj, ensure_ascii=False)
                    except ValueError:
                        pass
                if text.strip():
                    disable_thinking = True
                else:
                    raise LLMError(reason_detail)
            else:
                raise LLMError(reason_detail)

        latency = int((time.time() - started) * 1000)
        self._record_stats(purpose, usage)
        if not self.settings.no_cache:
            self._cache_path(cache_key).parent.mkdir(parents=True, exist_ok=True)
            self._cache_path(cache_key).write_text(
                json.dumps(
                    {
                        "text": text,
                        "reasoning": reasoning[:4000],
                        "usage": usage,
                        "model": self.settings.model,
                        "finish_reason": finish_reason,
                        "created_at": now_iso(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        self.console.detail(f"{purpose} 完成：{latency}ms，tokens {usage.get('total_tokens', '?')}")
        return LLMResponse(
            text=text,
            model=self.settings.model,
            usage=usage,
            latency_ms=latency,
            finish_reason=finish_reason,
            reasoning=reasoning,
        )

    def _parse_chat_response(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型返回结构异常（缺少 choices[0].message）：{str(data)[:500]}") from exc
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(str(item))
            content = "".join(parts)
        text = str(content or "")
        reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "")
        usage = data.get("usage") or {}
        if not usage:
            usage = {
                "prompt_tokens": 0,
                "completion_tokens": rough_tokens(text or reasoning),
            }
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return {
            "text": text,
            "reasoning": reasoning,
            "finish_reason": str(choice.get("finish_reason") or ""),
            "usage": usage,
        }

    def _empty_content_message(
        self,
        purpose: str,
        tokens: int,
        finish_reason: str,
        usage: dict[str, Any],
        thinking_disabled: bool = False,
    ) -> str:
        label = _purpose_label(purpose)
        completion = int(usage.get("completion_tokens") or 0)
        reasoning_tokens = 0
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            reasoning_tokens = int(details.get("reasoning_tokens") or 0)
        advice = (
            "处理办法：① 把“最大输出 token”调到 8192；② 换 deepseek-chat / qwen-plus 等非思考模型；"
            "③ 确认学校网关没有强制把 completion 截断在 4096。"
            if thinking_disabled
            else "处理办法：① 在页面勾选“关闭思考模式”；② 把“最大输出 token”调到 8192；"
            "③ 或改用 deepseek-chat / qwen-plus 等非思考模型。"
        )
        if finish_reason == "length" or (reasoning_tokens and reasoning_tokens >= tokens) or completion >= tokens:
            return (
                f"{label}失败：模型把 {completion or tokens} 个输出 token 全部用于“思考过程”，没有返回正文"
                f"（finish_reason={finish_reason or 'length'}）。\n{advice}"
            )
        return (
            f"{label}失败：模型返回了空正文（finish_reason={finish_reason or '未知'}，"
            f"completion_tokens={completion}）。\n{advice}"
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        purpose: str = "json",
        temperature: float | None = None,
        max_tokens: int | None = None,
        parse_retries: int = 2,
    ) -> tuple[dict[str, Any], LLMResponse]:
        convo = list(messages)
        last_error = ""
        last_text = ""
        last_finish = ""
        label = _purpose_label(purpose)
        attempt_tokens = max_tokens
        for attempt in range(parse_retries + 1):
            resp = self.chat(convo, purpose=purpose, temperature=temperature, max_tokens=attempt_tokens, json_mode=True)
            last_text = resp.text or resp.reasoning or ""
            last_finish = resp.finish_reason
            try:
                obj = extract_json(resp.text)
                return obj, resp
            except ValueError as exc:
                last_error = str(exc)
                self.console.warn(f"{label} 返回的不是合法 JSON，第 {attempt + 1}/{parse_retries + 1} 次")
                if resp.finish_reason == "length":
                    # 输出被截断时，重试要放宽 token 上限，否则会原样再截断一次。
                    base = int(attempt_tokens or self.settings.max_tokens or 4096)
                    attempt_tokens = min(16384, max(base + 2048, int(base * 1.5)))
                    self.console.warn(f"{label} 输出被截断，下一次把 max_tokens 提高到 {attempt_tokens}")
                convo = list(messages) + [
                    {
                        "role": "assistant",
                        "content": last_text[:6000] if last_text else "（空）",
                    },
                    {
                        "role": "user",
                        "content": (
                            "你上一次的输出不是合法 JSON"
                            + ("（而且被截断了，请在保持内容完整的前提下精简）" if resp.finish_reason == "length" else "")
                            + "。请只输出一个完整、合法的 JSON 对象，不要 Markdown 代码块、解释或前后缀。"
                            "保留原有内容，修正语法；如果内容太长，优先保留必填字段并缩短每条文字。"
                        ),
                    },
                ]
        preview = re.sub(r"\s+", " ", last_text).strip()[:700]
        raise LLMError(
            f"{label}失败：模型连续 {parse_retries + 1} 次没有返回合法 JSON"
            f"（最后一次 finish_reason={last_finish or '未知'}）。\n"
            f"最后一次输出开头：{preview or '（空）'}\n"
            "处理办法：① 勾选“关闭思考模式”后重试；② 把最大输出 token 调到 8192；"
            "③ 换 deepseek-chat / qwen-plus；④ 若模型支持，确认网关没有强制截断。"
        ) from None

    def _request_with_retry(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        url = f"{self.settings.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.api_key}",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(4):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.settings.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:800]
                except Exception:
                    pass
                if exc.code == 400 and "response_format" in detail and "response_format" in payload:
                    payload = dict(payload)
                    payload.pop("response_format", None)
                    self.console.warn("该模型不支持 response_format=json_object，已自动降级为纯提示词 JSON 模式")
                    continue
                if exc.code == 400 and "chat_template_kwargs" in payload:
                    payload = dict(payload)
                    payload.pop("chat_template_kwargs", None)
                    self.console.warn("该接口不支持 chat_template_kwargs，已去掉“关闭思考”参数重试")
                    continue
                if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                    wait = min(20, 2 ** attempt + random.random())
                    self.console.warn(f"{purpose} 请求失败（HTTP {exc.code}），{wait:.1f}s 后重试")
                    time.sleep(wait)
                    last_error = exc
                    continue
                raise LLMError(f"{_purpose_label(purpose)}请求失败（HTTP {exc.code}）：{detail or exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < 3:
                    wait = min(20, 2 ** attempt + random.random())
                    self.console.warn(f"{purpose} 网络错误，{wait:.1f}s 后重试：{exc}")
                    time.sleep(wait)
                    continue
                raise LLMError(f"{_purpose_label(purpose)}网络请求失败：{exc}") from exc
            except json.JSONDecodeError as exc:
                raise LLMError(f"{_purpose_label(purpose)}接口返回的不是 JSON：{exc}") from exc
        raise LLMError(f"{_purpose_label(purpose)}多次重试仍失败：{last_error}")

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def _record_stats(self, purpose: str, usage: dict[str, Any]) -> None:
        bucket = self.stats.setdefault(purpose, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0})
        bucket["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            try:
                bucket[key] += int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass

    def stats_summary(self) -> dict[str, Any]:
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        for bucket in self.stats.values():
            for key in total:
                total[key] += int(bucket.get(key, 0))
        return {"total": total, "by_purpose": self.stats}

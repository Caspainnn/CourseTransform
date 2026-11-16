import os
import re
import time
from pathlib import Path

from openai import OpenAI

from src.utils import setup_logging


def load_dotenv(path: str | Path | None = None) -> None:
    path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI 兼容接口的文本模型客户端，带超时、重试（指数退避）与异常处理。"""

    def __init__(self, config: dict):
        load_dotenv()
        self.config = config
        self.base_url = config.get("base_url", "")
        self.model = config.get("model", "")
        self.temperature = config.get("temperature", 0.3)
        self.timeout = float(config.get("timeout_seconds", 120))
        self.max_retries = int(config.get("max_retries", 3))
        api_key_env = config.get("api_key_env", "LLM_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        self.logger = setup_logging()
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=api_key or "EMPTY",
            timeout=self.timeout,
        )

    def chat(
        self,
        system: str | None = None,
        user: str | None = None,
        messages: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str:
        msgs = list(messages or [])
        if system:
            msgs = [{"role": "system", "content": system}] + msgs
        if user:
            msgs.append({"role": "user", "content": user})
        if not msgs:
            raise LLMError("消息列表为空，无法调用模型")
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=msgs,
                    temperature=self.temperature if temperature is None else temperature,
                )
                content = resp.choices[0].message.content
                if not content:
                    raise LLMError("模型返回内容为空")
                return content
            except Exception as exc:
                last_error = exc
                wait = min(2**attempt, 30)
                self.logger.warning(
                    f"LLM 请求第 {attempt}/{self.max_retries} 次失败: {exc}，{wait}s 后重试"
                )
                time.sleep(wait)
        raise LLMError(f"LLM 请求在 {self.max_retries} 次尝试后仍失败: {last_error}")


class MockLLMClient:
    """离线演示用的确定性假模型：不访问网络，按提示词结构生成可校验的内容。

    用途：无 API Key / 无网络环境下的端到端联调与演示（比赛排错与回环演示）。
    规则：
    - 裁判类提示（system 含"评审专家"）：逐项输出 pass=true 的 JSON；
    - 表格填充提示：保留原表头与固定列，只填待填单元格；"时间"列按总分钟数均分；
    - 其余按段落生成，引用教材原句保证"术语与教材一致"。
    """

    TIME_TOTAL = 45

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.logger = setup_logging()
        self.calls = 0

    def chat(
        self,
        system: str | None = None,
        user: str | None = None,
        messages: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str:
        self.calls += 1
        system = system or ""
        user = user or ""
        if "评审专家" in system or "评审意见" in user:
            return self._judge(user)
        if "表格填充内容" in user:
            return self._fill_table(user)
        return self._fill_paragraph(user)

    # ---- 内部实现 ----

    @staticmethod
    def _extract_knowledge(user: str) -> str:
        marker = "【教材内容】"
        if marker in user:
            tail = user.split(marker, 1)[1]
            for stop in ("【前文已生成内容摘要】", "【当前板块】", "【任务】"):
                if stop in tail:
                    tail = tail.split(stop, 1)[0]
            return tail.strip()
        return ""

    @staticmethod
    def _clean_snippet(knowledge: str, limit: int = 60) -> str:
        """去掉 Markdown 标记后截取教材原句，保证引用自然。"""
        text = re.sub(r"^#+\s*", "", knowledge, flags=re.M)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit] or "教材对应知识点"

    def _judge(self, user: str) -> str:
        import json
        import re

        items: list[str] = []
        match = re.search(r"\[\s*(?:\"[^\"]+\"\s*,?\s*)+\]", user, re.S)
        if match:
            try:
                items = json.loads(match.group(0))
            except json.JSONDecodeError:
                items = []
        results = [
            {"item": item, "pass": True, "reason": "（离线演示模式）内容基于教材原文，检查通过。"}
            for item in items
        ]
        return json.dumps({"results": results}, ensure_ascii=False)

    def _fill_table(self, user: str) -> str:
        # 只取"结构如下"之后最大的连续表格行块，避免把前文摘要中的表格误当作目标表
        segment = user.split("结构如下", 1)[-1] if "结构如下" in user else user
        lines = [ln for ln in segment.splitlines() if ln.strip().startswith("|")]
        # 取最长连续块
        groups: list[list[str]] = []
        current: list[str] = []
        for line in segment.splitlines():
            if line.strip().startswith("|"):
                current.append(line)
            elif current:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        if not groups:
            return "填充失败"
        lines = max(groups, key=len)
        # 解析全部行（跳过 Markdown 分隔行），再判断首行是否为表头
        raw_rows: list[list[str]] = []
        for line in lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells == ["---"] or all(set(c) <= set("-: ") and "-" in c for c in cells if c):
                continue
            raw_rows.append(cells)
        if not raw_rows:
            return "填充失败"
        n_cols = max(len(r) for r in raw_rows)
        raw_rows = [(r + [""] * n_cols)[:n_cols] for r in raw_rows]
        # 首行含待填标记（或单元格是标签而非列名）时视为无表头，全部行都是待填数据行
        first = raw_rows[0]
        has_header = n_cols > 1 and not any("____" in c for c in first) and len(raw_rows) > 1
        header = first if has_header else [f"列{i+1}" for i in range(n_cols)]
        rows = raw_rows[1:] if has_header else raw_rows
        # 找到时间列，把总分钟数均分到各数据行
        time_idx = next((i for i, h in enumerate(header) if "时间" in h), None)
        n_rows = len(rows)
        if time_idx is not None and n_rows:
            base = self.TIME_TOTAL // n_rows
            extra = self.TIME_TOTAL - base * n_rows
            for r_idx in range(n_rows):
                minutes = base + (extra if r_idx == n_rows - 1 else 0)
                rows[r_idx][time_idx] = f"{minutes}分钟"
        knowledge = self._extract_knowledge(user)
        snippet = self._clean_snippet(knowledge)
        generic = {
            "环节": f"环节（{snippet}）",
            "主要内容": f"依据教材讲解：{snippet}",
            "教师活动": "教师讲解教材关键概念并演示操作步骤",
            "学生活动": "学生跟随操作、记录要点并完成练习",
            "设计意图": "帮助学生理解教材核心内容并形成操作能力",
            "时间": f"{self.TIME_TOTAL}分钟",
            "板书": "核心概念与操作流程图",
            "记录": "待课后填写",
        }
        for row in rows:
            for i in range(n_cols):
                if i == time_idx:
                    continue
                cell = row[i]
                # 只填空单元格或含待填标记的单元格，保留模板已有固定内容
                if "____" not in cell and cell.strip():
                    continue
                key = next((k for k in generic if k in header[i]), None)
                row[i] = generic.get(key, f"填写：{header[i]}（基于教材）")
        out = []
        if has_header:
            out.append("| " + " | ".join(header) + " |")
            out.append("| " + " | ".join("---" for _ in header) + " |")
        for row in rows:
            out.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
        return "\n".join(out)

    def _fill_paragraph(self, user: str) -> str:
        knowledge = self._extract_knowledge(user)
        heading = ""
        if "【当前板块】" in user:
            heading = user.split("【当前板块】", 1)[1].split("\n", 1)[0].strip()
        sentences = [s.strip() for s in knowledge.replace("\n", "。").split("。") if s.strip()]
        body = "。".join(sentences[:2])
        if heading:
            return f"{heading}：本课时围绕教材内容展开。{body}。以上内容均以教材原文为依据，结合课堂实际组织教学。"
        return f"本部分依据教材内容撰写。{body}。教学组织以学生活动为主，教师讲解为辅。"

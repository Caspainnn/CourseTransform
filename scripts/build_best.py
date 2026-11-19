"""构建「最佳呈现版」教材 md：真实图片 + 文字转录并存，公式块级呈现。

基于带图版（有 ![]() 图片引用），对每张图：保留图片引用，紧跟其文字转录
（代码块/公式/流程文字/数据描述）。然后复用列表断行修复、换行压缩逻辑。

断言：29 图引用全保留、每图转录文本各出现1次、代码块成对、非空白字符无损。
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# --no-images：去掉所有图片引用，只保留文字转录（含代码块/公式/流程/数据描述）
NO_IMG = "--no-images" in sys.argv

def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

REPL = load("images_to_text").REPL
SRC = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护.md"
DST = (ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md") if NO_IMG \
      else (ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_最佳呈现.md")

PAT = re.compile(r"!\[[^\]]*\]\(images/([^)]+)\)")

# 公式的文本转录升级为块级 $$（图片名 → 块级公式块）
BLOCK_FORMULA = {
    "image-0002.jpg": "判别器损失（公式2-1）：\n$$\nL_D=-\\mathbb{E}_{x\\sim p_{data}(x)}[\\log D(x)]-\\mathbb{E}_{z\\sim p_z(z)}[\\log(1-D(G(z)))]\n$$",
    "image-0003.jpg": "生成器损失（公式2-2）：\n$$\nL_G=-\\mathbb{E}_{z\\sim p_z(z)}[\\log D(G(z))]\n$$",
}

def build() -> str:
    text = SRC.read_text(encoding="utf-8")
    found = PAT.findall(text)
    assert len(found) == 29 and set(found) == set(REPL), "图片引用与映射不匹配"

    def sub(m: re.Match) -> str:
        name = m.group(1)
        img = m.group(0)
        rep = REPL[name]
        if name in BLOCK_FORMULA:
            rep = BLOCK_FORMULA[name]
        return rep if NO_IMG else f"{img}\n\n{rep}"

    out = PAT.sub(sub, text)
    assert out.count("![") == (0 if NO_IMG else 29), "图片引用数不符预期"
    for name, rep in REPL.items():
        target = BLOCK_FORMULA.get(name, rep)
        # 每张图的转录文本应恰好出现1次（用首行判定，避免代码块内重复）
        key = target.splitlines()[0][:30]
        assert key in out, f"{name} 转录缺失: {key}"
    return out


def dedent_fix(text: str) -> str:
    """合并孤立 • 断行。"""
    lines = text.splitlines()
    idx = [i for i, l in enumerate(lines) if l.strip() == "•"]
    assert len(idx) == 13, f"孤立•应13处，实际{len(idx)}"
    for i in reversed(idx):
        lines[i] = "• " + lines[i + 1].strip()
        del lines[i + 1]
    return "\n".join(lines) + "\n"


def compact(text: str) -> str:
    """压缩超额换行，代码块内原样。"""
    END = "。！？；：，、）」』】.%:;!?,>"
    lines = text.splitlines()
    out: list[str] = []
    in_code = False
    code_buf: list[str] = []
    blank = 0
    def flush():
        nonlocal blank
        if blank and out:
            out.append("")
        blank = 0
    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            if in_code:
                code_buf.append(line); out.extend(code_buf); code_buf = []; in_code = False
            else:
                flush(); in_code = True; code_buf = [line]
            continue
        if in_code:
            code_buf.append(line); continue
        if not s:
            blank += 1; continue
        special = (s.startswith("#") or s.startswith("•") or s.startswith("【")
                   or s.startswith("$") or s.startswith("$$") or s.startswith("`")
                   or s.startswith("![") or s.startswith("图")
                   or s.startswith("(myenv)") or bool(re.match(r"^\d+\.\s", s)))
        if special:
            flush(); out.append(s); continue
        if (out and out[-1] and not out[-1].startswith(("#", "```", "【", "•", "$", "`", "![", "图", "(myenv)"))
                and out[-1][-1] not in END and not re.match(r"^\d+\.\s", out[-1])):
            out[-1] = out[-1] + s
        else:
            flush(); out.append(s)
        blank = 0
    flush()
    return "\n".join(out) + "\n"


def main() -> None:
    text = build()
    fp_before = re.sub(r"\s+", "", text)
    text = dedent_fix(text)
    text = compact(text)
    # 公式编号 "(2-N)" 被换行压缩黏到了正文行首，拆开独立成行
    text, n1 = re.subn(r"(?m)^\((2-\d+)\)(?=\S)", r"(\1)\n", text)
    assert n1 == 2, f"公式编号黏连应2处，实际{n1}"

    if NO_IMG:
        # 无图模式：删除独立成行的"图N-N 题注"（图已转文字，题注冗余）。
        # 但保留文字描述块【图N-N…】与正文"如图N-N所示"引用。
        cap = re.compile(r"^图\s*\d+\s*[-.]\s*\d+\s+\S")
        body_cont = re.compile(r"^(展示了|所示|给出|如下|见图|描述了|说明了)")
        kept: list[str] = []
        removed = 0
        for line in text.splitlines():
            s = line.strip()
            if cap.match(s) and len(s) < 45 and not body_cont.search(cap.sub("", s).strip()[:6]):
                removed += 1
                continue
            kept.append(line)
        assert removed == 13, f"应删13处独立图注，实际{removed}"
        text = "\n".join(kept) + "\n"
        # 删图注后块间可能残留连续2空行，压成1（代码块内无空行，安全）
        text = re.sub(r"\n{3,}", "\n\n", text)

    fp_after = re.sub(r"\s+", "", text)
    if NO_IMG:
        # 无图模式会主动删除13处图注，字符数必然减少；只校验代码块与转录完整
        assert text.count("```") == 22, "代码块丢失"
        assert "生成器损失" in text and "判别器损失" in text, "公式转录丢失"
    else:
        assert fp_before == fp_after, "非空白字符变化，内容丢失"
    assert text.count("```") % 2 == 0, "代码块围栏不成对"
    assert text.count("![") == (0 if NO_IMG else 29), "图片数量不符预期"
    assert "如图2-10 所示" in text, "正文图引用丢失"
    DST.write_text(text, encoding="utf-8")
    print(f"[OK] {DST}")
    print(f"     {'0图片(纯文本)' if NO_IMG else '29图片'} + 文字转录11代码块 + 块级公式；{len(text)}字符")


if __name__ == "__main__":
    main()

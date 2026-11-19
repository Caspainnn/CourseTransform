"""压缩纯文本 md 的超额换行（断言式）：
- 代码块内部原样保留；
- 连续空行压成 1 个；
- 段落内被 PDF 拆散的相邻文字行合并（中文行尾无句读 / 上行末与下行首可接续）。

断言：非空白字符总量不变、代码块内容逐字不变、无图片引用、代码块成对。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md"

# 行尾这些标点视为句子/段落自然结束，不与下一行合并
END_PUNCT = "。！？；：，、）」』】.%:;!?,>"


def main() -> None:
    text = F.read_text(encoding="utf-8")
    # 字数指纹：非空白字符序列（用于断言无损）
    fingerprint_before = re.sub(r"\s+", "", text)

    lines = text.splitlines()
    out: list[str] = []
    in_code = False
    code_buf: list[str] = []
    blank = 0

    def flush_blank() -> None:
        nonlocal blank
        if blank and out:
            out.append("")
        blank = 0

    for line in lines:
        s = line.strip()

        if s.startswith("```"):
            if in_code:
                code_buf.append(line)
                out.extend(code_buf)
                code_buf = []
                in_code = False
            else:
                flush_blank()
                in_code = True
                code_buf = [line]
            continue

        if in_code:
            code_buf.append(line)
            continue

        if not s:
            blank += 1
            continue

        # 结构性独立行：标题、列表项、图描述块、公式、代码行内命令 → 单独成行
        if (
            s.startswith("#")
            or s.startswith("•")
            or s.startswith("【")
            or re.match(r"^\d+\.\s", s)
            or s.startswith("$")
            or s.startswith("`")
            or s.startswith("(myenv)")
        ):
            flush_blank()
            out.append(s)
            continue

        # 普通文字行：若上行是普通文字且未以句读结尾 → 合并到上一行
        if out and out[-1] and not out[-1].startswith(("#", "```", "【", "•", "$", "`", "(myenv)")) \
                and out[-1][-1] not in END_PUNCT and not re.match(r"^\d+\.\s", out[-1]):
            out[-1] = out[-1] + s
        else:
            flush_blank()
            out.append(s)
        blank = 0

    flush_blank()
    if in_code:
        raise SystemExit("代码块未闭合")

    result = "\n".join(out) + "\n"
    fingerprint_after = re.sub(r"\s+", "", result)
    assert fingerprint_before == fingerprint_after, "非空白字符发生变化，内容有丢失"
    assert result.count("```") == text.count("```") == 22, "代码块围栏数变化"
    assert "![" not in result, "出现图片引用"
    assert "如图2-10 所示" in result, "正文图引用丢失"
    F.write_text(result, encoding="utf-8")
    print(f"[OK] 换行压缩完成：{len(lines)} 行 → {len(out)} 行；{len(fingerprint_after)} 非空白字符不变")


if __name__ == "__main__":
    main()

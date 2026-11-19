"""修复纯文本 md 的两处缺陷（断言式，任一不中整批不写盘）：
1) 孤立的 • 行与其下一行文字合并为 '• 文字'（PDF 文字块拆散所致）；
2) 删除图片转文字后重复的独立题注行'图N-N xxx'（保留正文中的"如图N-N所示"引用）。

题注识别：整行形如「图N-N 题名」或以「图 N-N 题名」开头，长度<45，且不以
「展示了/所示/给出/如」等正文措辞接续。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md"

# 独立题注行：图2-1 xxx / 图 2-6 xxx；排除"图2-3展示了…"这类正文
CAP = re.compile(r"^图\s*\d+\s*[-.]\s*\d+\s+\S")
# 只匹配编号后紧接的正文措辞，不匹配题名内部（如"迭代过程中"的"中"）
BODY_CONT = re.compile(r"^(展示了|所示|给出|如下|见图|描述了|说明了)")


def main() -> None:
    text = F.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- 修复1：合并孤立 • ---
    bullet_idx = [i for i, l in enumerate(lines) if l.strip() == "•"]
    assert len(bullet_idx) == 13, f"孤立•应为13处，实际 {len(bullet_idx)}"
    for i in reversed(bullet_idx):
        assert i + 1 < len(lines) and lines[i + 1].strip(), f"L{i+1} 的•后无文字"
        lines[i] = "• " + lines[i + 1].strip()
        del lines[i + 1]

    # --- 修复2：删除重复独立题注 ---
    caps: list[int] = []
    for i, l in enumerate(lines):
        s = l.strip()
        if CAP.match(s) and len(s) < 45:
            # 排除正文接续句（如"图2-3展示了…"）
            tail = CAP.sub("", s).strip()
            if BODY_CONT.search(tail[:6]):
                continue
            caps.append(i)
    assert len(caps) == 13, f"应删除13处独立题注，实际 {len(caps)}: {[lines[i].strip() for i in caps]}"
    for i in reversed(caps):
        del lines[i]

    out = "\n".join(lines) + "\n"
    assert "•\n" not in out, "仍有孤立•"
    assert out.count("```") % 2 == 0, "代码块围栏不成对"
    assert "如图2-10 所示" in out, "正文图引用不应被删"
    F.write_text(out, encoding="utf-8")
    print(f"[OK] 合并13处•断行，删除13处重复题注；{len(out)}字符")


if __name__ == "__main__":
    main()

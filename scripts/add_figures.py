"""把原 PDF 的架构图/流程图（指示图）以 md 图片链接形式插回纯文本版。

只插入结构性图示（原理结构图、流程图），不插代码截图与数据结果图——
后者已有代码块/表格承载。图片用相对路径 images/xxx.jpg，与 md 同目录。

每条插入带出现次数断言；任一不中整批不写盘。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md"

# (文字描述块的锚点行, 图片链接行)
FIGS = [
    ("【图2-1 生成对抗网络原理结构（文字描述）】",
     "![图2-1 生成对抗网络原理结构](images/image-0001.jpg)"),
    ("【图2-2 生成对抗网络程序流程图（文字描述）】",
     "![图2-2 生成对抗网络程序流程图](images/image-0004.jpg)"),
    ("【图2-3 生成对抗网络攻击流程图（文字描述）】",
     "![图2-3 生成对抗网络攻击流程图](images/image-0010.jpg)"),
    ("【图2-4 基于生成对抗网络的 sin 曲线模拟流程图（文字描述）】",
     "![图2-4 基于生成对抗网络的 sin 曲线模拟流程图](images/image-0011.jpg)"),
    ("【图2-6 基于生成对抗网络无数据替代训练的模型窃取实现流程图（文字描述）】",
     "![图2-6 基于生成对抗网络无数据替代训练的模型窃取实现流程图](images/image-0019.jpg)"),
    ("【图2-10 实践流程图（文字描述）】",
     "![图2-10 实践流程图](images/image-0028.jpg)"),
]


def main() -> None:
    text = F.read_text(encoding="utf-8")
    # 断言：每个锚点恰好1次；图片文件存在
    for anchor, link in FIGS:
        assert text.count(anchor) == 1, f"锚点应出现1次，实际 {text.count(anchor)}: {anchor[:30]}"
        img = link[link.index("images/"):-1]
        assert (ROOT / "output/book_faithful" / img).exists(), f"图片不存在: {img}"
    # 幂等：若已插入则跳过
    already = sum(1 for _, link in FIGS if link in text)
    if already == len(FIGS):
        print("[SKIP] 6张图示已存在，无需重复插入")
        return
    assert already == 0, f"部分图示已存在({already})，状态不一致"

    for anchor, link in FIGS:
        text = text.replace(anchor, f"{link}\n\n{anchor}", 1)

    assert text.count("![") == 6, f"应有6张图片链接，实际 {text.count('![')}"
    DST_WRITE(text)


def DST_WRITE(text: str) -> None:
    F.write_text(text, encoding="utf-8")
    print(f"[OK] 插入6张架构图/流程图链接；{len(text)}字符")


if __name__ == "__main__":
    main()

"""生成端到端演示用的仿真材料（《人工智能安全》风格）。

产物（demo_materials/）：
1. 教材md版/md/第2章.md        —— 模仿李剑《人工智能安全：原理与实践》第2章 GAN 内容的样例 md，
                                  含 ![](../images/第2章/image-00XX.jpg) 引用与"图2-1 …"题注；
   教材md版/images/第2章/*.jpg  —— 占位示意图（1x1 JPEG 字节）。
2. 第03学时_GAN生成sin曲线实践_教学设计_模板版.docx —— 九板块教学设计模板，
   含待填横线"教师：____"，教学过程表为 8 环节 × 6 列。

真实材料到位后可直接替换，无需修改代码。
"""
import base64
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo_materials"

CHAPTER2_MD = """# 第2章 生成对抗网络

## 2.1 生成对抗网络的基本原理

生成对抗网络（Generative Adversarial Network，GAN）由生成器（Generator）和判别器（Discriminator）两部分组成。生成器负责根据随机噪声生成尽可能逼真的样本，判别器负责判断输入样本是真实样本还是生成样本。两者在对抗训练中相互博弈：生成器努力生成更逼真的样本以欺骗判别器，判别器努力提高辨别能力，最终达到纳什均衡，生成器能够生成与真实数据分布一致的样本。

GAN 的训练目标可以表示为一个极小极大博弈问题。训练过程需要交替更新生成器与判别器的参数，训练的稳定性是实际应用中的关键问题。

![](../images/第2章/image-0001.jpg)

图2-1 生成对抗网络的原理结构图

如图2-1所示，真实样本与生成样本共同送入判别器，生成器不直接接触真实样本，而是通过判别器的梯度获得改进方向。

## 2.2 GAN实践：生成sin曲线

本节以"生成sin曲线"为任务，完整演示 GAN 的建模与训练过程。真实数据为一维 sin 函数采样点，生成器学习拟合该分布。

### 2.2.1 真实数据准备

在区间 [0, 2π] 上均匀采样，计算 sin 值作为真实样本，形成训练集。采样时需固定随机种子以保证实验可复现。

### 2.2.2 生成器与判别器设计

生成器与判别器均采用多层感知机实现。生成器输入一维随机噪声，输出一维伪样本；判别器输入一维样本，输出该样本为真实样本的概率。

### 2.2.3 对抗训练过程

训练时交替执行两个步骤：先固定生成器训练判别器，再固定判别器训练生成器。训练过程中记录两者的损失曲线，用于判断训练是否收敛。

![](../images/第2章/image-0002.jpg)

图2-2 对抗训练过程损失曲线示意图

### 2.2.4 训练结果核验

训练完成后，将生成样本与真实样本绘制在同一坐标系中对照。若生成样本能够覆盖 sin 曲线的主要形态，说明生成器学习到了目标分布。

![](../images/第2章/image-0003.jpg)

图2-3 生成样本与真实样本对照图

### 2.2.5 实践2-1 sin曲线样本模拟

本实践要求学生完成以下步骤：

1. 生成真实样本：在 [0, 2π] 区间采样并计算 sin 值；
2. 构建生成器与判别器网络；
3. 交替训练并记录损失；
4. 将生成样本与真实样本对照可视化。

实践中常见问题包括：训练不收敛、生成样本集中在个别区域、判别器过强导致梯度消失等。出现问题时可调整学习率、网络容量或训练轮次。

### 2.2.6 数据核验与对照分析

完成训练后需要对生成数据进行核验：检查生成样本的取值范围是否与真实样本一致、分布形态是否覆盖主要区段、是否存在异常离群点。核验通过后再进入结果分析环节。

## 2.3 本章小结

本章介绍了生成对抗网络的基本原理，并通过生成 sin 曲线的完整实践，掌握了真实数据准备、生成器与判别器设计、对抗训练、结果核验的完整流程。

## 习题

1. 简述生成对抗网络中生成器与判别器的对抗关系。
2. 结合实践2-1，说明如何通过损失曲线判断对抗训练是否收敛。
3. 生成样本核验时需要检查哪些方面？
"""

# 标准 1x1 JPEG（白底），作为占位示意图
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwcJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)

SECTIONS = [
    "2.2.5", "2.2.6",
]

PROCESS_STEPS = [
    "复习诊断", "数据核验", "更新示范", "短程运行",
    "对照分析", "汇报纠错", "达标检测", "小结迁移",
]

PROCESS_HEADER = ["环节", "时间", "主要内容", "教师活动", "学生活动", "设计意图"]


def set_run_font(run, name: str = "仿宋", size: int = 12) -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        from docx.oxml import OxmlElement
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), name)


def add_para(doc, text: str, size: int = 12, align=None) -> None:
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    set_run_font(run, size=size)


def add_heading(doc, text: str) -> None:
    p = doc.add_heading(text, level=1)
    for run in p.runs:
        set_run_font(run, name="黑体", size=14)


def make_table(doc, rows: list[list[str]], col_widths=None):
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = table.cell(r, c)
            cell.text = ""
            run = cell.paragraphs[0].add_run(text)
            set_run_font(run, size=10.5)
    return table


def make_chapter_md() -> None:
    md_path = OUT / "教材md版" / "md" / "第2章.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(CHAPTER2_MD, encoding="utf-8")
    img_dir = OUT / "教材md版" / "images" / "第2章"
    img_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2, 3):
        (img_dir / f"image-{i:04d}.jpg").write_bytes(TINY_JPEG)
    print(f"已生成 {md_path} 及 3 张占位图片")


def make_template_docx() -> None:
    doc = Document()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("《人工智能安全：原理与实践》教学设计（第03学时）")
    set_run_font(run, name="黑体", size=16)

    add_heading(doc, "一、课程基本信息")
    make_table(doc, [
        ["课程名称", "人工智能安全", "授课教师", "____"],
        ["授课章节", "第2章 生成对抗网络", "授课对象", "本科三年级"],
        ["课时安排", "1课时（45分钟）", "授课地点", "机房"],
    ])

    add_heading(doc, "二、教学目标")
    add_para(doc, "1. 知识目标：____")
    add_para(doc, "2. 能力目标：____")
    add_para(doc, "3. 素养目标：____")

    add_heading(doc, "三、教学重点与难点")
    make_table(doc, [
        ["教学重点", "____"],
        ["教学难点", "____"],
    ])

    add_heading(doc, "四、学情分析")
    add_para(doc, "____")

    add_heading(doc, "五、教学准备")
    add_para(doc, "____")

    add_heading(doc, "六、教学过程")
    rows = [PROCESS_HEADER] + [[step, "____", "____", "____", "____", "____"] for step in PROCESS_STEPS]
    make_table(doc, rows)

    add_heading(doc, "七、板书设计")
    make_table(doc, [
        ["板书主题", "____"],
        ["板书要点", "____"],
    ])

    add_heading(doc, "八、作业与思考题")
    add_para(doc, "1. ____")
    add_para(doc, "2. ____")

    add_heading(doc, "九、教学反思")
    make_table(doc, [
        ["成功之处", "____"],
        ["不足之处", "____"],
        ["改进措施", "____"],
    ])

    out = OUT / "第03学时_GAN生成sin曲线实践_教学设计_模板版.docx"
    doc.save(str(out))
    print(f"已生成 {out}")


if __name__ == "__main__":
    make_chapter_md()
    make_template_docx()

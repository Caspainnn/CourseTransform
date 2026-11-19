"""修复 demo_final 样板中「八、作业与思考题」第 2 题重复的问题。

中间稿块 18 塞入全部 4 题，块 19 又重复了第 2 题。
修法：把 4 题均分到模板的两个段落块（18 放 1/2，19 放 3/4），
然后从中间稿重新 export docx，不重跑模型、不改任何题目文字。

每条改写都带出现次数断言；任一不中则整批不写盘。
"""
from pathlib import Path

import yaml

from src.export_docx import export
from src.utils import read_text

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "output/demo_final/教案中间稿.md"
TPL = ROOT / "demo_materials/第03学时_GAN生成sin曲线实践_教学设计_模板版.docx"
OUT = ROOT / "output/demo_final/教案.docx"
CFG = ROOT / "config.yaml"

Q1 = "1. 简述生成对抗网络中生成器与判别器的对抗关系，并说明二者在训练中如何交替更新参数。"
Q2 = "2. 结合实践2-1，说明如何通过损失曲线判断对抗训练是否收敛；若出现训练不收敛或判别器过强导致梯度消失，可采取哪些调整措施。"
Q3 = "3. 生成样本核验时需要检查哪些方面？请结合sin曲线生成任务说明核验通过后再进入结果分析环节的理由。"
Q4 = "4. 思考：在生成sin曲线的实践中，为什么生成器不直接接触真实样本，而是通过判别器的梯度获得改进方向？这一设计对生成器学习目标分布有何意义？"

M18 = '<!-- CTBLOCK index=18 type=paragraph style="Normal" -->'
M19 = '<!-- CTBLOCK index=19 type=paragraph style="Normal" -->'

# 用块标记锚定整段，一次替换，避免「Q2 同时在两块出现」导致 replace 命中错位
OLD_SECTION = "\n".join([M18, Q1, Q2, Q3, Q4, "", M19, Q2, ""])
NEW_SECTION = "\n".join([M18, Q1, Q2, "", M19, Q3, Q4, ""])


def main() -> None:
    text = read_text(MD)

    assert text.count(OLD_SECTION) == 1, f"待修整段应恰好1次，实际 {text.count(OLD_SECTION)}"
    assert text.count(Q2) == 2, f"Q2 应恰好出现2次（块18内+块19重复），实际 {text.count(Q2)}"

    new = text.replace(OLD_SECTION, NEW_SECTION, 1)
    # 最终四题各出现1次，且块标记数量不变（22 块结构不变）
    for q in (Q1, Q2, Q3, Q4):
        assert new.count(q) == 1, f"最终 {q[:6]} 应只出现1次，实际 {new.count(q)}"
    assert new.count("<!-- CTBLOCK") == text.count("<!-- CTBLOCK"), "块数不得变化"

    # 全部断言通过才写盘
    MD.write_text(new, encoding="utf-8")
    cfg = yaml.safe_load(read_text(CFG))
    export(MD, TPL, OUT, cfg)
    print("[OK] 作业题去重完成，中间稿与 docx 已更新")


if __name__ == "__main__":
    main()

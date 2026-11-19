"""把纯文本 md 里被 PDF 压平的数据段转成标准 Markdown 表格（断言式）。

处理 5 处：
1) 表2-1 攻击手段分类（4 维度 × 各类别）
2) 表2-2 不同攻击的比较（Attack/ASR/Distance/Query）
3) 窃取结果 3 次实验数值
4) 分类报告（precision/recall/f1/support）
5) 混淆矩阵（2×2）

每条 old→new 带出现次数断言；任一不中整批不写盘。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md"

OLD_NEW = []

# ---- 1) 表2-1 ----
OLD_NEW.append((
"表2-1 基于GANs 的攻击手段分类欺骗模型提取模型拒绝服务隐私泄露数据投毒模型投毒按攻击目标分类基于生成对抗网络白盒攻击黑盒攻击灰盒攻击的攻击手段按攻击者知识分类按攻击阶段分类训练阶段攻击部署阶段攻击按攻击方式分类逃避攻击投毒攻击隐私攻击滥用攻击1．基于攻击目标的分类",
"""**表2-1 基于GANs 的攻击手段分类**

| 分类维度 | 类别 |
| --- | --- |
| 按攻击目标分类 | 欺骗模型、提取模型、拒绝服务、隐私泄露、数据投毒、模型投毒 |
| 按攻击者知识分类 | 白盒攻击、黑盒攻击、灰盒攻击 |
| 按攻击阶段分类 | 训练阶段攻击、部署阶段攻击 |
| 按攻击方式分类 | 逃避攻击、投毒攻击、隐私攻击、滥用攻击 |

1．基于攻击目标的分类"""))

# ---- 2) 表2-2 ----
OLD_NEW.append((
"表2-2 不同攻击的比较AttackASRDistanceQueryDaST-P96.83%\n4.79-GLS40.51%\n4.27297.07DaST-L98.35%\n4.72-Boundary100%\n4.69670.53表2-2 中，ASR：攻击成功率。Query：评估阶段的查询数。Boundary：基于决策的攻击。GLS：一种基于贪婪局部搜索的基于分数的黑盒攻击。-：表示DaST在评估阶段不需要查询。本实践中的DaST 使用FGSM 生成攻击。",
"""**表2-2 不同攻击的比较**

| Attack | ASR | Distance | Query |
| --- | --- | --- | --- |
| DaST-P | 96.83% | 4.79 | - |
| GLS | 40.51% | 4.27 | 297.07 |
| DaST-L | 98.35% | 4.72 | - |
| Boundary | 100% | 4.69 | 670.53 |

表2-2 中，ASR：攻击成功率。Query：评估阶段的查询数。Boundary：基于决策的攻击。GLS：一种基于贪婪局部搜索的基于分数的黑盒攻击。-：表示DaST在评估阶段不需要查询。本实践中的DaST 使用FGSM 生成攻击。"""))

# ---- 3) 窃取结果 ----
OLD_NEW.append((
"第 1 次：Accuracy of the network on net0: 91.20 %；Attack success rate: 42.76 %；l2 distance: 5.3174第 2 次：Accuracy of the network on net0: 94.45 %；Attack success rate: 40.05 %；l2 distance: 5.4083第 3 次：Accuracy of the network on net0: 94.30 %；Attack success rate: 42.20 %；l2 distance: 5.2722",
"""| 次数 | Accuracy on net0 | 攻击成功率 | L2 距离 |
| --- | --- | --- | --- |
| 第 1 次 | 91.20% | 42.76% | 5.3174 |
| 第 2 次 | 94.45% | 40.05% | 5.4083 |
| 第 3 次 | 94.30% | 42.20% | 5.2722 |"""))

# ---- 4) 分类报告 ----
OLD_NEW.append((
"【分类报告（文字转录）】\nROC AUC Score: 0.9257697149999999；AP Score: 0.9216430894417843Classification Report:\nprecision  recall  f1-score  support0            0.89     0.79      0.84    100001            0.81     0.90      0.85    10000accuracy                            0.84    20000macro avg       0.85     0.84      0.84    20000weighted avg    0.85     0.84      0.84    20000",
"""【分类报告】ROC AUC Score: 0.9257697149999999；AP Score: 0.9216430894417843

| 类别 | precision | recall | f1-score | support |
| --- | --- | --- | --- | --- |
| 0 | 0.89 | 0.79 | 0.84 | 10000 |
| 1 | 0.81 | 0.90 | 0.85 | 10000 |
| accuracy | | | 0.84 | 20000 |
| macro avg | 0.85 | 0.84 | 0.84 | 20000 |
| weighted avg | 0.85 | 0.84 | 0.84 | 20000 |"""))

# ---- 5) 混淆矩阵 ----
OLD_NEW.append((
"【混淆矩阵（文字描述）】二分类混淆矩阵，行表示真实标签、列表示预测标签：\n真实 Negative 中，预测 Negative 为 7922、预测 Positive 为 2078；\n真实 Positive 中，预测 Negative 为 1025、预测 Positive 为 8975。",
"""【混淆矩阵】行表示真实标签、列表示预测标签：

| 真实 \\ 预测 | Negative | Positive |
| --- | --- | --- |
| Negative | 7922 | 2078 |
| Positive | 1025 | 8975 |"""))


def main() -> None:
    text = F.read_text(encoding="utf-8")
    # 逐条断言出现次数
    for old, new in OLD_NEW:
        n = text.count(old)
        assert n == 1, f"待替换段应出现1次，实际{n}: {old[:40]}"
    for old, new in OLD_NEW:
        text = text.replace(old, new, 1)

    assert "![" not in text, "出现图片引用"
    assert text.count("```") % 2 == 0, "代码块围栏不成对"
    # 表格行数（以 | 开头）
    tbl_lines = sum(1 for l in text.splitlines() if l.strip().startswith("|"))
    assert tbl_lines >= 25, f"表格行应≥25，实际{tbl_lines}"
    F.write_text(text, encoding="utf-8")
    print(f"[OK] 5处数据转表格；表格行 {tbl_lines}；{len(text)}字符")


if __name__ == "__main__":
    main()

"""把忠实抽取 md 里的图片引用替换为文本内容（纯文本、无图片）。

代码截图 → ```python 代码块；公式 → LaTeX；流程图/架构图 → 文字步骤描述；
数据结果图 → 关键数值与结论的文字描述。图注行保留。

每条替换带出现次数断言；任一不中则整批不写盘。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护.md"
DST = ROOT / "output/book_faithful/第2章_生成对抗网络攻击与防护_纯文本.md"

# 图片文件名 → 替换文本（不含图注行，图注在 md 里独立保留）
REPL = {
    # === 图2-1 GAN 原理结构 ===
    "image-0001.jpg": (
        "【图2-1 生成对抗网络原理结构（文字描述）】\n"
        "随机向量 Z 输入生成器 G，生成器由多层神经网络构成，输出假数据 X假；"
        "真实数据 X真 与假数据 X假 一同送入判别器 D，判别器亦为多层神经网络，"
        "最终输出二分类判断「真」或「假」。生成器与判别器通过对抗训练，"
        "使生成器生成的假数据在分布上逐渐接近真实数据。"
    ),
    # === 公式 ===
    "image-0002.jpg": "判别器损失：$L_D=-\\mathbb{E}_{x\\sim p_{data}(x)}[\\log D(x)]-\\mathbb{E}_{z\\sim p_z(z)}[\\log(1-D(G(z)))]$",
    "image-0003.jpg": "生成器损失：$L_G=-\\mathbb{E}_{z\\sim p_z(z)}[\\log D(G(z))]$",
    # === 图2-2 训练流程 ===
    "image-0004.jpg": (
        "【图2-2 生成对抗网络程序流程图（文字描述）】\n"
        "1. 开始；\n"
        "2. 初始化生成器和判别器的模型参数；\n"
        "3. 判断是否满足训练迭代次数：满足则结束，否则继续；\n"
        "4. 从随机噪声中采样生成器的输入向量；\n"
        "5. 使用生成器生成一批伪样本；\n"
        "6. 使用判别器对真实样本和伪造样本进行分类；\n"
        "7. 计算判别器的分类损失，并更新判别器参数，以提高对真实与伪造样本的区分能力；\n"
        "8. 固定判别器参数，使用生成器生成新的伪造样本；\n"
        "9. 使用判别器对新伪造样本分类，计算生成器的生成损失并更新生成器参数，提高生成样本质量；\n"
        "10. 判断是否满足观察迭代次数：满足则绘制真实样本与伪造样本的对比散点图；\n"
        "11. 返回步骤 3 继续训练循环。"
    ),
    # === 对抗样本约束公式及说明（原被误渲染为图）===
    "image-0005.jpg": (
        "对抗样本的生成需满足约束条件（公式2-3）：\n"
        "$x_{adv}=x+G(z),\\ \\mathrm{s.t.}\\ \\|G(z)\\|_p\\leq\\epsilon\\ \\mathrm{且}\\ f(x_{adv})\\neq f(x)$\n"
        "其中：G(z) 表示生成器输出的扰动，需满足 $L_p$-范数约束（如 $\\|G(z)\\|_\\infty\\leq\\epsilon$）；"
        "ε 为扰动幅度的最大允许阈值，确保扰动不可察觉；f 为目标分类模型，对抗样本需使其输出错误预测。"
    ),
    # === 图2-3 攻击流程 ===
    "image-0009.jpg": (
        "【图2-3 对抗攻击流程（文字描述·输入与扰动生成）】\n"
        "潜在空间向量 z 输入生成器 G，生成扰动 δ；原始干净样本 x 与扰动 δ 相加得到对抗样本 x_adv。"
    ),
    "image-0010.jpg": (
        "【图2-3 生成对抗网络攻击流程图（文字描述）】\n"
        "1. 潜在空间向量 z 输入生成器 G，生成扰动 δ；\n"
        "2. 原始干净样本 x 与扰动 δ 相加（叠加）生成对抗样本 x_adv；\n"
        "3. 真实样本与对抗样本一同送入判别器 D，输出判别结果；\n"
        "4. 判别结果反馈给生成器 G，形成反馈闭环，持续优化扰动生成过程，"
        "使输出扰动以高隐蔽性欺骗目标模型。"
    ),
    # === 图2-4 sin 模拟流程 ===
    "image-0011.jpg": (
        "【图2-4 基于生成对抗网络的 sin 曲线模拟流程图（文字描述）】\n"
        "1. 开始；\n"
        "2. 导入必要的库和函数；\n"
        "3. 定义生成器和判别器模型；\n"
        "4. 定义一个完整的生成对抗网络模型，将生成器和判别器依次添加到模型中；\n"
        "5. 进入 n_epochs 次训练循环：生成真实样本与生成器生成的伪造样本；\n"
        "6. 更新判别器权重，训练生成器；\n"
        "7. 判断是否达到评估轮次：否 → 返回训练循环；是 → 计算判别器准确率、绘制样本分布图；\n"
        "8. 完成所有轮次后结束。"
    ),
    # === 2.4 节代码 ===
    "image-0012.jpg": (
        "```python\n"
        "# 定义一个独立的生成器模型\n"
        "def define_generator(latent_dim, n_outputs=2):\n"
        "    model = Sequential()\n"
        "    model.add(Dense(30, activation='relu', kernel_initializer='he_uniform', input_dim=latent_dim))\n"
        "    model.add(Dense(n_outputs, activation='linear'))\n"
        "    return model\n"
        "```"
    ),
    "image-0013.jpg": (
        "```python\n"
        "# 定义一个独立的判别器模型\n"
        "def define_discriminator(n_inputs=2):\n"
        "    model = Sequential()\n"
        "    model.add(Dense(50, activation='relu', kernel_initializer='he_uniform', input_dim=n_inputs))\n"
        "    model.add(Dense(1, activation='sigmoid'))\n"
        "    # 编译模型\n"
        "    model.compile(loss='binary_crossentropy', optimizer='adam', metrics=['accuracy'])\n"
        "    return model\n"
        "```"
    ),
    "image-0014.jpg": (
        "```python\n"
        "# 定义一个完整的生成对抗网络模型，即组合的生成器和判别器模型，以更新生成器\n"
        "def define_gan(generator, discriminator):\n"
        "    # 锁定判别器的权重，使它们不参与训练\n"
        "    discriminator.trainable = False\n"
        "    model = Sequential()\n"
        "    # 添加生成器\n"
        "    model.add(generator)\n"
        "    # 添加判别器\n"
        "    model.add(discriminator)\n"
        "    # 编译生成对抗网络模型\n"
        "    model.compile(loss='binary_crossentropy', optimizer='adam')\n"
        "    return model\n"
        "```"
    ),
    "image-0015.jpg": (
        "```python\n"
        "# 评估判别器并绘制真实样本和伪造样本\n"
        "def summarize_performance(epoch, generator, discriminator, latent_dim, n=100):\n"
        "    x_real, y_real = generate_real_samples(n)\n"
        "    _, acc_real = discriminator.evaluate(x_real, y_real, verbose=0)\n"
        "    x_fake, y_fake = generate_fake_samples(generator, latent_dim, n)\n"
        "    _, acc_fake = discriminator.evaluate(x_fake, y_fake, verbose=0)\n"
        "    print(acc_fake, acc_real, acc_fake)\n"
        "    pyplot.scatter(x_real[:, 0], x_real[:, 1], color='red')\n"
        "    pyplot.scatter(x_fake[:, 0], x_fake[:, 1], color='blue')\n"
        "    filename = 'E:/Learning/test_1/GANResult_2/generated_plot_e%03d.png' % (epoch + 1)\n"
        "    pyplot.savefig(filename)\n"
        "    pyplot.close()\n"
        "```"
    ),
    "image-0016.jpg": (
        "```python\n"
        "# 训练生成对抗网络\n"
        "def train(g_model, d_model, gan_model, latent_dim, n_epochs=8000, n_batch=128, n_eval=2000):\n"
        "    half_batch = int(n_batch / 2)\n"
        "    for i in range(n_epochs):\n"
        "        x_real, y_real = generate_real_samples(half_batch)\n"
        "        x_fake, y_fake = generate_fake_samples(g_model, latent_dim, half_batch)\n"
        "        # 更新判别器\n"
        "        discriminator.trainable = True  # 仅较新 Keras 版本必须添加\n"
        "        d_model.train_on_batch(x_real, y_real)\n"
        "        d_model.train_on_batch(x_fake, y_fake)\n"
        "        discriminator.trainable = False  # 仅较新 Keras 版本必须添加\n"
        "        x_gan = generate_latent_points(latent_dim, n_batch)\n"
        "        y_gan = ones((n_batch, 1))\n"
        "        gan_model.train_on_batch(x_gan, y_gan)\n"
        "        if (i + 1) % n_eval == 0:\n"
        "            summarize_performance(i, g_model, d_model, latent_dim)\n"
        "```"
    ),
    # === 训练输出/散点图 ===
    "image-0017.jpg": "训练输出示例（第 7999 次迭代）：`7999  0.5299999713897705  0.5799999833106995`（依次为迭代次数、真实样本判别准确率、伪造样本判别准确率）。",
    "image-0018.jpg": (
        "【图2-5 迭代过程中生成的散点图（文字描述）】\n"
        "四幅子图分别对应 2000、4000、6000、8000 次迭代，横轴为样本第一维取值，纵轴为第二维取值，"
        "红色为真实样本、蓝色为生成样本。随着迭代次数增加，蓝色生成点逐渐覆盖红色真实点的分布形态，"
        "到第 8000 次迭代时两者高度重合，表明生成器已恢复训练数据分布。"
    ),
    # === 图2-6 模型窃取流程 ===
    "image-0019.jpg": (
        "【图2-6 基于生成对抗网络无数据替代训练的模型窃取实现流程图（文字描述）】\n"
        "训练阶段：命令行执行程序 → 解析命令行参数 → 构建三个神经网络模型 Net_s、Net_m、Net_l → 选择模型 → "
        "初始化配置（CUDA、日志、Excel）→ 加载 MNIST 数据集并预处理 → 配置 DataLoader 批处理 → 模型移至 GPU → "
        "前向传播/反向传播优化 → 生成对抗样本 → 评估分类准确率 → 计算攻击成功率 → 记录结果到 Excel → "
        "判断是否为最佳模型：是则保存模型参数、清理内存、进入下一轮训练迭代；否则直接清理内存继续。\n"
        "测试阶段：开始 → 设置随机种子 → 加载测试数据集 → 选择对抗攻击方法 → 加载预训练模型 → "
        "生成对抗样本测试 → 计算最终攻击成功率 → 结束。"
    ),
    # === 2.5 节代码 ===
    "image-0020.jpg": (
        "```python\n"
        "class Net_s(nn.Module):\n"
        "    def __init__(self):\n"
        "        super(Net_s, self).__init__()\n"
        "        self.conv1 = nn.Conv2d(1, 20, 5, 1)\n"
        "        self.conv2 = nn.Conv2d(20, 50, 5, 1)\n"
        "        self.fc1 = nn.Linear(4*4*50, 500)\n"
        "        self.fc2 = nn.Linear(500, 10)\n"
        "\n"
        "    def forward(self, x):\n"
        "        x = F.relu(self.conv1(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv2(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = x.view(-1, 4*4*50)\n"
        "        x = F.relu(self.fc1(x))\n"
        "        x = self.fc2(x)\n"
        "        return F.log_softmax(x, dim=1)\n"
        "```"
    ),
    "image-0021.jpg": (
        "```python\n"
        "class Net_m(nn.Module):\n"
        "    def __init__(self):\n"
        "        self.number = 0\n"
        "        super(Net_m, self).__init__()\n"
        "        self.conv1 = nn.Conv2d(1, 20, 5, 1)\n"
        "        self.conv2 = nn.Conv2d(20, 50, 5, 1)\n"
        "        self.conv3 = nn.Conv2d(50, 50, 3, 1)\n"
        "        self.fc1 = nn.Linear(2*2*50, 500)\n"
        "        self.fc2 = nn.Linear(500, 10)\n"
        "\n"
        "    def forward(self, x, sign=0):\n"
        "        if sign == 0:\n"
        "            self.number += 1\n"
        "        x = F.relu(self.conv1(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv2(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv3(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = x.view(-1, 2*2*50)\n"
        "        x = F.relu(self.fc1(x))\n"
        "        x = self.fc2(x)\n"
        "        return F.log_softmax(x, dim=1)\n"
        "\n"
        "    def get_number(self):\n"
        "        return self.number\n"
        "```"
    ),
    "image-0022.jpg": (
        "```python\n"
        "class Net_l(nn.Module):\n"
        "    def __init__(self):\n"
        "        super(Net_l, self).__init__()\n"
        "        self.conv1 = nn.Conv2d(1, 20, 5, 1)\n"
        "        self.conv2 = nn.Conv2d(20, 50, 5, 1)\n"
        "        self.conv3 = nn.Conv2d(50, 50, 3, 1)\n"
        "        self.conv4 = nn.Conv2d(50, 50, 3, 1)\n"
        "        self.fc1 = nn.Linear(50, 500)\n"
        "        self.fc2 = nn.Linear(500, 10)\n"
        "\n"
        "    def forward(self, x):\n"
        "        x = F.relu(self.conv1(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv2(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv3(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = F.relu(self.conv4(x))\n"
        "        x = F.max_pool2d(x, 2, 2)\n"
        "        x = x.view(-1, 50)\n"
        "        x = F.relu(self.fc1(x))\n"
        "        x = self.fc2(x)\n"
        "        return x\n"
        "```"
    ),
    "image-0023.jpg": (
        "```python\n"
        "criterion = nn.CrossEntropyLoss()\n"
        "criterion_max = nn.CrossEntropyLoss()\n"
        "\n"
        "# setup optimizer\n"
        "optimizer0 = optim.Adam(net0.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))\n"
        "# optimizer0 = optim.SGD(net0.parameters(), lr=opt.lr, momentum=0.9, weight_decay=5e-4)\n"
        "optimizer6 = optim.Adam(net6.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))\n"
        "# optimizer6 = optim.SGD(net6.parameters(), lr=opt.lr, momentum=0.9, weight_decay=5e-4)\n"
        "optimizer_block = []\n"
        "for i in range(10):\n"
        "    optimizer_block.append(optim.Adam(pre_conv_block[i].parameters(), lr=opt.lr, betas=(opt.beta1, 0.999)))\n"
        "\n"
        "with torch.no_grad():\n"
        "    total_net0 = 0.0\n"
        "    total = 0\n"
        "    net0.eval()\n"
        "    for data in testloader:\n"
        "        inputs, labels = data\n"
        "        inputs = inputs.cuda()\n"
        "        labels = labels.cuda()\n"
        "        outputs = net0(inputs)\n"
        "        if opt.dataset == 'azure':\n"
        "            predicted = cal_azure(uf, inputs)\n"
        "        else:\n"
        "            outputs = original_net(inputs)\n"
        "            _, predicted = torch.max(outputs.data, 1)\n"
        "        total += labels.size(0)\n"
        "        correct_net0 += (predicted == labels).sum()\n"
        "print('Accuracy of the network on net0: %.2f %%' % (100. * correct_net0.float() / total))\n"
        "```"
    ),
    "image-0024.jpg": (
        "```python\n"
        "def test_adv(net, tar_net, attack, target):\n"
        "    net.eval()\n"
        "    tar_net.eval()\n"
        "    # BIM\n"
        "    if attack == 'BIM':\n"
        "        adversary = BasicIterativeAttack(\n"
        "            net,\n"
        "            loss_fn=nn.CrossEntropyLoss(reduction='sum'),\n"
        "            eps=0.25,\n"
        "            nb_iter=20, eps_iter=0.01, clip_min=0.0, clip_max=1.0,\n"
        "            targeted=opt.target)\n"
        "    # 目标攻击\n"
        "    elif attack == 'PGD':\n"
        "        if opt.target:\n"
        "            # 定义PGD攻击器，使用交叉熵损失函数，最大扰动0.25，迭代次数11，每次迭代的梯度步长0.03，最小值0.0，最大值1.0，目标攻击\n"
        "            adversary = PGDattack(\n"
        "                net,\n"
        "                loss_fn=nn.CrossEntropyLoss(reduction='sum'),\n"
        "                eps=0.25,\n"
        "                nb_iter=11, eps_iter=0.03, clip_min=0.0, clip_max=1.0,\n"
        "                targeted=opt.target)\n"
        "        else:\n"
        "            # 定义PGD攻击器，使用交叉熵损失函数，最大扰动0.25，迭代次数6，每次迭代的梯度步长0.03，最小值0.0，最大值1.0，非目标攻击\n"
        "            adversary = PGDattack(\n"
        "                net,\n"
        "                loss_fn=nn.CrossEntropyLoss(reduction='sum'),\n"
        "                eps=0.25,\n"
        "                nb_iter=6, eps_iter=0.03, clip_min=0.0, clip_max=1.0,\n"
        "                targeted=opt.target)\n"
        "    # FGSM\n"
        "    elif attack == 'FGSM':\n"
        "        # 定义一个快速梯度符号攻击，使用交叉熵损失函数，eps=0.26，targeted根据opt.target决定是否为目标攻击\n"
        "        adversary = GradientSignAttack(\n"
        "            net,\n"
        "            loss_fn=nn.CrossEntropyLoss(reduction='sum'),\n"
        "            eps=0.26,\n"
        "            targeted=opt.target)\n"
        "    elif attack == 'CW':\n"
        "        adversary = CarliniAndWagnerAttack(\n"
        "            net,\n"
        "            num_classes=10,\n"
        "            learning_rate=0.45,\n"
        "            # loss=nn.CrossEntropyLoss(reduction='sum'),\n"
        "            binary_search_steps=10,\n"
        "            max_iterations=12,\n"
        "            targeted=opt.target)\n"
        "```"
    ),
    # === 2.5 结果 ===
    "image-0025.jpg": (
        "【运行结果（文字描述）】训练迭代至 779/2000 轮，输出 D_prob、G、D(G(z))、loss_imitate、loss_diversity 等指标；"
        "网络在 net0 上的准确率为 95.15%，为当前最佳模型。"
    ),
    "image-0026.jpg": (
        "【模型准确率和攻击成功率（文字描述）】训练迭代至 674/2000 轮，截图所示网络准确率为 96.26%（原文图示）。"
    ),
    "image-0027.jpg": (
        "【窃取结果（命令输出转录）】\n"
        "`(myenv) PS D:\\Aisecurity\\DaST-master> python evaluation.py --mode=dast --adv=FGSM`\n"
        "第 1 次：Accuracy of the network on net0: 91.20 %；Attack success rate: 42.76 %；l2 distance: 5.3174\n"
        "第 2 次：Accuracy of the network on net0: 94.45 %；Attack success rate: 40.05 %；l2 distance: 5.4083\n"
        "第 3 次：Accuracy of the network on net0: 94.30 %；Attack success rate: 42.20 %；l2 distance: 5.2722"
    ),
    # === 图2-10 流程 ===
    "image-0028.jpg": (
        "【图2-10 实践流程图（文字描述）】\n"
        "开始 → 导入必要的库和模块 → 构建一个 DenseNet 模型 → 数据预处理 → 模型训练和保存 → "
        "可视化训练过程 → 模型评估，并可视化评估结果 → 结束。"
    ),
    # === 2.7 代码与结果 ===
    "image-0029.jpg": (
        "```python\n"
        "def build_model(pretrained):\n"
        "    model = Sequential([\n"
        "        pretrained,\n"
        "        layers.GlobalAveragePooling2D(),\n"
        "        layers.Dense(1, activation='sigmoid')\n"
        "    ])\n"
        "\n"
        "    model.compile(\n"
        "        loss='binary_crossentropy',\n"
        "        optimizer=Adam(learning_rate=0.0001),\n"
        "        metrics=['accuracy']\n"
        "    )\n"
        "```"
    ),
    "image-0030.jpg": (
        "【分类报告（文字转录）】\n"
        "ROC AUC Score: 0.9257697149999999；AP Score: 0.9216430894417843\n"
        "Classification Report:\n"
        "              precision  recall  f1-score  support\n"
        "     0            0.89     0.79      0.84    10000\n"
        "     1            0.81     0.90      0.85    10000\n"
        " accuracy                            0.84    20000\n"
        "macro avg       0.85     0.84      0.84    20000\n"
        "weighted avg    0.85     0.84      0.84    20000"
    ),
    "image-0031.jpg": (
        "【混淆矩阵（文字描述）】二分类混淆矩阵，行表示真实标签、列表示预测标签：\n"
        "真实 Negative 中，预测 Negative 为 7922、预测 Positive 为 2078；\n"
        "真实 Positive 中，预测 Negative 为 1025、预测 Positive 为 8975。"
    ),
    "image-0032.jpg": (
        "【图2-13 ROC 曲线（文字描述）】横轴为 False Positive Rate（0–1），纵轴为 True Positive Rate（0–1）；"
        "曲线自原点快速上升，在假正率约 0.2 时真正率已接近 0.9，随后趋近左上角，"
        "表明模型具有较好的区分能力（对应 AUC 约 0.926）。"
    ),
}

PAT = re.compile(r"!\[[^\]]*\]\(images/([^)]+)\)")


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    found = PAT.findall(text)
    print(f"md 中图片引用数: {len(found)}")
    missing = set(found) - set(REPL)
    extra = set(REPL) - set(found)
    if missing:
        raise SystemExit(f"以下图片无替换文本: {sorted(missing)}")
    if extra:
        raise SystemExit(f"以下替换项未在 md 中找到: {sorted(extra)}")
    # 每张图出现次数断言：均为 1
    for name in found:
        n = len(re.findall(r"!\[[^\]]*\]\(images/" + re.escape(name) + r"\)", text))
        assert n == 1, f"{name} 应出现1次，实际 {n}"

    def sub(m: re.Match) -> str:
        return REPL[m.group(1)]

    out = PAT.sub(sub, text)
    assert "![" not in out, "替换后仍有图片引用"
    # 代码块成对
    assert out.count("```") % 2 == 0, "代码块围栏不成对"
    DST.write_text(out, encoding="utf-8")
    print(f"[OK] 已写出: {DST}")
    print(f"     {len(found)} 张图 → 文本；代码块 {out.count('```')//2} 个；{len(out)} 字符")


if __name__ == "__main__":
    main()

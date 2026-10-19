# ml/ · 算式验证码识别模型（已完工）

> 本项目已交付：训练完成、ONNX 导出、独立测试集评估完成。
> 想了解来龙去脉与踩过的坑，见 [`AGENT_BRIEF.md`](AGENT_BRIEF.md)。
> **第二轮任务（压错答率）见 [`TASK_ROUND2.md`](TASK_ROUND2.md)。**

本目录是**离线训练**工程，与运行时隔离：`campuslink/` 里跑训练好的 ONNX，
本目录的代码不进安装包、不进 `requirements.txt`。

---

## 一、最终结论

| 指标 | 结果 | 验收线 | 判定 |
|---|---|---|---|
| 最终答案正确率（独立测试集 116 张） | **92.2%**（107/116） | ≥ 92% | ✅ 达标（余量很小） |
| 错误答率（会提交错答案） | **6.9%**（8/116） | ≤ 3% | ❌ **未达标** |
| 空/无效输出（安全，可换图） | 0.9%（1/116） | — | — |

**一句话判断**：识别能力已经够用，但"宁可不认、不能认错"这条还没守住。
上线前建议加**置信度阈值**（低于阈值返回空串让程序换图），把部分错答转成换图 ——
这是本阶段唯一的遗留项，详见第六节。

模型文件：

```
ml/models/crnn_math.onnx              21.2 MB   ← 运行时要用的
ml/models/crnn_math.pt                最优权重（val_exact 82.6%，最后一次 300 轮训练）
ml/models/crnn_math_ep234_83.5.pt     另一轮的最优权重（val_exact 83.5%，分数更高）
ml/models/train_full_log.txt          300 轮完整训练日志
```

> 注意：`crnn_math.onnx` 导出的是 `crnn_math_pt`（82.6%）那个。若要换成 83.5% 的权重，
> 用 `python ml/scripts/export_onnx.py --ckpt ml/models/crnn_math_ep234_83.5.pt`。

---

## 二、数据现状

| 项 | 数量 | 位置 |
|---|---|---|
| 训练用原图 | **3164 张** | `dataset/raw/` |
| 训练用有效标注 | **3071 条** | `dataset/labels/labels.csv` |
| 独立测试原图 | **120 张** | `dataset/test_raw/` |
| 测试集有效标注 | **116 条**（4 张模糊弃标） | `dataset/test_labels.csv` |

- 训练/测试**完全隔离**：测试集是后期新采的，从未参与训练
- 标注格式 `A<运算符>B`，运算符统一汉字，不带 `=?`
- 标注经**双人独立标注 + 分歧人工裁定 + 全量校对**，累计修正约 **194 处**错标

---

## 三、怎么复现

```bash
cd /home/anan/ubuntufile/wind/workbuddy/Project/2026-10-14-15-35-46/CampusLink
source .venv/bin/activate

# 训练（GPU 4060，约 2s/轮）
python ml/scripts/train_crnn.py --epochs 300 --batch 32 --lr 3e-3 --workers 4 \
       --sched-patience 30 --patience 60

# 导出 ONNX + 一致性复核
python ml/scripts/export_onnx.py

# 评估：只评估训练时留出的验证集（避免把训练图算进去）
python ml/scripts/evaluate.py --holdout
```

采样（需能访问校园网门户 `10.6.6.6`）：

```bash
# 训练数据（编号接着已有的往后排，--skip 必须等于现有最大编号+1）
python3 tools/captcha_sampler.py --count 1600 --skip 1564 --out ml/dataset/raw --interval 0.25
```

---

## 四、独立测试集怎么评（**别用错口径**）

`evaluate.py` 默认评估**全部已标注图**，其中大部分是训练用过的，分数会虚高
（实测虚高约 5~7 个点）。正确姿势：

```bash
# 口径 A：训练时留出的验证集（461 张，与训练同划分）
python ml/scripts/evaluate.py --holdout

# 口径 B：完全独立的新测试集（116 张，推荐作为上线依据）
python ml/scripts/evaluate.py \
    --raw ml/dataset/test_raw \
    --labels ml/dataset/test_labels.csv \
    --onnx ml/models/crnn_math.onnx
```

已知错误分布（8 个错答，**7 个是操作数数字错**）：

```
75减7  → 15减7      两位数 A 的首位被漏掉
93加7  →  9加7      同上
31加6  →  3加6      同上
22除2  → 12除2      同上
22加1  →  2加1      同上
61加6  →  6加6      同上
5乘7   →  1乘7      数字误读
2乘1   →  2乘7      B 位误读
```

**结论：运算符（加减乘除）几乎不再出错，瓶颈集中在"两位数 A 的首位"。**

---

## 五、关键工程决策（为什么这么做）

1. **强数据增强是转折点**
   初版只做亮度/对比度抖动，模型把训练集记住（train loss 0.0005）但验证集只有 5%。
   加入平移/±4° 旋转/合成穿字斜线/高斯噪声/随机擦除后，同样的数据从 **5% → 80%**。

2. **CTC 前期有"全 blank 平台期"，`ReduceLROnPlateau` 的 patience 不能太小**
   默认 patience=4 会在平台期把 lr 砍到 1/8，模型再也逃不出来。
   `--sched-patience 30` 是实测可用的值。

3. **300 轮跑满没有意义**
   lr 降到 2e-4 以下后验证集不再提升，train loss 继续降到 0.006 纯属过拟合。
   实测早停（patience 60）结果 83.5% ≥ 跑满 300 轮的 82.6%。**早停是对的选择。**

4. **标注质量是天花板，不是优化能补的**
   新批次首轮 AI 标注噪声率实测 **4.5%~14.7%**，包含"标注串行错位"（一张图拿到下一张的标签）这种
   致命错误。校对后模型上限明显抬高。**任何"再调调参"的方案都不如先把标签修干净。**

5. **汉字 `加` 最容易被误标**
   小尺寸下"口"像 0/8、"力"像除号偏旁，导致 `5加3` 被标成 `5除3`、
   `28除4` 被标成 `2加4`。校对时这类错误占比最高。

---

## 六、遗留项与下一步

**唯一未达标的验收项：错答率 6.9% > 3%。**

> **复盘结论（2026-10-19）**：下面原本第 1 条推荐的「加置信度阈值」**已实测证伪**，
> 详见 [`AGENT_BRIEF.md`](AGENT_BRIEF.md) 第 6 节。原因很简单 —— 8 个错答里有 4 个
> 置信度接近 1.000，模型对自己的错误零怀疑，阈值砍不掉它们，只会误伤正确样本。

推荐做法（按收益排序）：

1. **先换 83.5% 那个权重**（零成本）
   ```bash
   python ml/scripts/export_onnx.py --ckpt ml/models/crnn_math_ep234_83.5.pt
   python ml/scripts/evaluate.py --raw ml/dataset/test_raw --labels ml/dataset/test_labels.csv
   ```

2. **针对细笔画数字（`1`/`7`）补数据 / 加增强**
   双口径统计显示含 `1`/`7` 的样本错答率明显更高（测试集 11.7% vs 1.8%）。
   原文档写的"两位数 A 首位被漏"在 460 张留出集上**不成立**，别按那个方向补。

3. **提高输入高度**（`IMG_H=48/64`，需重训）
   原图 60 高压到 32，字符高约 16px，细笔画容易糊。

4. ~~加置信度阈值~~ —— **已证伪，不要再试**
   统计工具留在 `ml/scripts/conf_threshold.py`，换模型后可以重跑复核。

**另外**：116 张的 95% 置信区间约 ±5 个点，92.2% 属于"刚好压线"；
测试集复核时是看着模型输出做裁定的，存在轻微乐观偏差。要宣称稳定达标，
建议扩到 300~500 张且完全盲标。

---

## 七、目录

```
ml/
├── README.md                 本文件
├── AGENT_BRIEF.md            交接说明（背景 / 踩坑 / 硬约束）
├── requirements.txt          训练依赖
├── setup.sh                  一键建 venv（注意：系统缺 python3-venv 时需绕过）
├── dataset/
│   ├── raw/                  训练用原图 3164 张（不入库）
│   ├── test_raw/             独立测试原图 120 张
│   ├── test_labels.csv       独立测试集标注（116 条，权威）
│   └── labels/
│       ├── labels.csv        训练标注（3071 条，权威）
│       └── labels_auto.csv   早期预标注，仅参考
├── models/                   权重与 ONNX（不入库）
└── scripts/
    ├── common.py             字符集、预处理、CTC 编解码（不要改）
    ├── selftest.py           链路自检
    ├── label_web.py          Web 标注工具（纯标准库）
    ├── label_tool.py         tkinter 版（无桌面环境用不了）
    ├── autolabel.py          预标注（需 ddddocr，可选）
    ├── pack_dataset.py       打包数据集
    ├── train_crnn.py         训练 CRNN + CTC
    ├── export_onnx.py        导出 ONNX + 一致性复核
    ├── evaluate.py           评估（支持 --holdout 只评留出集）
    ├── conf_threshold.py     置信度阈值分析（结论：这条路走不通）
    └── error_profile.py      错答画像（按运算符/位数/细笔画切分）
```

---

## 八、字符集与两个关键细节

字符集 12 类：

```
CHARS = "0123456789加减乘除"
```

- `加`/`乘` 是**画在图上的汉字**，`÷`/`−` 是符号；训练时统一归一成汉字，
  推理后由 `campuslink.core.captcha_math` 换回符号求值。
- 不含 `=` `?`：尾部 `=?` 是固定标记，对求值无贡献，却是干扰 OCR 的最大元凶。
- 不含字母：杜绝 `和云弈`/`u`/`l` 这类幻觉。

两个容易踩的坑：

1. **`max_b_digits` 只在传统启发式链路用**。模型输出的是干净 `A<op>B`，
   真出现两位 B 就是真的两位，截断会算错。
2. **训练标签不带 `=?`**，但图里有。CTC 会把尾部当空白忽略 —— 这是有意的。

---

## 九、数据合规

验证码图片属运行时敏感物料，正常登录流程**绝不落盘**。本目录图片是人工发起、
显式指定目录的训练素材，属离线标注场景。但：

- `dataset/` 和 `models/` 已在 `.gitignore`，不要提交
- 不要上传到任何公共位置
- 仅本地保存

# ml/models/ —— 模型文件（通过 GitHub Release 获取）

本目录的模型权重与 ONNX **不入 git 仓库**（二进制体积大），统一挂在
[Release v2.0.0](https://github.com/aajj1314/CampusLink/releases/tag/v2.0.0) 的资产区，
文件名与下表一一对应，下载后放回本目录即可。

## 文件清单

| 文件 | 大小 | 用途 |
|---|---|---|
| `crnn_math.onnx` | 21.2 MB | **推理用**。客户端 2.0 与 `tools/captcha_read.py` 加载的就是它 |
| `crnn_math_ep234_83.5.pt` | 21.2 MB | 训练最优权重（val_exact 83.5%），供复现 / 微调 / 重新导出 |
| `crnn_math.pt` | 21.2 MB | 300 轮完整训练的最优权重（82.6%），当前 onnx 的来源 |
| `crnn_math_last.pt` | 21.2 MB | 最后一轮权重（过拟合，仅存档） |
| `train_full_log.txt` | 23 KB | 300 轮完整训练曲线 |

## 完整性

以下为发布时各文件的字节数，可用于下载后核对：

```
21213100  crnn_math.onnx
21239800  crnn_math.pt
21239800  crnn_math_ep234_83.5.pt
21240074  crnn_math_last.pt
    23180  train_full_log.txt
```

## 使用

- 客户端：把 `crnn_math.onnx` 放进安装目录的 `ml/models/`（或 `models/`），
  也可以用环境变量 `CAMPUSLINK_CRNN_MODEL` 指向任意路径。
- 训练侧：见 [`../README.md`](../README.md) 与 [`../AGENT_BRIEF.md`](../AGENT_BRIEF.md)；
  更换权重重新导出：`python ml/scripts/export_onnx.py --ckpt <权重文件>`。

## 合规

模型仅用于本项目所述的**单账号校园网自动认证**场景。不得用于多账号池、
规避门户频率限制或对门户服务端做任何攻击性测试。

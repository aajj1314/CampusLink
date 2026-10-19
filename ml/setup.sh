#!/usr/bin/env bash
# 在 Linux 训练机上准备环境（幂等，可重复执行）。
#
#   bash ml/setup.sh              # 建 .venv 并装依赖
#   bash ml/setup.sh --cuda 121   # 指定 CUDA 版本装 GPU 版 torch
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="$ROOT/.venv"

CUDA=""
if [ "${1:-}" = "--cuda" ]; then
    CUDA="${2:-}"
    if [ -z "$CUDA" ]; then
        echo "用法: bash ml/setup.sh --cuda 121   （121 = CUDA 12.1，见 pytorch.org）" >&2
        exit 2
    fi
fi

echo "== 项目目录: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
    echo "找不到 python3，请先装 Python 3.9+" >&2
    exit 2
fi
echo "== Python: $(python3 -V)"

if [ ! -d "$VENV" ]; then
    echo "== 创建虚拟环境 $VENV"
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"

python -m pip install -U pip wheel setuptools -q

if [ -n "$CUDA" ]; then
    echo "== 安装 GPU 版 torch (cu$CUDA)"
    pip install torch --index-url "https://download.pytorch.org/whl/cu$CUDA"
    # 装完再补其余依赖，避免 pip 又把 torch 换成 CPU 版
    grep -v '^torch' ml/requirements.txt > /tmp/_req_no_torch.txt
    pip install -r /tmp/_req_no_torch.txt
    rm -f /tmp/_req_no_torch.txt
else
    echo "== 安装依赖（含 CPU 版 torch）"
    pip install -r ml/requirements.txt
fi

echo
echo "== 自检"
python - <<'PY'
import sys
print("python  ", sys.version.split()[0])
try:
    import torch
    cuda = torch.cuda.is_available()
    print("torch   ", torch.__version__, "cuda:", cuda,
          ("| " + torch.cuda.get_device_name(0)) if cuda else "")
except ImportError:
    print("torch    缺失")
try:
    import onnxruntime
    print("onnxrt  ", onnxruntime.__version__,
          "providers:", onnxruntime.get_available_providers()[:3])
except ImportError:
    print("onnxruntime 缺失")
try:
    import numpy, PIL
    print("numpy   ", numpy.__version__)
    print("Pillow  ", PIL.__version__)
except ImportError as e:
    print("基础依赖缺失:", e)
PY

echo
echo "== 就绪。接下来："
echo "   source .venv/bin/activate"
echo "   python ml/scripts/label_web.py --port 8765    # 标注"
echo "   python ml/scripts/train_crnn.py --epochs 60   # 训练"

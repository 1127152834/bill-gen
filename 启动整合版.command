#!/bin/bash
# 账单模板整合版 — 一键启动（macOS，前台模式）
# 双击运行全部编辑器与切换页；Ctrl+C 全部停止。
# 若整合版已在运行则直接打开切换页。

cd "$(dirname "$0")" || exit 1

# ---------- 定位 Python（优先 pyenv） ----------
if [ -x "$HOME/.pyenv/shims/python3" ]; then
    export PATH="$HOME/.pyenv/shims:$PATH"
fi
PY="$(command -v python3 || true)"
[ -z "$PY" ] && PY=/usr/bin/python3
if [ -x .venv/bin/python ]; then PY="$PWD/.venv/bin/python"; fi

# ---------- 依赖检查 ----------
MISSING=""
"$PY" -c "import fitz" 2>/dev/null || MISSING="$MISSING pymupdf"
"$PY" -c "import fontTools" 2>/dev/null || MISSING="$MISSING fonttools"
"$PY" -c "import uharfbuzz" 2>/dev/null || MISSING="$MISSING uharfbuzz"
"$PY" -c "import PIL" 2>/dev/null || MISSING="$MISSING pillow"
"$PY" -c "import pystrich" 2>/dev/null || MISSING="$MISSING pystrich"
if [ -n "$MISSING" ]; then
    echo "❌ 缺少依赖:$MISSING，正在自动安装…"
    "$PY" -m pip install --quiet -r requirements.txt || {
        echo "安装失败，请按 README 创建虚拟环境并安装 requirements.txt"
        read -p "按回车关闭…"; exit 1; }
fi

# ---------- 已在运行？直接打开切换页 ----------
URL="http://127.0.0.1:${GATE_PORT:-8800}"
if curl -s -m 2 -o /dev/null "$URL/"; then
    echo "✅ 整合版已在运行：$URL  （本窗口可直接关闭）"
    open "$URL"; read -p "按回车关闭…"; exit 0
fi

# ---------- 前台启动 ----------
echo "正在启动账单模板整合版…  停止请按 Ctrl+C"
"$PY" run.py
echo ""
echo "整合版已停止。"
read -p "按回车关闭窗口…"

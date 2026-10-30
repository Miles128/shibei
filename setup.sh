#!/usr/bin/env bash
set -euo pipefail

echo "🧠 Shibei 一键安装"
echo "====================="

# 检查 Python（解析成具体路径，否则 uv 会按名字挑它自己管理的最新版本）
PYTHON="${SHIBEI_PYTHON:-python3}"
if ! PYTHON_BIN="$(command -v "$PYTHON")"; then
    echo "❌ 需要 Python 3.9+，未找到: $PYTHON"
    exit 1
fi
echo "✅ Python: $("$PYTHON_BIN" --version)"

# 检查 uv
if ! command -v uv &>/dev/null; then
    echo "❌ 需要 uv（依赖用 uv 管理），安装: brew install uv"
    exit 1
fi
echo "✅ uv: $(uv --version)"

# 安装依赖
echo ""
echo "📦 安装依赖..."
if [ ! -x .venv/bin/python ]; then
    uv venv .venv --python "$PYTHON_BIN" --quiet
fi
# fastembed（本地向量后端）要求 Python 3.10+；低版本只装核心与格式解析依赖
if "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    EXTRAS=".[all,vector]"
else
    EXTRAS=".[all]"
    echo "ℹ️ Python <3.10，跳过 fastembed 向量依赖（BM25 检索不受影响）"
fi
uv pip install --python .venv/bin/python -e "$EXTRAS" --quiet

# 初始化配置
echo ""
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo "📝 配置已创建: config.yaml（请修改 sources 路径）"
else
    echo "✅ config.yaml 已存在，跳过"
fi

# 创建数据目录
mkdir -p ~/.shibei/db

echo ""
echo "====================="
echo "✅ 安装完成！"
echo ""
echo "下一步："
echo "  source .venv/bin/activate    # 激活虚拟环境"
echo "  1. 编辑 config.yaml，修改 sources 为你的文档路径"
echo "  2. shibei import           # 导入文档"
echo "  3. shibei search '关键词'  # 搜索"
echo "  4. shibei serve            # 启动 MCP server"

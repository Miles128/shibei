#!/usr/bin/env bash
set -euo pipefail

echo "🧠 Shibei 一键安装"
echo "====================="

# 检查 Python
PYTHON="${SHIBEI_PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
    echo "❌ 需要 Python 3.9+，未找到: $PYTHON"
    exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# 安装依赖
echo ""
echo "📦 安装依赖..."
"$PYTHON" -m pip install -e . --quiet

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
echo "  1. 编辑 config.yaml，修改 sources 为你的文档路径"
echo "  2. shibei import           # 导入文档"
echo "  3. shibei search '关键词'  # 搜索"
echo "  4. shibei serve            # 启动 MCP server"

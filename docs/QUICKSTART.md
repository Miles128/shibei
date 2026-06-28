# 快速开始

## 1. 安装

```bash
git clone <repo-url> shibei
cd shibei
./setup.sh
```

`setup.sh` 做了三件事：
- `pip install -e .` 安装依赖
- 下载嵌入模型 `moka-ai/m3e-base`（约 440MB，首次需等几分钟）
- 创建默认配置 `config.yaml`

> 国内用户：`setup.sh` 自动用 `hf-mirror.com` 镜像下载模型。

## 2. 配置

编辑 `config.yaml`，**必改** `sources`：

```yaml
sources:
  - ~/Documents/notes        # 你的 Markdown 文档目录
  - ~/Documents/projects
```

其他项一般不用改。完整说明见 [CONFIG.md](CONFIG.md)。

## 3. 导入文档

```bash
shibei import
```

输出示例：
```
Found 51 markdown files
  ...
Total: 1012 chunks in 'knowledge_base'
```

## 4. 搜索

```bash
shibei search "银行风控" --limit 5
```

输出示例：
```
[1] ~/Documents/notes/银行系统.md (score=0.83, tags=interview,finance)
    银行IT系统全景...核心银行系统是存贷汇+会计核算...

[2] ~/Documents/notes/AI落地.md (score=0.82, tags=interview,ai)
    模型可解释性...SHAP和LIME是监管最认可的工具...
```

标签过滤：
```bash
shibei search "风控" --tag interview
```

## 5. 启动 MCP server

```bash
shibei serve
```

即可让任何 MCP 兼容的 Agent 接入。见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

## 6. 作为 Python 库用

```python
from shibei import Shibei

brain = Shibei("config.yaml")
results = brain.search("风控系统")
print(results["results"][0]["text"])
```

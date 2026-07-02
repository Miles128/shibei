"""CLI 入口 — shibei 命令"""

import argparse
import sys
import os

from .config import load_config
from .services import Shibei
from .profile import (
    resolve_config_path, list_profiles, create_profile,
    delete_profile, get_profile_path, profile_exists,
    PROFILES_DIR,
)


def cmd_serve(args):
    """启动 MCP server"""
    from .server import main as server_main
    if args.debug:
        os.environ["SHIBEI_MCP_DEBUG"] = "1"
    # Pass resolved config via env so server picks it up
    os.environ["SHIBEI_CONFIG"] = args.resolved_config
    server_main()


def cmd_search(args):
    brain = Shibei(args.resolved_config)
    results = brain.search(args.query, limit=args.limit, tag=args.tag)
    for r in results["results"]:
        print(f"[{r['rank']}] {r['source']} (score={r['score']}, tags={r['tags']})")
        print(f"    {r['text'].replace(chr(10), ' ')}")
        print()
    if results.get("memories"):
        print("🧠 相关记忆：")
        for m in results["memories"]:
            print(f"  [{m['category']}] {m['text']} (score={m['score']})")
        print()


def cmd_remember(args):
    """写入一条对话记忆"""
    brain = Shibei(args.resolved_config)
    result = brain.remember(
        fact=args.fact,
        category=args.category,
        source=args.source,
        tags=args.tags,
        expires_at=args.expires_at,
    )
    print(f"✅ 已记住 ({result['id'][:8]})")
    print(f"   {result['fact']}")
    if result.get("supersedes"):
        print(f"   ↳ 替代了旧记忆 {result['supersedes'][:8]}")


def cmd_memories_list(args):
    """列出记忆"""
    brain = Shibei(args.resolved_config)
    rows = brain.list_memories(limit=args.limit, category=args.category)

    if not rows:
        print("没有记忆")
        return

    for r in rows:
        superseded = " [已替代]" if r.get("superseded_by") else ""
        compressed = " [已压缩]" if "compressed" in (r.get("tags") or "") else ""
        print(f"{r['id'][:8]} [{r.get('category', '')}]{superseded}{compressed}")
        print(f"   {r['fact']}")
        if r.get("tags"):
            print(f"   tags: {r['tags']}")
        print()


def cmd_memories_search(args):
    """搜索记忆"""
    brain = Shibei(args.resolved_config)
    rows = brain.search_memories(args.query, limit=args.limit, category=args.category)

    if not rows:
        print("没有匹配记忆")
        return

    for r in rows:
        superseded = " [已替代]" if r.get("superseded_by") else ""
        compressed = " [已压缩]" if "compressed" in (r.get("tags") or "") else ""
        print(f"{r['id'][:8]} [{r.get('category', '')}]{superseded}{compressed}")
        print(f"   {r['fact']}")
        if r.get("tags"):
            print(f"   tags: {r['tags']}")
        print()


def cmd_memories_cleanup(args):
    """清理已过期的记忆"""
    brain = Shibei(args.resolved_config)
    deleted = brain.cleanup_memories()
    print(f"🧹 清理完成，删除 {deleted} 条过期记忆")


def cmd_memories_forget(args):
    """删除一条记忆"""
    brain = Shibei(args.resolved_config)
    if brain.forget_memory(args.memory_id):
        print(f"✅ 已删除记忆 {args.memory_id}")
    else:
        print(f"❌ 未找到记忆 {args.memory_id}")


def cmd_import(args):
    brain = Shibei(args.resolved_config)
    dirs = args.dirs.split(",") if args.dirs else None
    result = brain.import_all(dirs=dirs, full=args.full,
                              file_pattern=args.file_pattern)
    parts = [f"{result['files']} files → {result['chunks']} chunks"]
    if result.get("skipped"):
        parts.append(f"{result['skipped']} skipped")
    if result.get("removed"):
        parts.append(f"{result['removed']} removed")
    if result.get("deduped"):
        parts.append(f"{result['deduped']} deduped")
    print(f"Imported: {', '.join(parts)}")


def cmd_list(args):
    brain = Shibei(args.resolved_config)
    result = brain.list_sources()
    print(f"Collection: {result['count']} sources")
    for s in result["sources"]:
        print(f"  {s}")


def cmd_dream(args):
    """执行 dreaming — 默认行为"""
    brain = Shibei(args.resolved_config)
    housekeeping = False if getattr(args, "no_housekeeping", False) else {}
    result = brain.dream(housekeeping=housekeeping)

    if result["status"] == "empty":
        print("知识库为空，先运行 shibei import")
        return

    stats = result["stats"]
    print(f"💤 Dream 完成")
    print(f"   {stats['unique_docs']} 个文档, {stats['total_chunks']} 个 chunks")
    print(f"   发现 {stats['cross_references']} 个跨文档连接, {stats['topics_with_connections']} 个共享主题")
    print()

    if result["insights"]:
        print("📊 洞察：")
        for insight in result["insights"]:
            print(f"  • {insight}")
        print()

    if result["cross_references"]:
        print("🔗 跨文档连接 (Top 10)：")
        for ref in result["cross_references"][:10]:
            kw_str = ", ".join(ref["shared_keywords"][:3])
            print(f"  {ref['doc_a']} ←→ {ref['doc_b']}")
            print(f"    关键词: {kw_str} (强度={ref['strength']})")
        print()

    if result["patterns"]:
        print("🏷️  标签模式：")
        for tag, kws in result["patterns"].items():
            print(f"  [{tag}] {', '.join(kws)}")

    # 自动整理结果
    hk = result.get("housekeeping", {})
    if hk.get("enabled"):
        print()
        print("🧹 整理候选：")
        mem = hk.get("memory", {})
        if "expired_deleted" in mem:
            print(f"  清理过期记忆: {mem['expired_deleted']} 条")
        if "compressible_count" in mem:
            print(f"  可压缩记忆候选: {mem['compressible_count']} 条")
        cons = hk.get("consolidation", {})
        if cons.get("status") == "ok":
            print(f"  相似 chunk 聚类: {cons.get('cluster_count', 0)} 组")
        if mem.get("error") or cons.get("error"):
            print(f"  部分整理失败，详见日志")

    # 增量对比
    diff = result.get("diff")
    if diff and "message" not in diff:
        print()
        print("📈 增量变化（vs 上次 dream）：")
        delta = diff.get("stats_delta", {})
        delta_parts = []
        for k, v in delta.items():
            sign = "+" if v > 0 else ""
            delta_parts.append(f"{k} {sign}{v}")
        if delta_parts:
            print(f"  统计：{', '.join(delta_parts)}")
        if diff.get("new_connections"):
            print(f"  🆕 新增连接：{', '.join(diff['new_connections'][:3])}")
        if diff.get("lost_connections"):
            print(f"  ❌ 消失连接：{', '.join(diff['lost_connections'][:3])}")
        if diff.get("new_topics"):
            print(f"  🆕 新增主题：{', '.join(diff['new_topics'][:3])}")
        if diff.get("new_insights"):
            print(f"  🆕 新洞察：{diff['new_insights'][0]}")


def cmd_dream_compress(args):
    """压缩历史 dream 记录"""
    from .config import load_config
    from .dreamer import compress_dreams

    cfg = load_config(args.resolved_config)
    result = compress_dreams(cfg.db_path_expanded, keep_recent=args.keep)
    print(f"📦 Dream 历史压缩完成")
    print(f"   总记录: {result['total']}")
    print(f"   保留完整: {result['kept_full']}")
    print(f"   压缩为摘要: {result['compressed']}")


def cmd_dream_clean(args):
    """清理旧 dream 记录"""
    from .config import load_config
    from .dreamer import clean_old_dreams

    cfg = load_config(args.resolved_config)
    result = clean_old_dreams(cfg.db_path_expanded, max_age_days=args.days)
    print(f"🧹 Dream 历史清理完成")
    print(f"   删除: {result['deleted']} 条（超过 {args.days} 天）")
    print(f"   剩余: {result['remaining']} 条")


def cmd_dream_history(args):
    """查看 dream 历史"""
    from .config import load_config
    from .dreamer import list_dreams

    cfg = load_config(args.resolved_config)
    dreams = list_dreams(cfg.db_path_expanded)
    if not dreams:
        print("暂无 dream 历史，运行 shibei dream run")
        return
    print(f"Dream 历史（{len(dreams)} 条）：")
    for d in dreams:
        stats = d.get("stats", {})
        ts = d.get("timestamp", "unknown")
        chunks = stats.get("total_chunks", "?")
        refs = stats.get("cross_references", "?")
        compressed = " (compressed)" if d.get("compressed") else ""
        print(f"  {ts}  chunks={chunks}, refs={refs}{compressed}  [{d['file']}]")


def cmd_dream_diff(args):
    """查看与上次 dream 的对比"""
    from .config import load_config
    from .dreamer import load_last_dream

    cfg = load_config(args.resolved_config)
    last = load_last_dream(cfg.db_path_expanded)
    if not last:
        print("无历史 dream 记录，先运行 shibei dream run")
        return

    print(f"上次 dream: {last.get('timestamp', 'unknown')}")
    stats = last.get("stats", {})
    print(f"  chunks: {stats.get('total_chunks', '?')}")
    print(f"  docs: {stats.get('unique_docs', '?')}")
    print(f"  cross_refs: {stats.get('cross_references', '?')}")
    if last.get("insights"):
        print("\n洞察：")
        for ins in last["insights"][:5]:
            print(f"  • {ins}")


def cmd_consolidate(args):
    """整理知识库：发现相似 chunk 候选"""
    brain = Shibei(args.resolved_config)
    result = brain.consolidate(
        threshold=args.threshold,
        min_cluster_size=args.min_size,
        max_chunks=args.max_chunks,
    )

    if result["status"] == "empty":
        print(result.get("message", "知识库为空"))
        return

    print(f"🧹 整理完成")
    print(f"   共扫描 {result['total_chunks']} 个 chunks")
    print(f"   发现 {result['cluster_count']} 组相似内容")
    print()

    for i, cluster in enumerate(result["clusters"], 1):
        print(f"--- 组 {i} ({cluster['size']} 条) ---")
        print(f"来源: {', '.join(cluster['sources'])}")
        print("原始片段:")
        for c in cluster["chunks"][:3]:
            print(f"  • [{Path(c['source']).name}] {c['text'][:120]}...")
        print()

    if args.output:
        import json
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"💾 结果已保存到 {args.output}")


def cmd_profile_list(args):
    profiles = list_profiles()
    if profiles:
        print(f"Profiles ({len(profiles)}):")
        for name in profiles:
            p = get_profile_path(name)
            print(f"  {name:20s}  → {p}")
    else:
        print("No profiles yet. Create one with: shibei profile create <name>")


def cmd_profile_create(args):
    try:
        p = create_profile(args.name, args.template)
        print(f"✅ Created profile '{args.name}': {p}")
        print(f"   Edit it: vim {p}")
    except FileExistsError as e:
        print(f"❌ {e}")


def cmd_profile_delete(args):
    try:
        delete_profile(args.name)
        print(f"✅ Deleted profile '{args.name}'")
    except FileNotFoundError as e:
        print(f"❌ {e}")


def cmd_profile_show(args):
    if not profile_exists(args.name):
        print(f"❌ Profile '{args.name}' not found")
        return
    p = get_profile_path(args.name)
    print(f"Profile: {args.name}")
    print(f"Path:    {p}")
    print()
    print(p.read_text())


def cmd_init(args):
    """一键初始化：检查环境 + 安装依赖 + 创建配置"""
    import subprocess
    import sys

    print("🧠 Shibei 一键初始化")
    print("=====================")

    # 1. 检查 Python 版本
    if sys.version_info < (3, 9):
        print(f"❌ 需要 Python 3.9+，当前 {sys.version.split()[0]}")
        sys.exit(1)
    print(f"✅ Python {sys.version.split()[0]}")

    # 2. 安装依赖（含可选依赖）
    print("\n📦 安装依赖...")
    extras = args.extras or "all"
    cmd = [sys.executable, "-m", "pip", "install", "-e", f".[{extras}]"]
    if args.no_jieba and extras == "all":
        cmd = [sys.executable, "-m", "pip", "install", "-e", ".[docx,xlsx]"]
    try:
        subprocess.check_call(cmd)
        print(f"✅ 依赖安装完成 (extras={extras})")
    except subprocess.CalledProcessError as e:
        print(f"❌ 依赖安装失败: {e}")
        sys.exit(1)

    # 3. 创建配置
    print("\n📝 初始化配置...")
    config_path = args.config or "config.yaml"
    from pathlib import Path
    p = Path(config_path)
    if p.exists() and not args.force:
        print(f"✅ {config_path} 已存在，跳过（用 --force 覆盖）")
    else:
        example = Path("config.example.yaml")
        if example.exists():
            import shutil
            shutil.copy(example, p)
            print(f"✅ 配置已创建: {config_path}")
        else:
            print(f"⚠️  config.example.yaml 不存在，跳过")

    # 5. 创建数据目录
    data_dir = Path.home() / ".shibei" / "db"
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"✅ 数据目录: {data_dir}")

    print("\n=====================")
    print("✅ 初始化完成！")
    print("\n下一步：")
    print(f"  1. 编辑 {config_path}，修改 sources 为你的文档路径")
    print("  2. shibei import           # 导入文档")
    print("  3. shibei search '关键词'  # 搜索")
    print("  4. shibei serve            # 启动 MCP server")


def main():
    parser = argparse.ArgumentParser(
        description="🧠 Shibei — 中文语义知识库",
        prog="shibei",
    )
    parser.add_argument(
        "--config", default=None, help="配置文件路径"
    )
    parser.add_argument(
        "--profile", "-p", default=None,
        help=f"Profile 名称（~/.shibei/profiles/<name>.yaml）"
    )

    sub = parser.add_subparsers(dest="cmd")

    # init
    p_init = sub.add_parser("init", help="一键初始化：安装依赖 + 创建配置")
    p_init.add_argument("--extras", choices=["all", "docx", "xlsx", "jieba", "none"],
                        default="all", help="可选依赖组合（默认 all）")
    p_init.add_argument("--no-jieba", action="store_true", help="不安装 jieba（即使 extras=all）")
    p_init.add_argument("--config", default=None, help="配置文件路径（默认 config.yaml）")
    p_init.add_argument("--force", action="store_true", help="覆盖已有配置文件")

    # serve
    p = sub.add_parser("serve", help="启动 MCP stdio server")
    p.add_argument("--debug", action="store_true", help="显示 stderr 日志")

    # search
    p = sub.add_parser("search", help="语义搜索")
    p.add_argument("query", help="搜索关键词")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--tag", help="标签过滤")

    # import
    p_import = sub.add_parser("import", help="全量导入文档")
    p_import.add_argument("--full", action="store_true", help="全量重建（忽略 manifest，重新导入所有文件）")
    p_import.add_argument("--dirs", help="要扫描的目录（逗号分隔），默认用 config sources")
    p_import.add_argument("--file-pattern", help="文件名 glob 过滤（如 'README*'）")

    # list
    sub.add_parser("list", help="列出已入库文件")

    # remember
    p_remember = sub.add_parser("remember", help="写入一条 Agent 对话记忆")
    p_remember.add_argument("fact", help="要记忆的事实")
    p_remember.add_argument("--category", default="user",
                            choices=["user", "agent", "project", "preference"],
                            help="记忆分类（默认 user）")
    p_remember.add_argument("--source", default="", help="来源会话ID")
    p_remember.add_argument("--tags", default="", help="逗号分隔标签")
    p_remember.add_argument("--expires-at", default=None, help="过期时间 ISO 8601")

    # memories（子命令组）
    p_memories = sub.add_parser("memories", help="管理对话记忆")
    p_memories_sub = p_memories.add_subparsers(dest="memories_cmd")

    p_memories_list = p_memories_sub.add_parser("list", help="列出记忆")
    p_memories_list.add_argument("--category", default=None,
                                 choices=["user", "agent", "project", "preference"],
                                 help="按分类过滤")
    p_memories_list.add_argument("--limit", type=int, default=20, help="返回条数")

    p_memories_search = p_memories_sub.add_parser("search", help="搜索记忆")
    p_memories_search.add_argument("query", help="搜索关键词")
    p_memories_search.add_argument("--category", default=None,
                                   choices=["user", "agent", "project", "preference"],
                                   help="按分类过滤")
    p_memories_search.add_argument("--limit", type=int, default=20, help="返回条数")

    p_memories_cleanup = p_memories_sub.add_parser("cleanup", help="清理已过期的记忆")

    p_memories_forget = p_memories_sub.add_parser("forget", help="删除一条记忆")
    p_memories_forget.add_argument("memory_id", help="记忆 ID（可只给前 8 位，自动匹配）")

    # consolidate
    p_consolidate = sub.add_parser("consolidate", help="整理知识库：合并相似 chunk")
    p_consolidate.add_argument("--threshold", type=float, default=0.6,
                               help="Overlap 相似度阈值（默认 0.6）")
    p_consolidate.add_argument("--min-size", type=int, default=2,
                               help="最小组大小（默认 2）")
    p_consolidate.add_argument("--max-chunks", type=int, default=500,
                               help="最大扫描 chunk 数（默认 500）")
    p_consolidate.add_argument("--output", default=None,
                               help="输出合并报告到 JSON 文件")

    # dream（子命令组）
    p_dream = sub.add_parser("dream", help="知识库 dreaming — 发现隐藏连接与洞察")
    p_dream_sub = p_dream.add_subparsers(dest="dream_cmd")

    p_dream_run = p_dream_sub.add_parser("run", help="执行 dreaming（默认）")
    p_dream_run.add_argument("--no-housekeeping", action="store_true",
                             help="禁用整理（记忆清理/候选识别）")

    p_dream_compress = p_dream_sub.add_parser("compress", help="压缩历史 dream 记录")
    p_dream_compress.add_argument("--keep", type=int, default=5,
                                  help="保留最近 N 次完整记录（默认 5）")

    p_dream_clean = p_dream_sub.add_parser("clean", help="清理旧 dream 记录")
    p_dream_clean.add_argument("--days", type=int, default=90,
                               help="删除超过 N 天的记录（默认 90）")

    p_dream_sub.add_parser("history", help="查看 dream 历史")
    p_dream_sub.add_parser("diff", help="查看与上次 dream 的对比")

    # profile
    p_profile = sub.add_parser("profile", help="管理配置 profile")
    p_sub = p_profile.add_subparsers(dest="profile_cmd")

    p_sub.add_parser("list", help="列出所有 profile")

    p_create = p_sub.add_parser("create", help="创建新 profile")
    p_create.add_argument("name", help="Profile 名称")
    p_create.add_argument("--template", help="从已有 profile 复制")

    p_del = p_sub.add_parser("delete", help="删除 profile")
    p_del.add_argument("name", help="Profile 名称")

    p_show = p_sub.add_parser("show", help="查看 profile 内容")
    p_show.add_argument("name", help="Profile 名称")

    args = parser.parse_args()

    # Resolve config path
    try:
        args.resolved_config = resolve_config_path(args.config, args.profile)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    if not args.cmd:
        parser.print_help()
        return

    # init 不需要 resolved_config
    if args.cmd == "init":
        cmd_init(args)
        return

    # Profile subcommands
    if args.cmd == "profile":
        {
            "list": cmd_profile_list,
            "create": cmd_profile_create,
            "delete": cmd_profile_delete,
            "show": cmd_profile_show,
        }.get(args.profile_cmd, lambda a: p_profile.print_help())(args)
        return

    # memories 子命令组
    if args.cmd == "memories":
        memories_cmd = getattr(args, "memories_cmd", None) or "list"
        {
            "list": cmd_memories_list,
            "search": cmd_memories_search,
            "cleanup": cmd_memories_cleanup,
            "forget": cmd_memories_forget,
        }.get(memories_cmd, lambda a: p_memories.print_help())(args)
        return

    # Core commands
    if args.cmd == "dream":
        # dream 子命令组
        dream_cmd = getattr(args, "dream_cmd", None) or "run"
        {
            "run": cmd_dream,
            "compress": cmd_dream_compress,
            "clean": cmd_dream_clean,
            "history": cmd_dream_history,
            "diff": cmd_dream_diff,
        }.get(dream_cmd, cmd_dream)(args)
        return

    {
        "serve": cmd_serve,
        "search": cmd_search,
        "import": cmd_import,
        "list": cmd_list,
        "remember": cmd_remember,
        "consolidate": cmd_consolidate,
    }[args.cmd](args)


if __name__ == "__main__":
    main()

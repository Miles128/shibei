"""Shibei 服务层 — 统一入口"""

import logging
from collections.abc import Callable

from .config import load_config

logger = logging.getLogger(__name__)


class Shibei:
    """中文语义知识库顶层 API。

    Usage:
        brain = Shibei("config.yaml")
        brain.import_all()
        results = brain.search("银行风控")
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.config.db_path_expanded.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        include_memories: bool = True,
        rewrite: 'bool | None' = None,
        llm_rewrite: 'bool | None' = None,
        expand: 'bool | None' = None,
        fuzzy: 'bool | None' = None,
        synonyms: 'str | None' = None,
    ) -> dict:
        from copy import deepcopy

        from .searcher import Searcher

        cfg = self.config
        if any(
            x is not None
            for x in (rewrite, llm_rewrite, expand, fuzzy, synonyms)
        ):
            cfg = deepcopy(self.config)
            if rewrite:
                cfg.rewriter.enabled = True
            if llm_rewrite:
                cfg.rewriter.enabled = True
                cfg.rewriter.use_llm = True
            if expand is True:
                cfg.expander.enabled = True
            elif expand is False:
                cfg.expander.enabled = False
            if synonyms:
                cfg.expander.synonyms_path = synonyms
                cfg.expander.enabled = True
            if fuzzy:
                cfg.expander.fuzzy = True
                cfg.expander.enabled = True
        return Searcher(cfg).search(
            query, limit, tag, include_memories=include_memories
        )

    def context(
        self,
        message: str,
        limit: int = 5,
        tag: 'str | None' = None,
        write_hints: bool = True,
        query: 'str | None' = None,
    ) -> dict:
        """从用户消息构建答前上下文包（文档 + 记忆 + 写侧提示）。

        query: 可选。宿主 Agent 用自己的 LLM 扩写后的检索词，优先于规则抽词。
        """
        from .context import build_context
        return build_context(
            self.config,
            message=message,
            limit=limit,
            tag=tag,
            write_hints=write_hints,
            query=query,
        )

    def import_all(self, dirs: 'list[str] | None' = None, full: bool = False,
                   file_pattern: 'str | None' = None,
                   progress: 'Callable[[int, int], None] | None' = None) -> dict:
        from .importer import Importer
        return Importer(self.config).run(
            dirs, full=full, file_pattern=file_pattern, progress=progress
        )

    def list_sources(self, limit: int = 0, offset: int = 0) -> dict:
        from .importer import list_sources
        return list_sources(self.config, limit=limit, offset=offset)

    def clean_orphans(
        self,
        dirs: 'list[str] | None' = None,
        dry_run: bool = False,
    ) -> dict:
        from .importer import clean_orphans
        return clean_orphans(self.config, dirs=dirs, dry_run=dry_run)

    def dream(
        self,
        housekeeping: 'dict | None' = None,
        format: str = "brief",
        persist: bool = True,
    ) -> dict:
        from .dreamer import dream
        return dream(
            self.config.db_path_expanded,
            housekeeping=housekeeping,
            format=format,
            persist=persist,
        )

    # ---------- 对话记忆 ----------

    def _memory_store(self):
        from .memory import MemoryStore
        return MemoryStore(self.config.db_path_expanded)

    def remember(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: 'str | None' = None,
        source_message_ids: str = "",
    ) -> dict:
        """写入一条 Agent 对话记忆。

        source_message_ids: 溯源字段，触发该记忆的原始消息 ID（对标 TencentDB L1 溯源链）。
        """
        store = self._memory_store()
        return store.add(
            fact=fact,
            category=category,
            source=source,
            tags=tags,
            expires_at=expires_at,
            source_message_ids=source_message_ids,
        )

    def persona(
        self,
        per_category: int = 5,
        max_facts: int = 20,
    ) -> dict:
        """轻量画像层：按类别聚合有效记忆（零 LLM）。"""
        return self._memory_store().persona(
            per_category=per_category, max_facts=max_facts
        )

    def auto_remember(self, message: str) -> list[dict]:
        """自动记忆抽取（P1，默认关闭）：用 LLM 从消息抽取事实并写入。

        仅当 config.auto_memory.enabled 且设置了 SHIBEI_LLM_API_KEY 时生效；
        抽取失败或没有新事实返回空列表，不抛异常。
        """
        am = self.config.auto_memory
        if not am.enabled:
            return []
        from .llm import LLMClient, LLMOptions
        from .memory import MemoryStore

        try:
            client = LLMClient(LLMOptions(
                base_url=self.config.llm.base_url,
                model=self.config.llm.model,
                timeout=self.config.llm.timeout,
                temperature=0.0,
                max_tokens=200,
            ))
            prompt = (
                "从下面的用户消息中，提取值得长期记住的客观事实"
                "（偏好、习惯、目标、项目背景、关键决策、技术栈等）。\n"
                "规则：\n"
                "1. 用简洁第三人称陈述句，一条一行，不要编号\n"
                "2. 忽略寒暄、提问、无信息量内容\n"
                f"3. 最多输出 {am.max_facts} 条；没有可记的事实输出空行\n\n"
                f"用户消息：{message[:2000]}"
            )
            raw = client.chat([
                {"role": "system", "content": "你是记忆提取器，只输出事实列表。"},
                {"role": "user", "content": prompt},
            ])
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动记忆抽取失败: %s", exc)
            return []

        store = MemoryStore(self.config.db_path_expanded)
        written = []
        for line in raw.splitlines():
            fact = line.strip().lstrip("-•*0123456789. ")
            if not fact or len(fact) < 4:
                continue
            try:
                written.append(store.add(
                    fact=fact,
                    category=am.category,
                    source=am.source,
                    tags=am.category,
                    source_message_ids="auto",
                ))
            except ValueError:
                continue
            if len(written) >= am.max_facts:
                break
        return written

    def search_memories(self, query: str, limit: int = 5,
                        category: 'str | None' = None) -> list[dict]:
        """单独搜索对话记忆。"""
        return self._memory_store().search(query, limit=limit, category=category)

    def list_memories(self, limit: int = 100,
                      category: 'str | None' = None) -> list[dict]:
        """列出所有有效记忆（默认排除已被替代的版本）。"""
        return self._memory_store().list(limit=limit, category=category)

    def forget_memory(self, memory_id: str) -> bool:
        """删除一条记忆。"""
        return self._memory_store().forget(memory_id)

    def count_memories(self, category: 'str | None' = None) -> int:
        """统计有效记忆数量。"""
        return self._memory_store().count(category=category)

    def cleanup_memories(self) -> int:
        """清理已过期的对话记忆，返回删除条数。"""
        return self._memory_store().cleanup_expired()

    def update_memory(
        self,
        memory_id: str,
        fact: str,
        tags: 'str | None' = None,
    ) -> bool:
        """更新一条记忆内容（用于外部 Agent 执行压缩/修正）。"""
        return self._memory_store().update(memory_id, fact, tags=tags)

    def consolidate(
        self,
        threshold: float = 0.6,
        min_cluster_size: int = 2,
        max_chunks: int = 500,
        apply: bool = False,
    ) -> dict:
        """整理知识库：发现相似 chunk 聚类，可选直接合并（零 LLM 依赖）。"""
        from .consolidator import consolidate
        return consolidate(
            self.config.db_path_expanded,
            threshold=threshold,
            min_cluster_size=min_cluster_size,
            max_chunks=max_chunks,
            apply=apply,
        )

    def apply_consolidation(self, clusters: list[dict]) -> dict:
        """根据外部 Agent 提供的 cluster 摘要直接应用合并（零 LLM 依赖）。"""
        from .consolidator import apply_consolidation
        return apply_consolidation(self.config.db_path_expanded, clusters)

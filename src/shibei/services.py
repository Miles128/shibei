"""Shibei 服务层 — 统一入口"""

import logging

from .config import Config, load_config
from .llm import client_from_config

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
        self.llm = client_from_config(self.config)

    def search(
        self,
        query: str,
        limit: int = 5,
        tag: 'str | None' = None,
        include_memories: bool = True,
    ) -> dict:
        from .searcher import Searcher
        return Searcher(self.config, llm_client=self.llm).search(
            query, limit, tag, include_memories=include_memories
        )

    def import_all(self, dirs: 'list[str] | None' = None, full: bool = False,
                   file_pattern: 'str | None' = None) -> dict:
        from .importer import Importer
        return Importer(self.config, llm_client=self.llm).run(
            dirs, full=full, file_pattern=file_pattern
        )

    def list_sources(self, limit: int = 0, offset: int = 0) -> dict:
        from .importer import list_sources
        return list_sources(self.config, limit=limit, offset=offset)

    def dream(self, format: str = "full", persist: bool = True) -> dict:
        from .dreamer import dream
        return dream(
            self.config.chroma_path_expanded,
            collection_name=self.config.chroma.collection,
            format=format,
            persist=persist,
            llm_client=self.llm,
        )

    # ---------- 对话记忆 ----------

    def _memory_store(self):
        from .memory import MemoryStore
        return MemoryStore(
            self.config.chroma_path_expanded,
            embedding_model=self.config.embedding.model,
        )

    def remember(
        self,
        fact: str,
        category: str = "user",
        source: str = "",
        tags: str = "",
        expires_at: 'str | None' = None,
    ) -> dict:
        """写入一条 Agent 对话记忆。开启 LLM 时自动提取多条事实。"""
        store = self._memory_store()

        # LLM 事实提取：把一段话拆成多个独立事实
        facts = [fact]
        if self.llm.ready:
            try:
                extracted = self.llm.extract_facts(fact)
                if extracted:
                    facts = extracted
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
                logger.warning("LLM 事实提取失败，使用原 fact: %s", e)

        last_result = None
        for f in facts:
            last_result = store.add(
                fact=f,
                category=category,
                source=source,
                tags=tags,
                expires_at=expires_at,
                embedding_model=self.config.embedding.model,
            )

        # 记录本次提取数量，方便调用方感知
        if last_result is not None and len(facts) > 1:
            last_result["extracted_facts"] = facts
        return last_result or {"fact": fact, "id": ""}

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

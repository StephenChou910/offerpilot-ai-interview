"""
多路检索架构 - SearchChannel 接口设计
参考 Ragent 项目的多路检索设计
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from app.modules.knowledge_base.schemas import RagReferenceDTO

logger = logging.getLogger(__name__)

RRF_K = 60


@dataclass
class SearchContext:
    """检索上下文"""

    question: str
    kb_id: int
    top_k: int
    query_embedding: List[float] | None = None
    rewritten_query: str | None = None


@dataclass
class SearchChannelResult:
    """检索通道结果"""

    channel_name: str
    chunks: List[RagReferenceDTO]
    confidence: float
    latency_ms: int


class SearchChannel(ABC):
    """检索通道接口"""

    @abstractmethod
    def get_name(self) -> str:
        """获取通道名称"""
        pass

    @abstractmethod
    def get_priority(self) -> int:
        """获取优先级（数字越小优先级越高）"""
        pass

    def get_fusion_weight(self) -> float:
        """RRF 融合权重。默认通道等权，子类可降低辅助通道的影响。"""
        return 1.0

    @abstractmethod
    def is_enabled(self, context: SearchContext) -> bool:
        """判断通道是否启用"""
        pass

    @abstractmethod
    async def search(self, context: SearchContext) -> SearchChannelResult:
        """执行检索"""
        pass


class VectorSearchChannel(SearchChannel):
    """向量检索通道"""

    def __init__(self, rag_service, vector_service, db_session):
        self.rag_service = rag_service
        self.vector_service = vector_service
        self.db_session = db_session

    def get_name(self) -> str:
        return "VectorSearch"

    def get_priority(self) -> int:
        return 1  # 最高优先级

    def is_enabled(self, context: SearchContext) -> bool:
        return True  # 向量检索始终启用

    async def search(self, context: SearchContext) -> SearchChannelResult:
        """向量检索 - 使用 pgvector"""
        start = time.time()

        try:
            from sqlalchemy import select

            from app.modules.knowledge_base.models import KnowledgeBaseEntity, KnowledgeChunkEntity

            # 0. 获取知识库名称（用于来源追溯）
            kb_stmt = select(KnowledgeBaseEntity.name).where(KnowledgeBaseEntity.id == context.kb_id)
            kb_result = await self.db_session.execute(kb_stmt)
            source_name = kb_result.scalar_one_or_none() or "未知文档"

            # 1. 生成查询向量。优先使用改写后的检索查询，避免多轮问题或口语化问题绕过 rewrite。
            search_query = context.rewritten_query or context.question
            query_embedding = context.query_embedding or self.vector_service.embed_text(search_query)

            # 2. 使用 pgvector 进行向量检索
            # 优先使用 pgvector，如果 embedding 列为空则降级到内存检索
            stmt = (
                select(KnowledgeChunkEntity)
                .where(KnowledgeChunkEntity.knowledge_base_id == context.kb_id)
                .where(KnowledgeChunkEntity.embedding.isnot(None))
                .order_by(KnowledgeChunkEntity.embedding.cosine_distance(query_embedding))
                .limit(context.top_k * 2)  # 召回更多候选用于重排序
            )
            result = await self.db_session.execute(stmt)
            chunks_entities = result.scalars().all()

            # 3. 如果 pgvector 没有结果，降级到内存检索
            if not chunks_entities:
                logger.warning("pgvector 检索无结果，降级到内存检索")
                stmt_fallback = select(KnowledgeChunkEntity).where(
                    KnowledgeChunkEntity.knowledge_base_id == context.kb_id
                )
                result_fallback = await self.db_session.execute(stmt_fallback)
                all_chunks = result_fallback.scalars().all()
                chunks = self.rag_service._search_chunks(all_chunks, search_query, context.top_k * 2)
            else:
                # 4. 转换为 RagReferenceDTO
                chunks = []
                for chunk_entity in chunks_entities:
                    # 计算余弦相似度分数（1 - cosine_distance）
                    if chunk_entity.embedding is not None:
                        distance = self._cosine_distance(query_embedding, chunk_entity.embedding)
                        score = 1.0 - distance
                    else:
                        score = 0.5

                    chunks.append(
                        RagReferenceDTO(
                            chunk_id=chunk_entity.id,
                            chunk_index=chunk_entity.chunk_index,
                            title=chunk_entity.title or "",
                            content=chunk_entity.content or "",
                            content_preview=chunk_entity.content_preview or chunk_entity.content[:200],
                            score=score,
                            source_name=source_name,
                        )
                    )

            latency_ms = int((time.time() - start) * 1000)
            confidence = max([c.score for c in chunks]) if chunks else 0.0

            logger.info(
                "向量检索完成: channel=%s, chunks=%d, latency=%dms, method=%s",
                self.get_name(),
                len(chunks),
                latency_ms,
                "pgvector" if chunks_entities else "memory",
            )

            return SearchChannelResult(
                channel_name=self.get_name(), chunks=chunks, confidence=confidence, latency_ms=latency_ms
            )
        except Exception as e:
            logger.error("向量检索失败: %s", e, exc_info=True)
            return SearchChannelResult(
                channel_name=self.get_name(), chunks=[], confidence=0.0, latency_ms=int((time.time() - start) * 1000)
            )

    def _cosine_distance(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦距离"""
        import math

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))
        if magnitude1 == 0 or magnitude2 == 0:
            return 1.0
        cosine_similarity = dot_product / (magnitude1 * magnitude2)
        return 1.0 - cosine_similarity


class MultiChannelRetrievalEngine:
    """多路检索引擎"""

    def __init__(self, channels: List[SearchChannel]):
        self.channels = sorted(channels, key=lambda c: c.get_priority())
        logger.info("多路检索引擎初始化: channels=%d", len(self.channels))

    async def retrieve(self, context: SearchContext) -> List[RagReferenceDTO]:
        """
        执行多路检索

        流程：
        1. 并行执行所有启用的通道
        2. 合并所有通道的结果
        3. 后处理：去重 → Top-K
        """
        # 1. 筛选启用的通道
        enabled_channels = [c for c in self.channels if c.is_enabled(context)]
        if not enabled_channels:
            logger.warning("没有启用的检索通道")
            return []

        logger.info("启用的检索通道: %s", [c.get_name() for c in enabled_channels])

        # 2. 并行执行所有通道
        import asyncio

        results = await asyncio.gather(*[c.search(context) for c in enabled_channels], return_exceptions=True)

        # 3. 基于排名融合结果。
        # 不直接混排各通道原始分数：向量余弦相似度与图谱实体命中分数不在同一量纲，
        # 直接比较会让图谱的启发式分数挤掉语义更相关的向量候选。
        fused_chunks: dict[int, RagReferenceDTO] = {}
        fused_scores: dict[int, float] = {}
        # 纯向量已经提供完整 Top-K 时，图谱只作为候选集内的重排信号。
        # 这样可避免高频实体将向量第 K+1 名的弱相关片段提升进最终结果，
        # 从而牺牲基础召回率。向量候选不足时仍允许辅助通道补齐结果。
        primary_result = next(
            (
                result
                for i, result in enumerate(results)
                if not isinstance(result, Exception) and enabled_channels[i].get_priority() == 1
            ),
            None,
        )
        protected_primary_ids = (
            {chunk.chunk_id for chunk in primary_result.chunks[: context.top_k]}
            if primary_result is not None and len(primary_result.chunks) >= context.top_k
            else set()
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("通道 %s 检索失败: %s", enabled_channels[i].get_name(), result)
                continue
            channel = enabled_channels[i]
            weight = channel.get_fusion_weight()
            for rank, chunk in enumerate(result.chunks, start=1):
                if channel.get_priority() != 1 and protected_primary_ids and chunk.chunk_id not in protected_primary_ids:
                    continue
                contribution = weight / (RRF_K + rank)
                fused_scores[chunk.chunk_id] = fused_scores.get(chunk.chunk_id, 0.0) + contribution
                if chunk.chunk_id not in fused_chunks:
                    fused_chunks[chunk.chunk_id] = chunk

        if not fused_chunks:
            logger.warning("所有检索通道均未返回结果")
            return []

        # 4. 为最终引用写入融合分数并排序。相同 chunk 被多路召回时会累积贡献。
        chunks = list(fused_chunks.values())
        for chunk in chunks:
            chunk.score = fused_scores[chunk.chunk_id]
            chunk.metadata = {**chunk.metadata, "fusion": "weighted_rrf"}
        chunks.sort(key=lambda c: c.score, reverse=True)
        return chunks[: context.top_k]

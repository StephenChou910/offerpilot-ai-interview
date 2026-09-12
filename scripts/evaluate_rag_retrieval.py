"""
RAG 检索质量评估脚本

用途：对知识库的「纯向量检索」和「向量 + 知识图谱混合检索」做 A/B 对比，
输出 Recall@k / Hit@k / MRR 等检索指标，作为简历中 RAG 效果的量化证据。

设计说明：
    - 本脚本只评估「检索层」，不包含 rerank 重排序与 LLM 答案生成（那两层受 LLM 波动影响大，难以归因）。
    - 评估时直接用原问题作为检索 query（rewritten_query=None），不引入 LLM 查询改写的随机性，
      从而保证「纯向量 vs 混合」的对比是干净的、可复现的。

用法（在项目根目录运行，需先启动 PostgreSQL 并配置好 .env）：
    1) 列出所有知识库（拿到 kb_id）：
       python scripts/evaluate_rag_retrieval.py --list-kb

    2) 导出某知识库的全部 chunk（带主键 id，用于标注 golden）：
       python scripts/evaluate_rag_retrieval.py --export-chunks 1 --out chunks.txt

    3) 跑检索评估：
       python scripts/evaluate_rag_retrieval.py --kb-id 1 --testset tests/rag_eval_testset.json --top-k 5

测试集格式（JSON 数组）：
    [
      {"question": "能从知识库中找到答案的问题", "golden_chunk_ids": [12, 34]},
      {"question": "第二个问题", "golden_chunk_ids": [3]}
    ]
    golden_chunk_ids 是「能回答该问题」的 chunk 主键（knowledge_chunks.id）。
    先执行第 2 步导出 chunk 列表，从中挑出每个问题对应的正确 chunk id 填入。
"""

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

import app.database as database_module
from app.database import close_db, init_engine
from app.modules.knowledge_base.models import KnowledgeBaseEntity, KnowledgeChunkEntity
from app.modules.knowledge_base.rag_service import knowledge_base_rag_service
from app.modules.knowledge_base.search_channel import (
    MultiChannelRetrievalEngine,
    SearchContext,
    VectorSearchChannel,
)
from app.modules.knowledge_base.vector_service import knowledge_base_vector_service
from app.modules.knowledge_graph.graph_search_channel import GraphSearchChannel


async def list_knowledge_bases() -> None:
    async with database_module.async_session_factory() as session:
        result = await session.execute(
            select(KnowledgeBaseEntity.id, KnowledgeBaseEntity.name, KnowledgeBaseEntity.chunk_count).order_by(
                KnowledgeBaseEntity.id
            )
        )
        rows = result.all()
        if not rows:
            print("暂无知识库，请先上传文档并完成索引。")
            return
        print(f"{'KB_ID':<8}{'chunk_count':<14}name")
        for kb_id, name, chunk_count in rows:
            print(f"{kb_id:<8}{chunk_count or 0:<14}{name}")


async def export_chunks(kb_id: int) -> list[str]:
    lines: list[str] = []
    async with database_module.async_session_factory() as session:
        result = await session.execute(
            select(KnowledgeChunkEntity)
            .where(KnowledgeChunkEntity.knowledge_base_id == kb_id)
            .order_by(KnowledgeChunkEntity.chunk_index)
        )
        chunks = list(result.scalars().all())
        if not chunks:
            print(f"知识库 {kb_id} 暂无 chunk（可能未完成索引）。")
            return lines
        for chunk in chunks:
            content = (chunk.content or "").replace("\n", " ").strip()
            preview = content[:120]
            lines.append(f"[chunk_id={chunk.id}] index={chunk.chunk_index} | {preview}")
    return lines


def compute_metrics(cases: list[dict], retrieved_by_case: list[list[int]], top_k: int) -> dict:
    """计算二元相关性下的 Hit@K、Precision@K、Recall@K、MRR 与 nDCG@K。"""
    hits = 0
    rr_sum = 0.0
    recall_sum = 0.0
    precision_sum = 0.0
    ndcg_sum = 0.0
    valid = 0
    for case, ids in zip(cases, retrieved_by_case):
        golden = set(case.get("golden_chunk_ids") or [])
        if not golden:
            continue
        valid += 1
        overlap = golden & set(ids)
        if overlap:
            hits += 1
        precision_sum += len(overlap) / top_k
        for rank, cid in enumerate(ids, start=1):
            if cid in golden:
                rr_sum += 1.0 / rank
                break
        dcg = sum(1.0 / math.log2(rank + 1) for rank, cid in enumerate(ids, start=1) if cid in golden)
        ideal_count = min(len(golden), top_k)
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcg_sum += dcg / ideal_dcg if ideal_dcg else 0.0
        recall_sum += len(overlap) / len(golden)
    if valid == 0:
        return {"valid": 0, "hit_at_k": 0.0, "precision_at_k": 0.0, "mrr": 0.0, "recall_at_k": 0.0, "ndcg_at_k": 0.0}
    return {
        "valid": valid,
        "hit_at_k": hits / valid,
        "precision_at_k": precision_sum / valid,
        "mrr": rr_sum / valid,
        "recall_at_k": recall_sum / valid,
        "ndcg_at_k": ndcg_sum / valid,
    }


async def _retrieve(engine: MultiChannelRetrievalEngine, kb_id: int, question: str, top_k: int) -> list[int]:
    query_embedding = knowledge_base_vector_service.embed_text(question)
    context = SearchContext(
        question=question,
        kb_id=kb_id,
        top_k=top_k,
        query_embedding=query_embedding,
        rewritten_query=None,  # 固定用原问题，排除 rewrite 随机性
    )
    references = await engine.retrieve(context)
    return [ref.chunk_id for ref in references]


async def evaluate(kb_id: int, testset: list[dict], top_k: int, verbose: bool) -> dict:
    vector_retrieved: list[list[int]] = []
    hybrid_retrieved: list[list[int]] = []

    async with database_module.async_session_factory() as session:
        vector_channel = VectorSearchChannel(knowledge_base_rag_service, knowledge_base_vector_service, session)
        graph_channel = GraphSearchChannel(session)
        vector_engine = MultiChannelRetrievalEngine([vector_channel])
        hybrid_engine = MultiChannelRetrievalEngine([vector_channel, graph_channel])

        for case in testset:
            question = case["question"]
            vector_ids = await _retrieve(vector_engine, kb_id, question, top_k)
            hybrid_ids = await _retrieve(hybrid_engine, kb_id, question, top_k)
            vector_retrieved.append(vector_ids)
            hybrid_retrieved.append(hybrid_ids)

            if verbose:
                golden = case.get("golden_chunk_ids") or []
                print(f"\nQ: {question}")
                print(f"  golden: {golden}")
                print(f"  纯向量 top-{top_k}: {vector_ids}")
                print(f"  混合   top-{top_k}: {hybrid_ids}")

    vector_metrics = compute_metrics(testset, vector_retrieved, top_k)
    hybrid_metrics = compute_metrics(testset, hybrid_retrieved, top_k)
    n = vector_metrics["valid"]
    graph_contrib = sum(1 for v, h in zip(vector_retrieved, hybrid_retrieved) if v != h)

    print("\n" + "=" * 60)
    print("RAG 检索质量评估报告")
    print("=" * 60)
    print(f"知识库 KB_ID : {kb_id}")
    print(f"测试题数     : {len(testset)}（其中 {n} 题带 golden 标注）")
    print(f"检索 Top-K   : {top_k}")

    provider = knowledge_base_vector_service._embedding_provider
    using_real = knowledge_base_vector_service._use_real_embedding
    print(f"向量方式     : {provider}" + ("（真实 embedding）" if using_real else "（哈希降级，仅近似）"))

    print("\n" + "-" * 60)
    print(f"{'指标':<14}{'纯向量':<14}{'混合(向量+图)':<18}{'提升'}")
    print("-" * 60)
    for label, key in [
        ("Hit@k", "hit_at_k"),
        ("Precision@k", "precision_at_k"),
        ("MRR", "mrr"),
        ("Recall@k", "recall_at_k"),
        ("nDCG@k", "ndcg_at_k"),
    ]:
        v = vector_metrics[key]
        h = hybrid_metrics[key]
        diff = h - v
        sign = "+" if diff >= 0 else ""
        print(f"{label:<14}{v:<14.4f}{h:<18.4f}{sign}{diff:.4f}")
    print("-" * 60)
    print(f"图通道贡献题数: {graph_contrib}/{len(testset)}（混合结果与纯向量不同的题数）")

    if graph_contrib == 0:
        print("\n[提示] 混合检索与纯向量结果完全一致，图通道可能未生效：")
        print("       1) 问题中未提取到实体（GraphSearchChannel 需要 LLM 提取实体）；")
        print("       2) LLM 未配置或调用失败；或知识图谱尚未抽取三元组。")

    print("\n量化结论可直接写进简历，例如：")
    print(
        f"  「在自建 {n} 题测试集上，GraphRAG 混合检索较纯向量检索 "
        f"Hit@{top_k} 提升 {(hybrid_metrics['hit_at_k'] - vector_metrics['hit_at_k']) * 100:.1f} 个百分点。」"
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_id": kb_id,
        "top_k": top_k,
        "test_cases": len(testset),
        "valid_cases": n,
        "embedding_provider": provider,
        "using_real_embedding": using_real,
        "graph_changed_cases": graph_contrib,
        "vector": vector_metrics,
        "hybrid": hybrid_metrics,
        "delta": {key: hybrid_metrics[key] - vector_metrics[key] for key in vector_metrics if key != "valid"},
        "cases": [
            {
                "question": case["question"],
                "golden_chunk_ids": case.get("golden_chunk_ids") or [],
                "vector_chunk_ids": vector_ids,
                "hybrid_chunk_ids": hybrid_ids,
            }
            for case, vector_ids, hybrid_ids in zip(testset, vector_retrieved, hybrid_retrieved)
        ],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 检索质量评估")
    parser.add_argument("--list-kb", action="store_true", help="列出所有知识库后退出")
    parser.add_argument("--export-chunks", type=int, metavar="KB_ID", help="导出指定知识库的 chunk 列表后退出")
    parser.add_argument("--out", type=str, help="导出 chunk 时写入的文件路径（默认打印到屏幕）")
    parser.add_argument("--kb-id", type=int, help="要评估的知识库 id")
    parser.add_argument("--testset", type=str, help="测试集 JSON 文件路径")
    parser.add_argument("--top-k", type=int, default=5, help="检索返回的 Top-K（默认 5）")
    parser.add_argument("--verbose", action="store_true", help="打印每题检索明细")
    parser.add_argument("--output", type=str, help="将结构化评测结果写入 JSON 文件")
    args = parser.parse_args()

    init_engine()
    try:
        if args.list_kb:
            await list_knowledge_bases()
            return

        if args.export_chunks is not None:
            lines = await export_chunks(args.export_chunks)
            text = "\n".join(lines)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(text + "\n")
                print(f"已导出 {len(lines)} 个 chunk 到 {args.out}")
            else:
                print(text if text else "（无 chunk）")
            return

        if args.kb_id is None or args.testset is None:
            parser.error("评估模式需要同时提供 --kb-id 和 --testset")

        with open(args.testset, "r", encoding="utf-8") as f:
            testset = json.load(f)
        if not isinstance(testset, list) or not testset:
            print("测试集格式错误：应为非空 JSON 数组。", file=sys.stderr)
            sys.exit(1)

        result = await evaluate(args.kb_id, testset, args.top_k, args.verbose)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"\n结构化结果已写入: {args.output}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())

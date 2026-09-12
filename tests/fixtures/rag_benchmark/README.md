# RAG 基准语料使用说明

1. 在知识库页面上传 `offerpilot_rag_benchmark.md`，建议名称为“RAG 检索评测基准语料”。
2. 等待索引状态变为 `COMPLETED`，记录知识库 ID。
3. 运行 `python scripts/evaluate_rag_retrieval.py --export-chunks <KB_ID> --out tests/fixtures/rag_benchmark/chunks.txt`。
4. 根据导出的 chunk 内容，把 `question_bank.json` 中每题对应的正确 chunk ID 写入最终测试集，再执行评测。

测试语料与问题库均为人工编写的公开技术内容，可安全提交和公开展示。

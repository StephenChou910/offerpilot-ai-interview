# Staging 数据库基线说明

当前历史 Alembic 迁移从 `001` 开始时假设业务表已经存在，不能直接用于全新空库初始化。

对于全新 staging 数据库执行：

```powershell
$env:POSTGRES_DB = "interview_guide_staging"
.\.venv\Scripts\python.exe scripts/bootstrap_staging.py
.\.venv\Scripts\python.exe -m alembic stamp 008_scope_kb_hash_user
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

预期版本为 `010_llm_call_audits (head)`。开发库不执行重置或删除操作。

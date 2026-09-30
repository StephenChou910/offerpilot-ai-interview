"""Bootstrap a clean staging database when legacy migrations have no base schema.

Usage: set POSTGRES_DB to an empty staging database, run this script once, then
run `alembic stamp 008_scope_kb_hash_user` and `alembic upgrade head`.
"""
import asyncio
import sqlalchemy as sa
import app.database as database
from app.models.base import Base


def import_models() -> None:
    import app.common.llm_audit_models  # noqa: F401
    import app.common.task_models  # noqa: F401
    import app.modules.agent_orchestration.models  # noqa: F401
    import app.modules.auth.models  # noqa: F401
    import app.modules.interview.models  # noqa: F401
    import app.modules.knowledge_base.models  # noqa: F401
    import app.modules.knowledge_graph.models  # noqa: F401
    import app.modules.organization.models  # noqa: F401
    import app.modules.resume.models  # noqa: F401
    import app.modules.training.models  # noqa: F401


async def main() -> None:
    import_models()
    database.init_engine()
    async with database.engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)


if __name__ == "__main__":
    asyncio.run(main())

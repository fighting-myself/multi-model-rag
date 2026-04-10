"""
向数据库写入默认多智能体工具
用法:
  python scripts/seed_agent_tools.py
"""
from __future__ import annotations

import asyncio

from app.core.database import AsyncSessionLocal
from app.services.agent_tool_registry_service import seed_default_agent_tools


async def main() -> None:
    async with AsyncSessionLocal() as db:
        changed = await seed_default_agent_tools(db)
        print(f"seed done, upserted={changed}")


if __name__ == "__main__":
    asyncio.run(main())

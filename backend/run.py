#!/usr/bin/env python3
"""
后端启动脚本。在 Windows 上必须先设置 ProactorEventLoop 再启动 uvicorn，
否则 Playwright/浏览器助手会因 create_subprocess_exec 触发 NotImplementedError。
用法:
  cd backend && python run.py
  cd backend && python run.py --reload --port 8000
  或在项目根目录: python backend/run.py
"""
import asyncio
import os
import sys


def main() -> None:
    # 保证从 backend 目录启动，便于解析 app.main:app
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.getcwd() != script_dir:
        os.chdir(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    import uvicorn
    # 解析简单参数，默认与 uvicorn app.main:app 一致
    reload = "--reload" in sys.argv or "-r" in sys.argv
    port = 8000
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass
            break
        if a.startswith("--port="):
            try:
                port = int(a.split("=", 1)[1])
            except ValueError:
                pass
            break
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()

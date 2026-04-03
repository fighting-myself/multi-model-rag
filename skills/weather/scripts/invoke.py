#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from urllib.parse import quote


def _load_args() -> dict:
    if len(sys.argv) < 2:
        return {}
    try:
        obj = json.loads(sys.argv[1])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _run(args: dict) -> str:
    loc = str(args.get("location") or args.get("city") or args.get("q") or "").strip()
    if not loc:
        return '错误: 请提供 location（或 city），例如 skill_args={"location":"Shanghai"}。'
    try:
        import httpx
    except ImportError:
        return "错误: 未安装 httpx，无法查询天气。"
    url = f"https://wttr.in/{quote(loc, safe='')}?format=3"
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers={"User-Agent": "curl/7.68 (skill weather)"})
            r.raise_for_status()
            return (r.text or "").strip() or "[wttr.in 无正文返回]"
    except Exception as e:
        return f"天气查询失败: {e}"


def main() -> None:
    out = _run(_load_args())
    data = (out if isinstance(out, str) else str(out)).encode("utf-8", errors="replace")
    try:
        sys.stdout.buffer.write(data + b"\n")
    except Exception:
        # 退回普通打印，避免极端环境无 buffer
        print(out)


if __name__ == "__main__":
    main()

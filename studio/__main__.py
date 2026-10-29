"""``python -m studio`` 的入口。

历史上这个入口启动的是 Gradio 单页界面（端口 7870，代码见已下线的 ``ui.py``）。
那套界面已随 schema v5 改造移除，现在与 ``python -m studio.api`` 等价，启动新系统的后端 API；
前端在 ``frontend/`` 目录，用 ``npm run dev`` 启动（或双击 ``start_studio.cmd`` 一键起前后端）。
"""
from __future__ import annotations

import sys


def main() -> None:
    # 复用 api.py 的参数解析（--host / --port，默认 127.0.0.1:7871），
    # 延迟导入让 `python -m studio --help` 不必加载 torch。
    from .api import main as api_main

    # 只是把 -m studio 的 --help 描述说得更清楚些，参数本身完全交给 api_main。
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print("Qwen3 TTS Voiceover Studio — backend API (frontend: cd frontend && npm run dev)\n")
    api_main()


if __name__ == "__main__":
    main()

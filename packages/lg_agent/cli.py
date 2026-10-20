"""그래프를 터미널에서 한 번 돌린다.

    python -m lg_agent.cli "장바구니 합계를 구하는 함수"
"""
from __future__ import annotations

import argparse
import asyncio
import time
import uuid

from lg_agent.deps import build_deps
from lg_agent.graph import build_graph, create_checkpointer, initial_state
from lg_agent.settings import load_settings


async def main(title: str) -> None:
    settings = load_settings()
    requirement_id = f"REQ-LG-{uuid.uuid4().hex[:8]}"
    config = {
        "configurable": {"thread_id": requirement_id},
        "run_name": "vsi-graph",
        "metadata": {"requirement_id": requirement_id, "impl": "langgraph"},
    }
    started = time.monotonic()
    async with create_checkpointer(settings.checkpoint_url) as saver:
        graph = build_graph(build_deps(settings), saver)
        async for mode, chunk in graph.astream(
            initial_state(requirement_id, title, settings.max_revisions),
            config, stream_mode=["updates", "custom"],
        ):
            if mode == "custom":
                print(f"   · {chunk}")
            else:
                for node, update in chunk.items():
                    status = (update or {}).get("status", "") if isinstance(update, dict) else ""
                    print(f"[{time.monotonic() - started:6.1f}s] {node} → {status}")
        final = (await graph.aget_state(config)).values
    print(f"\n{requirement_id}: {final['status']} (회차 {final['revision']}, "
          f"{time.monotonic() - started:.1f}s)")
    for v in final.get("verdicts", {}).values():
        print(f"  {v['agent']}: {v['verdict']} — {v['summary'][:200]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("title")
    asyncio.run(main(parser.parse_args().title))

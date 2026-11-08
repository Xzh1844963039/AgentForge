"""Phase 3 memory evolution controlled experiment.

Run:
    python examples/memory_demo.py

This uses deterministic Mock LLM decisions and REAL private-workspace file tools.
It verifies the memory pipeline (reflection → storage → retrieval → action change
→ feedback). It does not claim real-LLM learning performance.
"""

from __future__ import annotations

import asyncio
import json

from agentforge.config import Settings
from agentforge.memory.experiment import MemoryExperimentRunner


async def main() -> int:
    runner = MemoryExperimentRunner(
        Settings(_env_file=None, llm_provider="mock", log_level="WARNING"),
        learning_runs=2,
    )
    result = await runner.run()
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.pipeline_verified else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""LOCAL UI DEVELOPMENT HARNESS - uses the test-only FakeLLM (answers derived from ground truth).

Lets you click through the console without Gemini credentials. It is NOT the product's behaviour:
the deployed service always calls Gemini. Run:  python -m tests.ui_harness  (http://127.0.0.1:8090)
"""
import os

os.environ["AUTH_MODE"] = "none"
os.environ["STORE_BACKEND"] = "memory"
os.environ["USE_EMBEDDINGS"] = "false"

import asyncio  # noqa: E402

import uvicorn  # noqa: E402

from app.agents import llm as llm_mod  # noqa: E402
from tests.conftest import FakeLLM  # noqa: E402


class SlowFake(FakeLLM):
    async def generate_json(self, **kw):
        await asyncio.sleep(1.2)  # make the live trace visible
        return await super().generate_json(**kw)


if __name__ == "__main__":
    llm_mod.set_llm(SlowFake())
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8090")))

"""Isolated browser-test server: fake inference, temporary chat storage, no index writes."""

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
import config
import api.server as server
from test_webapp import FakeChain, FakeEvaluator, FakeStore


class BrowserChain(FakeChain):
    async def astream(self, values):
        if values['question'] == 'Stop this request':
            yield 'Partial answer [FB-014].'
            await asyncio.sleep(30)
        async for token in super().astream(values):
            yield token


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        config.CHAT_HISTORY_PATH = Path(directory) / "chats.sqlite3"
        chain = BrowserChain()
        server.create_engine = lambda: SimpleNamespace(store=FakeStore(), chain=chain, evaluator=FakeEvaluator(chain))
        uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="warning")

import asyncio
import shutil
from pathlib import Path

import pytest

from app.config import CONFIG_DIR, load_settings
from app.monitoring.events import EventBus, History, Metrics
from app.robot.sim import SimAdapter
from app.safety.gate import Gate
from app.skills.registry import Registry


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    return d


@pytest.fixture
def settings(cfg_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ROBOT_MODE", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "test-key-not-real")
    return load_settings("sim", config_dir=cfg_dir, data_dir=tmp_path / "data", env_file=None)


@pytest.fixture
def registry(settings):
    return Registry(settings)


@pytest.fixture
def sim(settings):
    return SimAdapter(settings.poses["home"]["joints"], time_scale=20.0)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def gate(settings, registry, sim):
    g = Gate(settings, registry, sim, EventBus(), Metrics(path=None), History())
    await g.start()
    yield g
    await g.shutdown()


async def say(gate: Gate, text: str):
    """Operator utterance as the voice path delivers it: commit, then transcript."""
    gate.note_turn_committed("voice")
    await asyncio.sleep(0)
    return gate.note_transcript(text, source="voice")


async def wait_state(gate: Gate, state: str, timeout: float = 5.0):
    t = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < t:
        if gate.state.value == state:
            return True
        await asyncio.sleep(0.01)
    return False

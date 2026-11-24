import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

REAL_DISCORD = importlib.util.find_spec("discord") is not None
if not REAL_DISCORD:
    import discord_stub
    discord_stub.install()

"""A permissive stand-in for discord.py, used ONLY when the real library is not installed.

It lets the test-suite import every cog (executing all module-level code, class
bodies and decorators) to catch NameErrors, typos in decorators and syntax
problems. It cannot verify real Discord behaviour: that needs live testing.
"""
from __future__ import annotations

import sys
import types


class _Any:
    def __call__(self, *a, **k):
        if len(a) == 1 and callable(a[0]) and not k:
            return a[0]
        return _Any()

    def __getattr__(self, name):
        return _Any()

    def __getitem__(self, item):
        return _Any()

    def __iter__(self):
        return iter(())


class _Meta(type):
    def __getattr__(cls, name):
        return _Any()

    def __getitem__(cls, item):
        return cls


class Base(metaclass=_Meta):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Any()


class _Module(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name[0].islower():            # decorators / helpers such as ui.button, app_commands.command
            return _Any()
        cls = _Meta(name, (Base,), {})
        setattr(self, name, cls)
        return cls


def install() -> None:
    names = ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands", "discord.ui"]
    mods = {n: _Module(n) for n in names}
    mods["discord"].ext = mods["discord.ext"]
    mods["discord"].app_commands = mods["discord.app_commands"]
    mods["discord"].ui = mods["discord.ui"]
    mods["discord.ext"].commands = mods["discord.ext.commands"]
    mods["discord.app_commands"].check = lambda pred: (lambda f: f)
    for n, m in mods.items():
        sys.modules[n] = m

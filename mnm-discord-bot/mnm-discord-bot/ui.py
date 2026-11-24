"""Shared visual identity and interaction helpers for the MNM bot.

Design language: minimal, scientific, one consistent colour palette, each
laboratory with its own accent colour.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Sequence

import discord

from config import WEBSITE_URL

log = logging.getLogger("mnm.ui")

BRAND = 0x3A86FF
SUCCESS = 0x2ECC71
ERROR = 0xE5484D
WARNING = 0xF5A524
MUTED = 0x6B7280

LAB_META: dict[str, dict[str, Any]] = {
    "neuro": {"name": "Neuron Lab", "subject": "Neuroscience", "emoji": "🧠", "color": 0x2EC4B6,
              "tagline": "An interactive neuroscience exploration environment.",
              "footer": "Neuron Lab · simplified educational models"},
    "math": {"name": "Theorem Lab", "subject": "Mathematics", "emoji": "📐", "color": 0x8B5CF6,
             "tagline": "A mathematical research and problem-solving environment.",
             "footer": "Theorem Lab · answer keys are checked with Python, not official solutions"},
    "med": {"name": "Vitalis Lab", "subject": "Medicine", "emoji": "🧬", "color": 0xEF476F,
            "tagline": "An educational biomedical science exploration environment.",
            "footer": "Vitalis Lab · educational content only, not medical advice"},
}

FOOTER = "MediNeuroMatics"


def clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def make_embed(title: Optional[str] = None, description: Optional[str] = None, *,
               color: int = BRAND, footer: Optional[str] = None,
               fields: Sequence[tuple[str, str, bool]] = (), thumbnail: Optional[str] = None,
               url: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title=clip(title, 256) if title else None,
                          description=clip(description, 4000) if description else None,
                          color=color, url=url)
    for name, value, inline in fields[:25]:
        embed.add_field(name=clip(name, 256), value=clip(value, 1024) or "—", inline=inline)
    embed.set_footer(text=clip(footer or FOOTER, 2000))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def error_embed(message: str, title: str = "Something needs attention") -> discord.Embed:
    return make_embed(f"⚠️ {title}", message, color=ERROR)


def success_embed(message: str, title: str = "Done") -> discord.Embed:
    return make_embed(f"✅ {title}", message, color=SUCCESS)


def info_embed(message: str, title: str) -> discord.Embed:
    return make_embed(title, message, color=BRAND)


def lab_footer(lab: str, extra: Optional[str] = None) -> str:
    base = LAB_META[lab]["footer"]
    return f"{extra} · {base}" if extra else base


# ----------------------------------------------------------------- responses
async def send_error(interaction: discord.Interaction, message: str) -> None:
    """Best-effort ephemeral error reply that works before or after a response."""
    embed = error_embed(message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        log.debug("Could not deliver error message", exc_info=True)


async def present(interaction: discord.Interaction, *, embed: Optional[discord.Embed] = None,
                  view: Optional[discord.ui.View] = None, files: Optional[list[discord.File]] = None,
                  ephemeral: bool = True, edit: bool = False,
                  replaces: Optional[discord.ui.View] = None) -> None:
    """Show an embed/view, editing the current message or sending a new one.

    ``replaces`` is stopped first so its timeout cannot overwrite the new message.
    When ``edit`` is requested but not possible (e.g. slash-command interaction) it
    falls back to sending a message.
    """
    if replaces is not None:
        replaces.stop()
    if edit and not interaction.response.is_done():
        try:
            await interaction.response.edit_message(embed=embed, view=view, attachments=files or [])
            _remember_editor(view, lambda: interaction.edit_original_response(view=view))
            return
        except (discord.DiscordException, TypeError, AttributeError):
            log.debug("edit_message failed, falling back to a new message", exc_info=True)
    kwargs: dict[str, Any] = {"ephemeral": ephemeral}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if files:
        kwargs["files"] = files
    if interaction.response.is_done():
        msg = await interaction.followup.send(wait=True, **kwargs)
        _remember_editor(view, lambda: msg.edit(view=view))
    else:
        await interaction.response.send_message(**kwargs)
        _remember_editor(view, lambda: interaction.edit_original_response(view=view))


def _remember_editor(view: Optional[discord.ui.View], factory: Callable[[], Awaitable[Any]]) -> None:
    if isinstance(view, BaseView):
        view._refresh = factory


class BaseView(discord.ui.View):
    """View with owner-only interaction checks, tidy timeouts and error reporting."""

    def __init__(self, owner_id: Optional[int] = None, *, timeout: Optional[float] = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self._refresh: Optional[Callable[[], Awaitable[Any]]] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This menu belongs to someone else. Run the command to open your own.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True  # type: ignore[attr-defined]
        if self._refresh is not None:
            try:
                await self._refresh()
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.exception("Error in view %s", type(self).__name__, exc_info=error)
        await send_error(interaction, "Something went wrong with that button. Please try the command again.")


class CallbackButton(discord.ui.Button):
    """Button that awaits ``callback(interaction)``."""

    def __init__(self, label: str, callback: Callable[[discord.Interaction], Awaitable[None]], *,
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary,
                 emoji: Optional[str] = None, row: Optional[int] = None):
        super().__init__(label=label, style=style, emoji=emoji, row=row)
        self._cb = callback

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self._cb(interaction)


def website_button(label: str = "Website") -> discord.ui.Button:
    return discord.ui.Button(label=label, url=WEBSITE_URL, style=discord.ButtonStyle.link)


def discord_timestamp(dt, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"

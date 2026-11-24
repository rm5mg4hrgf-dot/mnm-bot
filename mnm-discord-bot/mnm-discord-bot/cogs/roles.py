"""Academic interest roles (self-service) and MNM team role definitions."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ui import BaseView, BRAND, make_embed, present, send_error, success_embed

log = logging.getLogger("mnm.roles")

INTEREST_ROLES = [
    ("neuroscience", "Neuroscience", "🧠"),
    ("medicine", "Medicine", "🧬"),
    ("mathematics", "Mathematics", "📐"),
    ("research", "Research", "🔬"),
    ("competitions", "Competitions", "🏆"),
    ("technology", "Technology", "💻"),
]
TEAM_ROLES = [
    ("programs-curriculum", "Programs & Curriculum"),
    ("web-tech", "Web & Tech"),
    ("media-content", "Media & Content"),
    ("design-brand", "Design & Brand"),
    ("partnerships", "Partnerships"),
    ("volunteering", "Volunteering"),
]
INTEREST_LABELS = {k: (n, e) for k, n, e in INTEREST_ROLES}

_DANGEROUS = discord.Permissions(
    administrator=True, manage_guild=True, manage_roles=True, manage_channels=True, kick_members=True,
    ban_members=True, moderate_members=True, manage_messages=True, mention_everyone=True,
    manage_webhooks=True, manage_nicknames=True,
)


def role_is_safe(role: discord.Role) -> bool:
    """A role may only be self-assigned/mapped if it carries no moderation-level permissions."""
    return not any(enabled and getattr(role.permissions, name) for name, enabled in _DANGEROUS)


class RoleSelect(discord.ui.Select):
    def __init__(self, member: discord.Member, mapping: dict[str, int]):
        owned = {r.id for r in member.roles}
        options = [
            discord.SelectOption(label=name, value=key, emoji=emoji, default=mapping.get(key) in owned)
            for key, name, emoji in INTEREST_ROLES if key in mapping
        ]
        super().__init__(placeholder="Pick your academic interests…", min_values=0,
                         max_values=len(options), options=options)
        self.mapping = mapping

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        member = interaction.user
        guild = interaction.guild
        if not isinstance(member, discord.Member) or guild is None:
            return
        chosen = set(self.values)
        to_add, to_remove, skipped = [], [], []
        me = guild.me
        for key, role_id in self.mapping.items():
            role = guild.get_role(role_id)
            if role is None:
                continue
            if not role_is_safe(role) or role >= me.top_role:
                skipped.append(role.name)
                continue
            has = role in member.roles
            if key in chosen and not has:
                to_add.append(role)
            elif key not in chosen and has:
                to_remove.append(role)
        try:
            if to_add:
                await member.add_roles(*to_add, reason="MNM interest role selection")
            if to_remove:
                await member.remove_roles(*to_remove, reason="MNM interest role selection")
        except discord.Forbidden:
            await send_error(interaction, "I don't have permission to change roles. Ask an admin to move my "
                                          "role above the interest roles.")
            return
        names = [INTEREST_LABELS[k][0] for k in INTEREST_LABELS if k in chosen and k in self.mapping]
        text = ("Your interests: " + ", ".join(f"**{n}**" for n in names)) if names else "No interest roles selected."
        if skipped:
            text += f"\n\nSkipped (not safe to assign): {', '.join(skipped)}"
        await present(interaction, embed=success_embed(text, "Roles updated"), view=None, edit=True)
        if self.view is not None:
            self.view.stop()


class RolePickerView(BaseView):
    def __init__(self, member: discord.Member, mapping: dict[str, int]):
        super().__init__(member.id, timeout=180)
        self.add_item(RoleSelect(member, mapping))


class RolePanelView(discord.ui.View):
    """Persistent panel posted by an admin."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Choose your interests", style=discord.ButtonStyle.primary, emoji="🎓",
                       custom_id="mnm:roles:open")
    async def open_picker(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog: Optional["RolesCog"] = interaction.client.get_cog("RolesCog")  # type: ignore[assignment]
        if cog is None:
            await send_error(interaction, "Roles are unavailable right now.")
            return
        await cog.open_picker(interaction)


def panel_embed() -> discord.Embed:
    lines = "\n".join(f"{e} **{n}**" for _, n, e in INTEREST_ROLES)
    return make_embed("🎓 Choose your academic interests",
                      "Pick the roles that match what you want to explore. You can change them any time.\n\n" + lines,
                      color=BRAND)


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(RolePanelView())

    async def open_picker(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await send_error(interaction, "Please use this inside the server.")
            return
        mapping = await self.bot.db.roles("interest")  # type: ignore[attr-defined]
        mapping = {k: v for k, v in mapping.items() if member.guild.get_role(v) is not None}
        if not mapping:
            await send_error(interaction, "Interest roles aren't set up yet. An admin can run `/mnm setup-roles`.")
            return
        await present(interaction, embed=make_embed(
            "🎓 Academic interests", "Select every area you are interested in, then confirm by choosing.",
            color=BRAND), view=RolePickerView(member, mapping))

    @app_commands.command(name="roles", description="Choose your academic interest roles.")
    @app_commands.guild_only()
    async def roles(self, interaction: discord.Interaction) -> None:
        await self.open_picker(interaction)

    async def setup_interest_roles(self, guild: discord.Guild) -> tuple[list[str], list[str]]:
        """Create any missing interest roles (no permissions) and save the mapping."""
        created, reused = [], []
        existing = {r.name.lower(): r for r in guild.roles}
        for key, name, _ in INTEREST_ROLES:
            role = existing.get(name.lower())
            if role is None:
                role = await guild.create_role(name=name, permissions=discord.Permissions.none(),
                                               mentionable=False, reason="MNM interest role setup")
                created.append(name)
            elif not role_is_safe(role):
                log.warning("Existing role %s has elevated permissions; not mapped", name)
                continue
            else:
                reused.append(name)
            await self.bot.db.set_role("interest", key, role.id)  # type: ignore[attr-defined]
        return created, reused


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))

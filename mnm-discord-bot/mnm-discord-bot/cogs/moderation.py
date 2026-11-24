"""Moderation: warnings, timeouts, bans, anti-spam, content filter and audit logging."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from permissions import is_staff, staff_only
from services.moderation_service import (
    AUTO_TIMEOUT_MINUTES, AUTO_TIMEOUT_WARNINGS, MAX_TIMEOUT_MINUTES, ContentFilter, SpamTracker,
    can_moderate,
)
from ui import ERROR, WARNING, BRAND, make_embed, success_embed

log = logging.getLogger("mnm.moderation")


class ModerationCog(commands.Cog):
    mod = app_commands.Group(name="mod", description="Moderation tools (staff only).", guild_only=True,
                             default_permissions=discord.Permissions(moderate_members=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.spam = SpamTracker()
        self.filter = ContentFilter()

    async def cog_load(self) -> None:
        self.filter.set_words(await self.bot.db.filter_words())  # type: ignore[attr-defined]

    async def reload_filter(self) -> None:
        self.filter.set_words(await self.bot.db.filter_words())  # type: ignore[attr-defined]

    # ------------------------------------------------------------- helpers
    def _check_target(self, interaction: discord.Interaction, target: discord.Member) -> Optional[str]:
        actor = interaction.user
        guild = interaction.guild
        assert isinstance(actor, discord.Member) and guild is not None
        return can_moderate(
            actor.top_role.position, target.top_role.position,
            actor_is_owner=guild.owner_id == actor.id, target_is_owner=guild.owner_id == target.id,
            target_is_bot=target.bot, same_user=actor.id == target.id,
            bot_top_position=guild.me.top_role.position)

    async def _audit(self, action: str, target: discord.abc.User, moderator: discord.abc.User,
                     reason: str, color: int = WARNING, extra: str = "") -> None:
        embed = make_embed(f"🛡️ {action}", None, color=color, fields=[
            ("Member", f"{target.mention} (`{target.id}`)", True),
            ("Moderator", moderator.mention, True),
            ("Reason", reason or "No reason given", False),
        ] + ([("Details", extra, False)] if extra else []))
        await self.bot.log_event(embed)  # type: ignore[attr-defined]

    async def _dm(self, member: discord.abc.User, text: str) -> bool:
        try:
            await member.send(text)
            return True
        except discord.HTTPException:
            return False

    # ------------------------------------------------------------- commands
    @mod.command(name="warn", description="Warn a member.")
    @staff_only()
    @app_commands.describe(member="Member to warn", reason="Why they are being warned")
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 3, 300]) -> None:
        error = self._check_target(interaction, member)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        db = self.bot.db  # type: ignore[attr-defined]
        await db.add_mod_action(member.id, interaction.user.id, "warn", reason)
        count = len(await db.active_warnings(member.id))
        dm_ok = await self._dm(member, f"You received a warning in **{interaction.guild.name}**: {reason}\n"
                                       f"Active warnings: {count}")
        note = ""
        if count >= AUTO_TIMEOUT_WARNINGS:
            try:
                await member.timeout(timedelta(minutes=AUTO_TIMEOUT_MINUTES),
                                     reason=f"Auto-timeout: {count} active warnings")
                await db.add_mod_action(member.id, self.bot.user.id, "timeout", "Automatic: warning threshold",
                                        AUTO_TIMEOUT_MINUTES)
                note = f"\nReached {count} warnings: timed out for {AUTO_TIMEOUT_MINUTES} minutes."
            except discord.HTTPException:
                note = "\nWarning threshold reached but I could not apply the timeout."
        await self._audit("Warning", member, interaction.user, reason, extra=f"Active warnings: {count}{note}")
        await interaction.response.send_message(embed=success_embed(
            f"Warned {member.mention} ({count} active).{note}" + ("" if dm_ok else "\nCould not DM the member."),
            "Warning issued"), ephemeral=True)

    @mod.command(name="warnings", description="Show a member's moderation history.")
    @staff_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        db = self.bot.db  # type: ignore[attr-defined]
        active = await db.active_warnings(member.id)
        recent = await db.recent_actions(member.id, 10)
        lines = [f"`#{r['id']}` {r['action']}{'' if r['active'] else ' (cleared)'}: {r['reason'] or '-'} "
                 f"({r['created_at'][:10]})" for r in recent] or ["No moderation history."]
        await interaction.response.send_message(embed=make_embed(
            f"History for {member.display_name}", "\n".join(lines), color=BRAND,
            fields=[("Active warnings", str(len(active)), True)]), ephemeral=True)

    @mod.command(name="clear-warnings", description="Clear a member's active warnings.")
    @staff_only()
    async def clear_warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        cleared = await self.bot.db.clear_warnings(member.id)  # type: ignore[attr-defined]
        await self._audit("Warnings cleared", member, interaction.user, f"{cleared} warning(s) cleared")
        await interaction.response.send_message(embed=success_embed(f"Cleared {cleared} warning(s)."), ephemeral=True)

    @mod.command(name="timeout", description="Time a member out.")
    @staff_only()
    @app_commands.describe(minutes="Duration in minutes (max 40320 = 28 days)", reason="Reason")
    async def timeout(self, interaction: discord.Interaction, member: discord.Member,
                      minutes: app_commands.Range[int, 1, MAX_TIMEOUT_MINUTES],
                      reason: app_commands.Range[str, 3, 300]) -> None:
        error = self._check_target(interaction, member)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"{interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to time out that member.", ephemeral=True)
            return
        await self.bot.db.add_mod_action(member.id, interaction.user.id, "timeout", reason, minutes)  # type: ignore[attr-defined]
        await self._audit("Timeout", member, interaction.user, reason, extra=f"Duration: {minutes} minutes")
        await interaction.response.send_message(embed=success_embed(
            f"{member.mention} timed out for {minutes} minutes.", "Timeout applied"), ephemeral=True)

    @mod.command(name="untimeout", description="Remove a member's timeout.")
    @staff_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None, reason=f"Removed by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to change that member.", ephemeral=True)
            return
        await self._audit("Timeout removed", member, interaction.user, "")
        await interaction.response.send_message(embed=success_embed(f"Timeout removed for {member.mention}."),
                                                ephemeral=True)

    @mod.command(name="ban", description="Ban a member.")
    @staff_only()
    @app_commands.describe(delete_days="Days of their messages to delete (0-7)")
    async def ban(self, interaction: discord.Interaction, member: discord.Member,
                  reason: app_commands.Range[str, 3, 300],
                  delete_days: app_commands.Range[int, 0, 7] = 0) -> None:
        error = self._check_target(interaction, member)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await self._dm(member, f"You were banned from **{interaction.guild.name}**: {reason}")
        try:
            await interaction.guild.ban(member, reason=f"{interaction.user}: {reason}",
                                        delete_message_seconds=delete_days * 86400)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to ban that member.", ephemeral=True)
            return
        await self.bot.db.add_mod_action(member.id, interaction.user.id, "ban", reason)  # type: ignore[attr-defined]
        await self._audit("Ban", member, interaction.user, reason, color=ERROR)
        await interaction.response.send_message(embed=success_embed(f"Banned **{member}**.", "Member banned"),
                                                ephemeral=True)

    @mod.command(name="unban", description="Unban a user by their ID.")
    @staff_only()
    async def unban(self, interaction: discord.Interaction, user_id: str,
                    reason: app_commands.Range[str, 3, 300]) -> None:
        if not user_id.isdigit():
            await interaction.response.send_message("Provide the numeric user ID.", ephemeral=True)
            return
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=f"{interaction.user}: {reason}")
        except discord.NotFound:
            await interaction.response.send_message("That user isn't banned or doesn't exist.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to unban.", ephemeral=True)
            return
        await self.bot.db.add_mod_action(user.id, interaction.user.id, "unban", reason)  # type: ignore[attr-defined]
        await self._audit("Unban", user, interaction.user, reason)
        await interaction.response.send_message(embed=success_embed(f"Unbanned **{user}**."), ephemeral=True)

    # ----------------------------------------------------- automatic actions
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        if message.author.bot or message.guild is None or message.guild.id != cfg.guild_id:
            return
        author = message.author
        if not isinstance(author, discord.Member) or is_staff(author, cfg):
            return
        hit = self.filter.find(message.content) if message.content else None
        verdict = None if hit else self.spam.check(author.id, message.content, len(message.mentions))
        if not hit and not (verdict and verdict.spam):
            return
        reason = f"Blocked word: {hit}" if hit else verdict.reason  # type: ignore[union-attr]
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        if hit:
            try:
                await message.channel.send(f"{author.mention}, that message was removed (content filter).",
                                           delete_after=8, allowed_mentions=discord.AllowedMentions(users=[author]))
            except discord.HTTPException:
                pass
            await self._audit("Content filter", author, self.bot.user, reason)
            return
        self.spam.reset(author.id)
        try:
            await author.timeout(timedelta(minutes=5), reason=f"Anti-spam: {reason}")
            action = "5-minute timeout applied"
        except discord.HTTPException:
            action = "message removed (could not time out)"
        await self.bot.db.add_mod_action(author.id, self.bot.user.id, "timeout", f"Anti-spam: {reason}", 5)  # type: ignore[attr-defined]
        await self._audit("Anti-spam", author, self.bot.user, reason, extra=action)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))

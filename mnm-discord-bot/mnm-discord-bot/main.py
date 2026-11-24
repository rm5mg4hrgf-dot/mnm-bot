"""MediNeuroMatics (MNM) official Discord bot: entry point.

Run with:  python main.py
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import Config, ConfigError, WEBSITE_URL, load_config
from database import Database
from services.content_service import ContentError, load_content
from services.openai_service import AIService
from ui import send_error

log = logging.getLogger("mnm")

EXTENSIONS = (
    "cogs.welcome",
    "cogs.roles",
    "cogs.tickets",
    "cogs.moderation",
    "cogs.laboratories",
    "cogs.ai_assistant",
    "cogs.challenges",
    "cogs.leaderboard",
    "cogs.showcase",
    "cogs.general",
    "cogs.admin",
)


class RedactingFilter(logging.Filter):
    """Replace any known secret value that appears in a log record with ***."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            message = record.getMessage()
            redacted = message
            for secret in self.secrets:
                redacted = redacted.replace(secret, "***")
            if redacted != message:
                record.msg, record.args = redacted, ()
        return True


def setup_logging(config: Config) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    handler.addFilter(RedactingFilter(config.secrets()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(config.log_level)
    # httpx logs request URLs at INFO; keep third-party noise down.
    for noisy in ("httpx", "httpcore", "openai", "discord.http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class MNMBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.members = True          # welcome messages, role handling, moderation
        intents.message_content = True  # anti-spam and content filter
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
            activity=discord.Activity(type=discord.ActivityType.watching, name="/lab · mnmstem.study"),
        )
        self.config = config
        self.db = Database(config.database_path)
        self.ai: Optional[AIService] = None
        self.content = None
        self._log_channel: Optional[discord.abc.Messageable] = None
        self.tree.on_error = self.on_tree_error  # type: ignore[assignment]

    # --------------------------------------------------------------- startup
    async def setup_hook(self) -> None:
        await self.db.connect()
        self.content = load_content()
        self.ai = AIService(self.config, self.db)
        for message in self.config.warnings:
            log.warning(message)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d slash commands to guild %s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d guild(s). Website: %s",
                 self.user, getattr(self.user, "id", "?"), len(self.guilds), WEBSITE_URL)
        if self.get_guild(self.config.guild_id) is None:
            log.error("The bot is not a member of GUILD_ID=%s. Invite it to the server.", self.config.guild_id)

    async def close(self) -> None:
        log.info("Shutting down…")
        await self.db.close()
        await super().close()

    # --------------------------------------------------------------- helpers
    @property
    def guild_obj(self) -> Optional[discord.Guild]:
        return self.get_guild(self.config.guild_id)

    async def get_channel_safe(self, channel_id: Optional[int]):
        if not channel_id:
            return None
        channel = self.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.fetch_channel(channel_id)
        except discord.HTTPException:
            log.warning("Channel %s could not be found or accessed", channel_id)
            return None

    async def log_event(self, embed: discord.Embed, *, file: Optional[discord.File] = None) -> None:
        """Send an embed to the configured log channel; never raises."""
        channel = await self.get_channel_safe(self.config.log_channel_id)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, file=file) if file else await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not write to the log channel", exc_info=True)

    # ----------------------------------------------------------- slash errors
    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure) and not isinstance(
                error, (app_commands.CommandOnCooldown, app_commands.MissingPermissions)):
            await send_error(interaction, str(error) or "You are not allowed to use this command.")
        elif isinstance(error, app_commands.CommandOnCooldown):
            await send_error(interaction, f"Slow down: try again in {error.retry_after:.0f}s.")
        elif isinstance(error, app_commands.MissingPermissions):
            await send_error(interaction, "You do not have permission to use this command.")
        else:
            name = interaction.command.qualified_name if interaction.command else "unknown"
            log.error("Unhandled error in /%s", name, exc_info=error)
            await send_error(interaction, "Something went wrong. The team has been notified in the logs.")


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:  # python-dotenv is optional when variables come from the host panel
        pass
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[MNM] {exc}", file=sys.stderr)
        sys.exit(2)
    setup_logging(config)
    try:
        load_content()
    except ContentError as exc:
        log.critical("Content error: %s", exc)
        sys.exit(2)
    bot = MNMBot(config)
    try:
        bot.run(config.discord_token, log_handler=None, reconnect=True)
    except discord.LoginFailure:
        log.critical("Discord rejected the bot token. Check DISCORD_TOKEN.")
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        log.critical("Enable 'Server Members Intent' and 'Message Content Intent' in the Developer Portal.")
        sys.exit(1)


if __name__ == "__main__":
    main()

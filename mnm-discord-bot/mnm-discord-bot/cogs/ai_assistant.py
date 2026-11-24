"""The central AI academic assistant with Neuro, Math and Med modes."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.openai_service import DISCLAIMER, MODES, AIUnavailable
from ui import ERROR, LAB_META, BaseView, CallbackButton, clip, make_embed, present

log = logging.getLogger("mnm.ai")

MODE_COLORS = {"neuro": LAB_META["neuro"]["color"], "math": LAB_META["math"]["color"],
               "med": LAB_META["med"]["color"]}


def answer_embed(mode: str, question: str, text: str, cached: bool = False) -> discord.Embed:
    meta = MODES[mode]
    embed = make_embed(f"{meta['emoji']} {meta['label']}", text, color=MODE_COLORS[mode],
                       footer=DISCLAIMER + (" (cached)" if cached else ""))
    embed.add_field(name="Your question", value=clip(question, 300), inline=False)
    return embed


class QuestionModal(discord.ui.Modal):
    def __init__(self, cog: "AIAssistantCog", mode: str, *, follow_up: bool = False, public: bool = False):
        super().__init__(title=f"{MODES[mode]['label']}: {'follow-up' if follow_up else 'ask a question'}"[:45],
                         timeout=600)
        self.cog, self.mode, self.follow_up, self.public = cog, mode, follow_up, public
        limit = min(cog.bot.config.ai_max_input_chars, 4000)  # type: ignore[attr-defined]
        self.question = discord.ui.TextInput(
            label="Your question", style=discord.TextStyle.paragraph, max_length=limit,
            placeholder="e.g. How do synapses strengthen during learning?")
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=not self.public, thinking=True)
        await self.cog.run_query(interaction, self.mode, self.question.value, follow_up=self.follow_up,
                                 public=self.public)


class AnswerView(BaseView):
    def __init__(self, cog: "AIAssistantCog", owner_id: int, mode: str, public: bool):
        super().__init__(owner_id, timeout=900)
        self.cog, self.mode, self.public = cog, mode, public

        async def follow_up(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(
                QuestionModal(cog, mode, follow_up=True, public=public))

        async def clear(interaction: discord.Interaction) -> None:
            cog.bot.ai.context.clear(interaction.user.id, mode)  # type: ignore[attr-defined]
            await interaction.response.send_message("Conversation context cleared.", ephemeral=True)

        self.add_item(CallbackButton("Follow-up", follow_up, style=discord.ButtonStyle.primary, emoji="💬"))
        self.add_item(CallbackButton("New topic", clear, emoji="🧹"))


class ModeSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Choose an academic mode…", row=0, options=[
            discord.SelectOption(label=m["label"], value=k, emoji=m["emoji"], default=(k == current),
                                 description=f"Ask about {m['subject']}")
            for k, m in MODES.items()])

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view: AskPanelView = self.view  # type: ignore[assignment]
        view.mode = self.values[0]
        for opt in self.options:
            opt.default = opt.value == view.mode
        await present(interaction, embed=view.cog.panel_embed(view.mode), view=view, edit=True)


class AskPanelView(BaseView):
    def __init__(self, cog: "AIAssistantCog", owner_id: int, mode: str):
        super().__init__(owner_id, timeout=600)
        self.cog, self.mode = cog, mode
        self.add_item(ModeSelect(mode))

        async def ask(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(QuestionModal(cog, self.mode))

        self.add_item(CallbackButton("Ask a question", ask, style=discord.ButtonStyle.primary, emoji="✍️", row=1))


class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def panel_embed(self, mode: str) -> discord.Embed:
        meta = MODES[mode]
        ai = self.bot.ai  # type: ignore[attr-defined]
        status = "" if ai.enabled else "\n\n⚠️ The AI assistant isn't enabled on this server yet."
        return make_embed(
            f"{meta['emoji']} MNM Academic Assistant · {meta['label']}",
            f"Ask about **{meta['subject']}**. Pick a mode, then press **Ask a question**. "
            "You can follow up on an answer and I'll remember the last few turns.\n\n"
            "• 🧠 **Neuro**: brain, neurons, cognition\n• 📐 **Math**: step-by-step reasoning\n"
            "• 🧬 **Med**: anatomy, physiology, medical science (education only, never diagnosis)"
            + status, color=MODE_COLORS[mode], footer=DISCLAIMER)

    async def run_query(self, interaction: discord.Interaction, mode: str, question: str, *,
                        follow_up: bool = False, public: bool = False) -> None:
        """Call the AI service and send the result as a followup (interaction must be deferred)."""
        try:
            reply = await self.bot.ai.ask(interaction.user.id, mode, question, follow_up=follow_up)  # type: ignore[attr-defined]
        except AIUnavailable as exc:
            await interaction.followup.send(embed=make_embed("AI assistant", exc.user_message, color=ERROR),
                                            ephemeral=True)
            return
        except Exception:  # noqa: BLE001
            log.exception("Unexpected AI failure")
            await interaction.followup.send(embed=make_embed(
                "AI assistant", "Something unexpected happened. Please try again later.", color=ERROR),
                ephemeral=True)
            return
        await interaction.followup.send(
            embed=answer_embed(mode, question, reply.text, reply.cached),
            view=AnswerView(self, interaction.user.id, mode, public), ephemeral=not public)

    @app_commands.command(name="ask", description="Ask the MNM AI assistant about neuroscience, maths or medicine.")
    @app_commands.describe(mode="Academic mode", question="Your question (leave empty to open the panel)",
                           public="Show the answer to everyone in the channel")
    @app_commands.choices(mode=[app_commands.Choice(name=f"{m['emoji']} {m['label']}", value=k)
                                for k, m in MODES.items()])
    @app_commands.checks.cooldown(1, 5.0)
    async def ask(self, interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None,
                  question: Optional[app_commands.Range[str, 3, 1500]] = None, public: bool = False) -> None:
        mode_key = mode.value if mode else "neuro"
        if question:
            await interaction.response.defer(ephemeral=not public, thinking=True)
            await self.run_query(interaction, mode_key, question, public=public)
            return
        await present(interaction, embed=self.panel_embed(mode_key), view=AskPanelView(self, interaction.user.id, mode_key))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIAssistantCog(bot))

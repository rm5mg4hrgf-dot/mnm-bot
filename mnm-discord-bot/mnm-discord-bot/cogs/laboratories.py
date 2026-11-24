"""The three flagship laboratories: Neuron Lab, Theorem Lab and Vitalis Lab.

Navigation is entirely component based: /lab opens a hub, dropdown menus pick a
laboratory and a feature, and every feature has a Back button.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from quiz_ui import QuestionView
from services import science_tools as sci
from services.challenge_service import (
    TOPICS, TRIAL_GRACE_SECONDS, TRIAL_POINTS_PER_PROBLEM, TRIAL_TIME_LIMIT_MINUTES, Question,
    check_answer, generate_problem, today_utc, trial_id_for,
)
from services.diagrams import render_diagram
from services.openai_service import DISCLAIMER, AIUnavailable
from ui import (
    BRAND, ERROR, LAB_META, BaseView, CallbackButton, lab_footer, make_embed, present, send_error,
)

log = logging.getLogger("mnm.labs")

FEATURES: dict[str, list[tuple[str, str, str, str]]] = {
    "neuro": [
        ("atlas", "Brain Atlas", "🗺️", "Explore brain regions and their functions"),
        ("explorer", "Neural Explorer", "🔎", "Neurons, synapses and neural communication"),
        ("memory", "Memory Vault", "🗃️", "Memory, cognition, learning and plasticity"),
        ("challenge", "NeuroChallenge", "🎯", "Quizzes with difficulty levels and scoring"),
        ("simulator", "Neural Simulator", "📈", "Spiking neuron and perceptron demonstrations"),
    ],
    "math": [
        ("forge", "Proof Forge", "🔨", "Submit a proof for informal AI feedback"),
        ("geometry", "Geometry Lab", "📐", "Geometry problems with diagrams"),
        ("reactor", "Problem Reactor", "⚛️", "Freshly generated, Python-checked problems"),
        ("infinity", "Infinity Vault", "♾️", "Primes, infinity, open problems and a number explorer"),
        ("trials", "Olympiad Trials", "🏁", "Timed competition-style trials with rankings"),
    ],
    "med": [
        ("anatomy", "Anatomy Vault", "🫀", "Human organ systems"),
        ("pathology", "Pathology Files", "📁", "Disease mechanisms (educational)"),
        ("molecular", "Molecular Lab", "🧪", "Cells, DNA, proteins and a DNA toolkit"),
        ("chronicles", "Clinical Chronicles", "📜", "Fictional educational scenarios"),
        ("frontiers", "Medical Frontiers", "🚀", "Discoveries: established vs emerging"),
    ],
}
LAB_ORDER = ("neuro", "math", "med")
DIFFICULTIES = [("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard"), ("mixed", "Mixed")]


# =============================================================== small views
class _LabSelect(discord.ui.Select):
    def __init__(self, cog: "LaboratoriesCog"):
        options = [discord.SelectOption(label=LAB_META[k]["name"], value=k, emoji=LAB_META[k]["emoji"],
                                        description=LAB_META[k]["subject"]) for k in LAB_ORDER]
        super().__init__(placeholder="Choose a laboratory…", options=options, row=0)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.cog.show_lab(interaction, self.values[0], edit=True, replaces=self.view)


class LabHubView(BaseView):
    def __init__(self, cog: "LaboratoriesCog", owner_id: int):
        super().__init__(owner_id, timeout=600)
        self.add_item(_LabSelect(cog))


class _FeatureSelect(discord.ui.Select):
    def __init__(self, cog: "LaboratoriesCog", lab: str):
        options = [discord.SelectOption(label=name, value=key, emoji=emoji, description=desc)
                   for key, name, emoji, desc in FEATURES[lab]]
        super().__init__(placeholder=f"Open a {LAB_META[lab]['name']} feature…", options=options, row=0)
        self.cog, self.lab = cog, lab

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.cog.open_feature(interaction, self.lab, self.values[0], self.view)


class LabMenuView(BaseView):
    def __init__(self, cog: "LaboratoriesCog", owner_id: int, lab: str):
        super().__init__(owner_id, timeout=600)
        self.add_item(_FeatureSelect(cog, lab))

        async def back(interaction: discord.Interaction) -> None:
            await cog.show_hub(interaction, edit=True, replaces=self)

        self.add_item(CallbackButton("All laboratories", back, emoji="🏠", row=1))


class _ChoiceSelect(discord.ui.Select):
    def __init__(self, placeholder: str, choices: Sequence[tuple[str, str]]):
        super().__init__(placeholder=placeholder, row=0,
                         options=[discord.SelectOption(label=label, value=value) for value, label in choices])

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view: ChoiceView = self.view  # type: ignore[assignment]
        await view.on_pick(interaction, self.values[0], view)


class ChoiceView(BaseView):
    """A single dropdown plus a Back button."""

    def __init__(self, owner_id: int, placeholder: str, choices: Sequence[tuple[str, str]],
                 on_pick: Callable[[discord.Interaction, str, "ChoiceView"], Awaitable[None]],
                 back: Callable[[discord.Interaction], Awaitable[None]]):
        super().__init__(owner_id, timeout=600)
        self.on_pick = on_pick
        self.add_item(_ChoiceSelect(placeholder, choices))

        async def go_back(interaction: discord.Interaction) -> None:
            self.stop()
            await back(interaction)

        self.add_item(CallbackButton("Back", go_back, emoji="◀️", row=1))


class _ItemSelect(discord.ui.Select):
    def __init__(self, items: list[dict], index: int):
        options = [discord.SelectOption(label=it["name"][:100], value=str(i), default=(i == index))
                   for i, it in enumerate(items)]
        super().__init__(placeholder="Jump to…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view: BrowserView = self.view  # type: ignore[assignment]
        await view.goto(interaction, int(self.values[0]))


class _GateSelect(discord.ui.Select):
    def __init__(self, cog: "LaboratoriesCog"):
        super().__init__(placeholder="Train a perceptron on a logic gate…", row=1,
                         options=[discord.SelectOption(label=g, value=g) for g in sci.GATES])
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.cog._perceptron(interaction, self.values[0])


class BrowserView(BaseView):
    """Browse a list of content items with a dropdown and previous/next buttons."""

    def __init__(self, cog: "LaboratoriesCog", owner_id: int, lab: str, title: str, items: list[dict], *,
                 tool: Optional[tuple[str, Callable[[], discord.ui.Modal]]] = None):
        super().__init__(owner_id, timeout=900)
        self.cog, self.lab, self.title, self.entries, self.index = cog, lab, title, items, 0
        self.jump_select = _ItemSelect(items, 0)
        self.add_item(self.jump_select)
        self.prev_btn = CallbackButton("Prev", self._prev, emoji="◀️", row=1)
        self.next_btn = CallbackButton("Next", self._next, emoji="▶️", row=1)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        if tool is not None:
            label, factory = tool

            async def open_tool(interaction: discord.Interaction) -> None:
                await interaction.response.send_modal(factory())

            self.add_item(CallbackButton(label, open_tool, style=discord.ButtonStyle.primary, emoji="🧰", row=1))

        async def back(interaction: discord.Interaction) -> None:
            await cog.show_lab(interaction, lab, edit=True, replaces=self)

        self.add_item(CallbackButton("Lab menu", back, emoji="🏠", row=2))
        self._sync()

    def _sync(self) -> None:
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.entries) - 1
        for opt in self.jump_select.options:
            opt.default = opt.value == str(self.index)

    def embed(self) -> discord.Embed:
        meta = LAB_META[self.lab]
        item = self.entries[self.index]
        title = f"{item.get('emoji', '')} {item['name']}".strip()
        embed = make_embed(title, item["summary"], color=meta["color"],
                           footer=lab_footer(self.lab, f"{self.index + 1}/{len(self.entries)}"))
        embed.set_author(name=f"{meta['name']} · {self.title}")
        for label, text in item.get("fields", []):
            embed.add_field(name=label, value=text, inline=False)
        return embed

    async def goto(self, interaction: discord.Interaction, index: int) -> None:
        self.index = max(0, min(index, len(self.entries) - 1))
        self._sync()
        await present(interaction, embed=self.embed(), view=self, edit=True)

    async def _prev(self, interaction: discord.Interaction) -> None:
        await self.goto(interaction, self.index - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        await self.goto(interaction, self.index + 1)


# ====================================================================== modals
class _SimpleModal(discord.ui.Modal):
    """Modal with TextInputs whose submission is handled by an async callback."""

    def __init__(self, title: str, inputs: list[discord.ui.TextInput],
                 handler: Callable[[discord.Interaction, list[str]], Awaitable[None]]):
        super().__init__(title=title, timeout=600)
        self.inputs, self.handler = inputs, handler
        for item in inputs:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.handler(interaction, [i.value for i in self.inputs])

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Modal error", exc_info=error)
        await send_error(interaction, "Something went wrong with that form. Please try again.")


def _result_embed(lab: str, title: str, description: str, fields: Sequence[tuple[str, str, bool]] = (),
                  footer_extra: Optional[str] = None) -> discord.Embed:
    return make_embed(title, description, color=LAB_META[lab]["color"], fields=fields,
                      footer=lab_footer(lab, footer_extra))


# ======================================================================= cog
class LaboratoriesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------- shortcuts
    @property
    def content(self):
        return self.bot.content  # type: ignore[attr-defined]

    @property
    def db(self):
        return self.bot.db  # type: ignore[attr-defined]

    # -------------------------------------------------------------- hub/menu
    def hub_embed(self) -> discord.Embed:
        fields = [(f"{LAB_META[k]['emoji']} {LAB_META[k]['name']} · {LAB_META[k]['subject']}",
                   LAB_META[k]["tagline"], False) for k in LAB_ORDER]
        return make_embed("🔬 MNM Laboratories",
                          "Three interactive environments where medicine, the brain and math meet. "
                          "Choose a laboratory below.", color=BRAND, fields=fields)

    async def show_hub(self, interaction: discord.Interaction, *, edit: bool = False,
                       replaces: Optional[discord.ui.View] = None) -> None:
        await present(interaction, embed=self.hub_embed(), view=LabHubView(self, interaction.user.id),
                      edit=edit, replaces=replaces)

    async def show_lab(self, interaction: discord.Interaction, lab: str, *, edit: bool = True,
                       replaces: Optional[discord.ui.View] = None) -> None:
        meta = LAB_META[lab]
        feature_lines = "\n".join(f"{emoji} **{name}**: {desc}" for _, name, emoji, desc in FEATURES[lab])
        embed = make_embed(f"{meta['emoji']} {meta['name']}", f"*{meta['tagline']}*\n\n{feature_lines}",
                           color=meta["color"], footer=lab_footer(lab))
        await present(interaction, embed=embed, view=LabMenuView(self, interaction.user.id, lab), edit=edit,
                      replaces=replaces)

    @app_commands.command(name="lab", description="Open the Neuron, Theorem and Vitalis laboratories.")
    async def lab(self, interaction: discord.Interaction) -> None:
        await self.show_hub(interaction)

    # ------------------------------------------------------------- dispatch
    async def open_feature(self, interaction: discord.Interaction, lab: str, feature: str,
                           from_view: discord.ui.View) -> None:
        c = self.content
        browse = {
            ("neuro", "atlas"): ("Brain Atlas", c.regions, None),
            ("neuro", "explorer"): ("Neural Explorer", c.neural_topics, None),
            ("neuro", "memory"): ("Memory Vault", c.memory_topics, None),
            ("math", "infinity"): ("Infinity Vault", c.infinity_vault,
                                   ("Number Explorer", self._number_modal)),
            ("med", "anatomy"): ("Anatomy Vault", c.systems, None),
            ("med", "pathology"): ("Pathology Files", c.pathology, None),
            ("med", "molecular"): ("Molecular Lab", c.molecular, ("DNA Toolkit", self._dna_modal)),
            ("med", "frontiers"): ("Medical Frontiers", c.frontiers, None),
        }
        if (lab, feature) in browse:
            title, items, tool = browse[(lab, feature)]
            view = BrowserView(self, interaction.user.id, lab, title, items, tool=tool)
            await present(interaction, embed=view.embed(), view=view, edit=True, replaces=from_view)
            return
        special = {
            ("neuro", "challenge"): self._neuro_challenge,
            ("neuro", "simulator"): self._simulator,
            ("math", "forge"): self._proof_forge,
            ("math", "geometry"): self._geometry,
            ("math", "reactor"): self._reactor,
            ("math", "trials"): self._trials,
            ("med", "chronicles"): self._chronicles,
        }
        await special[(lab, feature)](interaction, from_view)

    def _back_to(self, lab: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def back(interaction: discord.Interaction) -> None:
            await self.show_lab(interaction, lab, edit=True)
        return back

    # ---------------------------------------------------------------- quizzes
    async def _quiz_next(self, interaction: discord.Interaction, *, lab: str, title: str,
                         pool: list[Question], prefix: str, source: str,
                         replaces: Optional[discord.ui.View] = None, with_diagram: bool = False) -> None:
        if not pool:
            await send_error(interaction, "No questions are available for that choice yet.")
            return
        done = await self.db.attempted_keys(interaction.user.id, prefix)
        fresh = [q for q in pool if f"{prefix}{q.id}" not in done]
        question = random.choice(fresh or pool)
        scored = bool(fresh)
        png = None
        if with_diagram and question.diagram:
            png = await asyncio.to_thread(render_diagram, question.diagram)

        async def again(i: discord.Interaction) -> None:
            await self._quiz_next(i, lab=lab, title=title, pool=pool, prefix=prefix, source=source,
                                  with_diagram=with_diagram)

        footer = None if scored else "all questions in this set attempted: practice mode"
        view = QuestionView(self.bot, interaction.user.id, question, title=title, color=LAB_META[lab]["color"],
                            key=f"{prefix}{question.id}" if scored else None, source=source,
                            points=question.points if scored else 0, footer=footer,
                            back=self._back_to(lab), back_label="Lab menu", next_cb=again,
                            diagram_png=png)
        await view.show(interaction, edit=True, replaces=replaces)

    async def _difficulty_picker(self, interaction: discord.Interaction, from_view: discord.ui.View, *,
                                 lab: str, title: str, make_pool: Callable[[str], list[Question]],
                                 prefix: str, source: str, with_diagram: bool = False) -> None:
        async def picked(i: discord.Interaction, value: str, view: ChoiceView) -> None:
            pool = make_pool(value)
            await self._quiz_next(i, lab=lab, title=title, pool=pool, prefix=prefix, source=source,
                                  replaces=view, with_diagram=with_diagram)

        view = ChoiceView(interaction.user.id, "Choose a difficulty…", DIFFICULTIES, picked, self._back_to(lab))
        embed = _result_embed(lab, title, "Pick a difficulty. Each question awards points the first time you "
                                          "answer it (5 / 10 / 15 for easy / medium / hard).")
        await present(interaction, embed=embed, view=view, edit=True, replaces=from_view)

    async def _neuro_challenge(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        bank = self.content.bank
        await self._difficulty_picker(
            interaction, from_view, lab="neuro", title="🎯 NeuroChallenge",
            make_pool=lambda d: bank.pool("neuro", d), prefix="quiz:", source="quiz")

    async def _geometry(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        bank = self.content.bank
        await self._difficulty_picker(
            interaction, from_view, lab="math", title="📐 Geometry Lab",
            make_pool=lambda d: [q for q in bank.geometry if d == "mixed" or q.difficulty == d],
            prefix="geo:", source="geometry", with_diagram=True)

    async def _chronicles(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        await self._quiz_next(interaction, lab="med", title="📜 Clinical Chronicles",
                              pool=list(self.content.chronicles), prefix="chron:", source="chronicles",
                              replaces=from_view)

    # -------------------------------------------------------- problem reactor
    async def _reactor(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        async def picked(i: discord.Interaction, topic: str, view: ChoiceView) -> None:
            await self._reactor_next(i, topic, replaces=view)

        choices = [("any", "Any topic")] + [(t, t.title()) for t in TOPICS]
        view = ChoiceView(interaction.user.id, "Choose a topic…", choices, picked, self._back_to("math"))
        embed = _result_embed("math", "⚛️ Problem Reactor",
                              "Generates a new problem each time. Answers are computed by Python, so they are "
                              "reliable, but generated problems are practice only and award no points.")
        await present(interaction, embed=embed, view=view, edit=True, replaces=from_view)

    async def _reactor_next(self, interaction: discord.Interaction, topic: str,
                            replaces: Optional[discord.ui.View] = None) -> None:
        question = generate_problem(topic)

        async def again(i: discord.Interaction) -> None:
            await self._reactor_next(i, topic)

        view = QuestionView(self.bot, interaction.user.id, question, title="⚛️ Problem Reactor",
                            color=LAB_META["math"]["color"], footer="generated and checked by Python",
                            back=self._back_to("math"), back_label="Lab menu", next_cb=again,
                            next_label="Another problem")
        await view.show(interaction, edit=True, replaces=replaces)

    # --------------------------------------------------------------- proof forge
    async def _proof_forge(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        cfg = self.bot.config  # type: ignore[attr-defined]
        problem = discord.ui.TextInput(label="Problem or claim", style=discord.TextStyle.paragraph,
                                       max_length=600, placeholder="State what you are proving")
        proof = discord.ui.TextInput(label="Your proof", style=discord.TextStyle.paragraph,
                                     max_length=min(cfg.ai_proof_max_chars, 4000),
                                     placeholder="Write your reasoning step by step")
        await interaction.response.send_modal(_SimpleModal("Proof Forge", [problem, proof], self._proof_submit))

    async def _proof_submit(self, interaction: discord.Interaction, values: list[str]) -> None:
        ai = self.bot.ai  # type: ignore[attr-defined]
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            reply = await ai.review_proof(interaction.user.id, values[0], values[1])
        except AIUnavailable as exc:
            await interaction.followup.send(embed=make_embed("Proof Forge unavailable", exc.user_message,
                                                             color=ERROR), ephemeral=True)
            return
        embed = _result_embed("math", "🔨 Proof Forge review", reply.text,
                              footer_extra="informal AI review, not formal verification")
        embed.add_field(name="Reminder", value="This is feedback to help you improve. It is **not** formal "
                                               "verification and can miss errors or flag correct steps. " + DISCLAIMER,
                        inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------------------------------------------------------- olympiad trials
    async def _trials(self, interaction: discord.Interaction, from_view: Optional[discord.ui.View]) -> None:
        tid = trial_id_for(today_utc())
        embed = _result_embed(
            "math", "🏁 Olympiad Trials",
            f"**{tid[1:]}** · {len(self.content.bank.trial(tid))} problems · "
            f"**{TRIAL_TIME_LIMIT_MINUTES} minutes** from the moment you start.\n\n"
            f"Each correct answer is worth {TRIAL_POINTS_PER_PROBLEM} points. You get **one** scored attempt per "
            "trial and the trial refreshes every week (UTC).\n"
            "Rankings are by score, then by speed. Answers are checked automatically against a "
            "Python-verified key, which is not an official solution.")
        view = BaseView(interaction.user.id, timeout=600)

        async def start(i: discord.Interaction) -> None:
            await self._trial_start(i, tid)

        async def rankings(i: discord.Interaction) -> None:
            await self._trial_rankings(i, tid)

        async def back(i: discord.Interaction) -> None:
            view.stop()
            await self.show_lab(i, "math", edit=True)

        view.add_item(CallbackButton("Start / resume", start, style=discord.ButtonStyle.primary, emoji="⏱️"))
        view.add_item(CallbackButton("Rankings", rankings, emoji="🏆"))
        view.add_item(CallbackButton("Lab menu", back, emoji="◀️"))
        await present(interaction, embed=embed, view=view, edit=True, replaces=from_view)

    async def _trial_start(self, interaction: discord.Interaction, tid: str) -> None:
        problems = self.content.bank.trial(tid)
        row = await self.db.start_trial(tid, interaction.user.id)
        if row["submitted_at"]:
            await interaction.response.send_message(embed=_result_embed(
                "math", "Already submitted",
                f"You scored **{row['score']}** points in this trial. Come back next week!"), ephemeral=True)
            return
        started = datetime.fromisoformat(row["started_at"])
        deadline = started + timedelta(minutes=TRIAL_TIME_LIMIT_MINUTES)
        if datetime.now(timezone.utc) > deadline + timedelta(seconds=TRIAL_GRACE_SECONDS):
            await self.db.submit_trial(tid, interaction.user.id, 0)
            await interaction.response.send_message(embed=_result_embed(
                "math", "Time's up", "Your time limit ran out before you submitted, so this attempt scored 0."),
                ephemeral=True)
            return
        text = "\n\n".join(f"**Problem {n}.** {p.text}" for n, p in enumerate(problems, 1))
        embed = _result_embed("math", f"🏁 Trial {tid[1:]}", text,
                              footer_extra=f"deadline {deadline.strftime('%H:%M:%S')} UTC")
        embed.add_field(name="Time", value=f"Submit {discord_ts(deadline)}", inline=False)
        view = BaseView(interaction.user.id, timeout=TRIAL_TIME_LIMIT_MINUTES * 60 + 120)

        async def open_modal(i: discord.Interaction) -> None:
            inputs = [discord.ui.TextInput(label=f"Answer to problem {n}", max_length=60, required=True)
                      for n in range(1, len(problems) + 1)]

            async def submit(j: discord.Interaction, values: list[str]) -> None:
                await self._trial_submit(j, tid, problems, values, deadline)

            await i.response.send_modal(_SimpleModal("Submit trial answers", inputs, submit))

        view.add_item(CallbackButton("Submit answers", open_modal, style=discord.ButtonStyle.success, emoji="✍️"))
        await present(interaction, embed=embed, view=view)

    async def _trial_submit(self, interaction: discord.Interaction, tid: str, problems: list[Question],
                            values: list[str], deadline: datetime) -> None:
        late = datetime.now(timezone.utc) > deadline + timedelta(seconds=TRIAL_GRACE_SECONDS)
        results = [check_answer(p, v) for p, v in zip(problems, values)]
        score = 0 if late else sum(results) * TRIAL_POINTS_PER_PROBLEM
        stored = await self.db.submit_trial(tid, interaction.user.id, score)
        if not stored:
            await interaction.response.send_message("You have already submitted this trial.", ephemeral=True)
            return
        await self.db.record_attempt(interaction.user.id, f"trial:{tid}", "trial", score > 0, score)
        lines = [f"{'✅' if ok else '❌'} **Problem {n}** · answer **{p.correct_display()}**\n{p.explanation}"
                 for n, (p, ok) in enumerate(zip(problems, results), 1)]
        header = ("⏰ You submitted after the deadline, so the score is 0.\n\n" if late
                  else f"**Score: {score} points** ({sum(results)}/{len(problems)} correct)\n\n")
        await interaction.response.send_message(embed=_result_embed(
            "math", "🏁 Trial submitted", header + "\n\n".join(lines)), ephemeral=True)

    async def _trial_rankings(self, interaction: discord.Interaction, tid: str) -> None:
        rows = await self.db.trial_rankings(tid, 10)
        if not rows:
            text = "No scored attempts yet. Be the first!"
        else:
            medals = ["🥇", "🥈", "🥉"]
            text = "\n".join(
                f"{medals[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> · **{r['score']}** pts · "
                f"{int(r['seconds'] // 60)}m {int(r['seconds'] % 60)}s" for i, r in enumerate(rows))
        await interaction.response.send_message(embed=_result_embed("math", f"🏆 Trial rankings · {tid[1:]}", text),
                                                ephemeral=True)

    # ------------------------------------------------------------- simulator
    async def _simulator(self, interaction: discord.Interaction, from_view: discord.ui.View) -> None:
        view = BaseView(interaction.user.id, timeout=900)

        async def lif(i: discord.Interaction) -> None:
            current = discord.ui.TextInput(label="Input current (nA, 0-10)", default="2.0", max_length=6)
            duration = discord.ui.TextInput(label="Duration (ms, 20-500)", default="200", max_length=5)
            await i.response.send_modal(_SimpleModal("Spiking neuron", [current, duration], self._lif_submit))

        async def back(i: discord.Interaction) -> None:
            view.stop()
            await self.show_lab(i, "neuro", edit=True)

        gate_select = _GateSelect(self)
        view.add_item(CallbackButton("Spiking neuron (LIF)", lif, style=discord.ButtonStyle.primary, emoji="⚡", row=0))
        view.add_item(CallbackButton("Lab menu", back, emoji="◀️", row=0))
        view.add_item(gate_select)
        embed = _result_embed(
            "neuro", "📈 Neural Simulator",
            "Two simplified demonstrations, computed live in Python:\n\n"
            "⚡ **Leaky integrate-and-fire neuron**: inject a constant current and watch the membrane potential "
            "charge, fire and reset.\n"
            "🔢 **Perceptron**: a single artificial neuron that learns a logic gate.\n\n"
            "*These are teaching models. Real neurons and brains are vastly more complex.*")
        await present(interaction, embed=embed, view=view, edit=True, replaces=from_view)

    async def _lif_submit(self, interaction: discord.Interaction, values: list[str]) -> None:
        try:
            current, duration = float(values[0]), float(values[1])
            result = sci.simulate_lif(current, duration)
        except ValueError as exc:
            msg = str(exc) if "must be" in str(exc) else "Please enter numbers, e.g. 2.0 and 200."
            await interaction.response.send_message(embed=make_embed("Check your inputs", msg, color=ERROR),
                                                    ephemeral=True)
            return
        plot = sci.render_lif(result)
        if result.spike_count == 0:
            verdict = (f"Below threshold: input current {current:g} nA does not reach the rheobase "
                       f"(about {sci.LIF_RHEOBASE_NA:g} nA), so the neuron never fires.")
        else:
            first = result.spike_times[0]
            verdict = (f"**{result.spike_count} spikes** · **{result.rate_hz:.0f} Hz** · first spike at "
                       f"{first:.1f} ms")
        embed = _result_embed("neuro", "⚡ Leaky integrate-and-fire neuron", f"```\n{plot}\n```\n{verdict}", [
            ("Model", f"τ = {sci.LIF_TAU:g} ms, rest {sci.LIF_V_REST:g} mV, threshold {sci.LIF_V_THRESH:g} mV, "
                      f"reset {sci.LIF_V_RESET:g} mV, refractory {sci.LIF_REFRACTORY:g} ms", False),
            ("Idea", "More current means a higher firing rate, not bigger spikes: action potentials are "
                     "all-or-none. A stronger input simply reaches threshold sooner and more often.", False),
            ("Limits", "Real neurons have many ion channels, dendritic computation and noise that this "
                       "single-equation model leaves out.", False)])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _perceptron(self, interaction: discord.Interaction, gate: str) -> None:
        r = sci.train_perceptron(gate)
        table = "\n".join(f"{a} {b} -> {out}  (target {sci.GATES[gate][(a, b)]})"
                          for (a, b), out in r.predictions.items())
        if r.converged:
            verdict = f"Learned **{gate}** in **{r.epochs_run}** epoch(s): every input is classified correctly."
        else:
            verdict = (f"Did **not** converge after {r.epochs_run} epochs. {gate} is not *linearly separable*: "
                       "no single straight line can split its 1s from its 0s, so one neuron is not enough. "
                       "This limitation is why multi-layer networks matter.")
        embed = _result_embed("neuro", f"🔢 Perceptron · {gate}", f"{verdict}\n```\n{table}\n```", [
            ("Weights", f"w1 = {r.weights[0]}, w2 = {r.weights[1]}, bias = {r.bias}", True),
            ("Errors per epoch", ", ".join(map(str, r.errors_per_epoch[:15])), True),
        ])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- tool modals
    def _dna_modal(self) -> discord.ui.Modal:
        seq = discord.ui.TextInput(label="DNA coding strand (A, C, G, T)", style=discord.TextStyle.paragraph,
                                   max_length=sci.MAX_DNA_LENGTH, placeholder="ATGGCCATTGTAATGGGCCGCTGA")
        return _SimpleModal("DNA Toolkit", [seq], self._dna_submit)

    async def _dna_submit(self, interaction: discord.Interaction, values: list[str]) -> None:
        try:
            report = sci.analyse_dna(values[0])
        except ValueError as exc:
            await interaction.response.send_message(embed=make_embed("Check your sequence", str(exc), color=ERROR),
                                                    ephemeral=True)
            return
        protein = report.protein or "(none: starts with a stop codon)"
        note = "Translation stopped at a stop codon." if report.hit_stop else (
            f"No stop codon found; {report.leftover_bases} leftover base(s)." if report.leftover_bases
            else "No stop codon found.")
        embed = _result_embed("med", "🧬 DNA Toolkit", None, [  # type: ignore[arg-type]
            ("Coding strand (5'→3')", f"`{report.sequence}`", False),
            ("Reverse complement (other strand, 5'→3')", f"`{report.complement_template}`", False),
            ("mRNA (T→U)", f"`{report.mrna}`", False),
            ("Protein, reading frame 1", f"`{protein}`\n{note}", False),
            ("GC content", f"{report.gc:.1f}%", True),
            ("Length", f"{len(report.sequence)} bases", True),
        ], footer_extra="teaching tool")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _number_modal(self) -> discord.ui.Modal:
        n = discord.ui.TextInput(label="Whole number (1 to 10^12)", max_length=13, placeholder="2024")
        return _SimpleModal("Number Explorer", [n], self._number_submit)

    async def _number_submit(self, interaction: discord.Interaction, values: list[str]) -> None:
        try:
            n = int(values[0].replace(",", "").replace(" ", ""))
            report = await asyncio.to_thread(sci.explore_number, n)
        except ValueError as exc:
            msg = str(exc) if "Enter a whole" in str(exc) else "Please enter a whole number."
            await interaction.response.send_message(embed=make_embed("Check your input", msg, color=ERROR),
                                                    ephemeral=True)
            return
        collatz = (f"{report.collatz} steps to reach 1" if report.collatz is not None
                   else "more than 100,000 steps (stopped)")
        embed = _result_embed("math", f"♾️ Number Explorer · {report.n:,}", None, [  # type: ignore[arg-type]
            ("Prime?", "Yes" if report.is_prime else "No", True),
            ("Prime factorisation", sci.format_factorisation(report.factors), True),
            ("Number of divisors", f"{report.divisor_count:,}", True),
            ("Sum of divisors", f"{report.divisor_sum:,}", True),
            ("Euler's totient φ(n)", f"{report.totient:,}", True),
            ("Collatz sequence", f"{collatz}\n*The Collatz conjecture is unproven.*", False),
        ])
        await interaction.response.send_message(embed=embed, ephemeral=True)


def discord_ts(dt: datetime) -> str:
    return f"<t:{int(dt.timestamp())}:R>"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LaboratoriesCog(bot))

"""Automated tests for the MNM bot.

Everything that does not need a live Discord connection is tested for real.
Cog/UI modules are import-smoke-tested (with a stub if discord.py is absent);
actual Discord behaviour requires live testing (see TESTING_REPORT.md).
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import io
import random
import re
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from itertools import permutations
from math import comb, gcd
from pathlib import Path

import pytest

from config import ConfigError, has_admin_access, has_staff_access, load_config
from database import Database
from services import challenge_service as cs
from services import science_tools as sci
from services.content_service import load_content
from services.moderation_service import ContentFilter, SpamTracker, can_moderate, normalise_filter_word
from services.openai_service import (AIService, AIUnavailable, ConversationStore, classify_error,
                                     truncate_for_discord)
from services.research_service import ValidationError, validate_opportunity, validate_submission, validate_url

ROOT = Path(__file__).resolve().parent.parent
GOOD_ENV = {"DISCORD_TOKEN": "x" * 30, "GUILD_ID": "123", "ADMIN_ROLE_ID": "456",
            "OPENAI_API_KEY": "sk-test-key-123456", "OPENAI_MODEL": "some-model"}


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ config
def test_config_loads_and_validates():
    cfg = load_config(GOOD_ENV)
    assert cfg.guild_id == 123 and cfg.ai_enabled
    assert cfg.openai_api_key not in repr(cfg.warnings)
    assert cfg.secrets() == [GOOD_ENV["DISCORD_TOKEN"], GOOD_ENV["OPENAI_API_KEY"]]


def test_config_missing_and_placeholders_reported():
    with pytest.raises(ConfigError) as exc:
        load_config({"DISCORD_TOKEN": "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE", "GUILD_ID": "YOUR_DISCORD_SERVER_ID"})
    msg = str(exc.value)
    assert "DISCORD_TOKEN" in msg and "GUILD_ID" in msg and "ADMIN_ROLE_ID" in msg


def test_config_bad_numbers_and_optional_ai():
    with pytest.raises(ConfigError):
        load_config({**GOOD_ENV, "GUILD_ID": "abc"})
    env = {k: v for k, v in GOOD_ENV.items() if not k.startswith("OPENAI")}
    cfg = load_config(env)
    assert not cfg.ai_enabled and any("AI features" in w for w in cfg.warnings)


def test_env_example_has_required_keys_and_no_real_secrets():
    text = (ROOT / ".env.example").read_text()
    for key in ("DISCORD_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL", "GUILD_ID", "WELCOME_CHANNEL_ID",
                "LOG_CHANNEL_ID", "TICKET_CATEGORY_ID", "SHOWCASE_CHANNEL_ID", "ADMIN_ROLE_ID"):
        assert re.search(rf"^{key}=", text, re.M), key
    assert "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE" in text
    with pytest.raises(ConfigError):  # the untouched template must be rejected, not accepted
        load_config({k: v for k, v in
                     (line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))})


def test_gitignore_protects_secrets():
    ignore = (ROOT / ".gitignore").read_text()
    assert ".env" in ignore and "*.db" in ignore and "__pycache__" in ignore


# ------------------------------------------------------------ permissions
def test_permission_rules():
    cfg = load_config({**GOOD_ENV, "MODERATOR_ROLE_ID": "789"})
    assert has_staff_access({456}, False, cfg) and has_staff_access({789}, False, cfg)
    assert has_staff_access(set(), True, cfg)
    assert not has_staff_access({1, 2}, False, cfg)
    assert has_admin_access({456}, False, cfg) and not has_admin_access({789}, False, cfg)


def test_hierarchy_rules():
    kw = dict(actor_is_owner=False, target_is_owner=False, target_is_bot=False, same_user=False)
    assert can_moderate(10, 5, bot_top_position=20, **kw) is None
    assert can_moderate(5, 5, bot_top_position=20, **kw)           # equal rank denied
    assert can_moderate(10, 5, bot_top_position=5, **kw)            # bot below target
    assert can_moderate(10, 5, bot_top_position=20, **{**kw, "same_user": True})
    assert can_moderate(10, 5, bot_top_position=20, **{**kw, "target_is_owner": True})
    assert can_moderate(10, 5, bot_top_position=20, **{**kw, "target_is_bot": True})
    assert can_moderate(1, 5, bot_top_position=20, **{**kw, "actor_is_owner": True}) is None


# ---------------------------------------------------------------- database
def test_database_init_and_persistence(tmp_path):
    path = tmp_path / "sub" / "mnm.db"

    async def go():
        db = Database(path)
        await db.connect()
        assert {"attempts", "tickets", "showcase", "mod_actions"} <= set(await db.table_names())
        await db.record_attempt(1, "daily:2026-01-01", "daily", True, 10)
        tid = await db.create_ticket(555, 1, "general")
        await db.close()
        db2 = Database(path)  # simulate a restart
        await db2.connect()
        assert (await db2.get_attempt(1, "daily:2026-01-01"))["points"] == 10
        assert (await db2.ticket_by_channel(555))["id"] == tid
        await db2.close()

    run(go())


def test_scoring_never_duplicates_even_concurrently():
    async def go():
        db = Database(":memory:")
        await db.connect()
        results = await asyncio.gather(*[db.record_attempt(7, "daily:x", "daily", True, 10) for _ in range(10)])
        assert sum(1 for first, _ in results if first) == 1
        assert sum(p for _, p in results) == 10
        assert (await db.user_stats(7))["total"] == 10
        first, pts = await db.record_attempt(7, "daily:y", "daily", False, 10)  # wrong answer
        assert first and pts == 0
        await db.close()

    run(go())


def test_leaderboard_ranking_weekly_and_prefix():
    async def go():
        db = Database(":memory:")
        await db.connect()
        await db.record_attempt(1, "daily:a", "daily", True, 10)
        await db.record_attempt(2, "daily:a", "daily", True, 15)
        await db.record_attempt(2, "champ:2026-W01:q", "championship", True, 20)
        await db.record_attempt(3, "daily:a", "daily", False, 15)
        rows = await db.leaderboard(10)
        assert [r["user_id"] for r in rows] == [2, 1]          # zero-point user excluded
        assert rows[0]["total"] == 35
        champ = await db.leaderboard(10, prefix="champ:2026-W01:")
        assert [(r["user_id"], r["total"]) for r in champ] == [(2, 20)]
        assert (await db.user_stats(1))["rank"] == 2
        assert (await db.user_stats(3))["rank"] == 0
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assert await db.leaderboard(10, since=future) == []
        assert await db.attempted_keys(2, "champ:") == {"champ:2026-W01:q"}
        await db.close()

    run(go())


def test_trials_tickets_showcase_ai_usage_and_roles():
    async def go():
        db = Database(":memory:")
        await db.connect()
        # trials
        row = await db.start_trial("T1", 1)
        assert row["submitted_at"] is None
        assert await db.submit_trial("T1", 1, 30)
        assert not await db.submit_trial("T1", 1, 99)            # cannot resubmit
        assert [r["score"] for r in await db.trial_rankings("T1")] == [30]
        # tickets
        await db.create_ticket(10, 1, "general")
        assert (await db.open_ticket_for_user(1))["channel_id"] == 10
        assert await db.close_ticket(10, 2, "transcript text")
        assert not await db.close_ticket(10, 2, "again")
        assert await db.open_ticket_for_user(1) is None
        # showcase: only one reviewer can win
        sid = await db.create_showcase(1, "T", "D" * 50, "math", None)
        await db.set_showcase_review_message(sid, 999)
        assert (await db.showcase_by_review_message(999))["id"] == sid
        assert await db.review_showcase(sid, "approved", 5, 1234)
        assert not await db.review_showcase(sid, "rejected", 6)
        # AI usage limits
        assert await db.ai_try_reserve("d", 1, 2, 10) == "ok"
        assert await db.ai_try_reserve("d", 1, 2, 10) == "ok"
        assert await db.ai_try_reserve("d", 1, 2, 10) == "user_limit"
        await db.ai_refund("d", 1)
        assert await db.ai_try_reserve("d", 1, 2, 10) == "ok"
        assert await db.ai_try_reserve("d", 2, 5, 2) == "global_limit"
        # moderation, filter, roles, opportunities
        await db.add_mod_action(1, 2, "warn", "r")
        await db.add_mod_action(1, 2, "warn", "r2")
        assert len(await db.active_warnings(1)) == 2
        assert await db.clear_warnings(1) == 2 and not await db.active_warnings(1)
        assert await db.add_filter_word("badword") and not await db.add_filter_word("badword")
        assert await db.filter_words() == ["badword"]
        await db.set_role("interest", "research", 5)
        await db.set_role("interest", "research", 6)
        assert await db.roles("interest") == {"research": 6}
        oid = await db.add_opportunity("T", "https://x.org", "research", None, 1)
        assert len(await db.opportunities()) == 1 and await db.remove_opportunity(oid)
        await db.close()

    run(go())


# ---------------------------------------------------------------- content
def test_content_loads_and_is_consistent():
    c = load_content()
    assert all(len(c.bank.by_discipline[d]) >= 10 for d in cs.DISCIPLINES)
    for pool in (*c.bank.by_discipline.values(), c.bank.geometry, c.bank.olympiad, c.chronicles):
        for q in pool:
            if q.is_mcq:
                assert 0 <= q.answer_index < len(q.options)
                assert len(set(q.options)) == len(q.options)
            else:
                assert q.answers
            assert len(q.text) < 1500 and len(q.explanation) < 1000


def test_math_answer_keys_verified_independently():
    c = load_content()
    key = {q.id: q.answers[0] for q in c.bank.by_discipline["math"] + c.bank.olympiad + c.bank.geometry}
    divs = lambda n: [d for d in range(1, n + 1) if n % d == 0]  # noqa: E731
    expected = {
        "m001": sum(range(1, 11)), "m002": 2 ** 10, "m003": len(divs(36)), "m004": 53, "m005": 36,
        "m006": pow(7, 100, 5), "m007": len(set(permutations("LEVEL"))), "m009": 84,
        "m010": 180, "m011": sum(1 for i in range(1, 101) if i % 3 == 0 or i % 5 == 0),
        "m012": pow(3, 100, 100), "m014": sum(1 for x in range(1, 10) for y in range(1, 10)
                                              for z in range(1, 10) if x + y + z == 10),
        "m015": sum(25 // 5 ** k for k in (1, 2)),
        "o001": sum(1 for n in range(1, 1001) if n % 2 and n % 5), "o002": len(divs(2024)),
        "o003": pow(2, 2024, 7), "o005": sum(divs(360)), "o006": len(divs(720)), "o007": pow(7, 2024, 10),
        "o008": 3 ** 3 + 7 ** 3,
        "o009": len({n for n in range(1, 201) if any(a * a == n for a in range(15))
                     or any(a ** 3 == n for a in range(6))}),
        "o004": sum(1 for p in permutations(range(5)) if abs(p.index(0) - p.index(1)) != 1),
        "g001": 10, "g002": 30, "g003": 2, "g004": 8, "g005": 12, "g006": 60, "g007": 115,
    }
    for qid, val in expected.items():
        assert int(key[qid]) == val, qid
    assert Fraction(key["m013"]) == sum(Fraction(1, n * (n + 1)) for n in range(1, 100))
    assert int(key["m008"]) == 12 // 2


def test_browser_content_fits_discord_limits():
    c = load_content()
    for items in (c.regions, c.neural_topics, c.memory_topics, c.systems, c.pathology, c.molecular,
                  c.frontiers, c.infinity_vault):
        assert 1 <= len(items) <= 25
        for it in items:
            total = len(it["name"]) + len(it["summary"]) + sum(len(a) + len(b) for a, b in it["fields"])
            assert total < 5500
            assert len(it["fields"]) <= 25


# --------------------------------------------------------------- challenges
def test_answer_checking():
    short = cs.Question("t", "math", "easy", "q", "e", answers=("99/100",))
    assert cs.check_answer(short, "0.99") and cs.check_answer(short, " 99 / 100 ") and cs.check_answer(short, "198/200")
    assert not cs.check_answer(short, "1") and not cs.check_answer(short, "abc")
    num = cs.Question("t", "math", "easy", "q", "e", answers=("1",))
    assert cs.check_answer(num, "01") and cs.check_answer(num, "1.0") and cs.check_answer(num, "1.")
    assert not cs.check_answer(num, "1e999999999") and not cs.check_answer(num, "9" * 500)
    mcq = cs.Question("t", "neuro", "easy", "q", "e", options=("a", "b"), answer_index=1)
    assert cs.check_answer(mcq, 1) and not cs.check_answer(mcq, 0) and not cs.check_answer(mcq, "1")


def test_daily_championship_trial_are_deterministic_and_fair():
    bank = load_content().bank
    d = date(2026, 11, 24)
    assert bank.daily(d).id == bank.daily(d).id
    discs = {bank.daily(d + timedelta(days=i)).discipline for i in range(3)}
    assert discs == set(cs.DISCIPLINES)                      # rotates through all disciplines
    wid = cs.week_id(d)
    champ = bank.championship(wid)
    assert [q.id for q in champ] == [q.id for q in bank.championship(wid)]
    assert len(champ) == 6 and len({q.id for q in champ}) == 6
    assert {q.discipline for q in champ} == set(cs.DISCIPLINES)
    trial = bank.trial(cs.trial_id_for(d))
    assert len(trial) == cs.TRIAL_PROBLEMS and len({q.id for q in trial}) == 3
    assert cs.championship_points(champ[0]) == champ[0].points * cs.CHAMPIONSHIP_MULTIPLIER
    assert cs.week_start_iso(d).startswith("2026-11-23")      # Monday


def test_generated_problems_have_correct_answers():
    for topic in cs.TOPICS:
        for seed in range(150):
            q = cs.generate_problem(topic, random.Random(seed))
            ans = int(q.answers[0])
            t = q.text
            if "larger root" in t:
                m = re.search(r"x\^2\s*(?:([+-])\s*(\d+)x)?\s*(?:([+-])\s*(\d+))?\s*=", t)
                b = (-1 if m.group(1) == "-" else 1) * int(m.group(2) or 0)
                c = (-1 if m.group(3) == "-" else 1) * int(m.group(4) or 0)
                roots = [x for x in range(-30, 31) if x * x + b * x + c == 0]
                assert roots and ans == max(roots), t
            elif "remainder" in t:
                a, e, mod = map(int, re.findall(r"\d+", t))
                assert ans == (a ** e) % mod
            elif "divisors does" in t:
                n = int(re.findall(r"\d+", t)[0])
                assert ans == len([d for d in range(1, n + 1) if n % d == 0])
            elif "greatest common divisor" in t:
                a, b = map(int, re.findall(r"\d+", t))
                assert ans == gcd(a, b)
            elif "committee" in t:
                k, n = map(int, re.findall(r"\d+", t))
                assert ans == comb(n, k)
            elif "arrangements" in t:
                word = t.split("word ")[1].rstrip("?")
                assert ans == len(set(permutations(word)))
            elif "hypotenuse" in t:
                a, b = map(int, re.findall(r"\d+", t))
                assert ans ** 2 == a * a + b * b
            elif "area" in t:
                a, b, c = map(int, re.findall(r"\d+", t))
                s = (a + b + c) / 2
                assert ans == round((s * (s - a) * (s - b) * (s - c)) ** 0.5)
            else:
                pytest.fail(f"unrecognised generated problem: {t}")
            assert cs.check_answer(q, str(ans))


# ---------------------------------------------------------------- science
def test_lif_simulation_behaviour():
    below = sci.simulate_lif(1.0)
    assert below.spike_count == 0 and max(below.trace) < sci.LIF_V_THRESH
    above = sci.simulate_lif(2.0)
    assert above.spike_count > 0
    assert sci.simulate_lif(4.0).spike_count > above.spike_count          # more current, higher rate
    assert sci.simulate_lif(sci.LIF_RHEOBASE_NA + 0.05, duration_ms=500).spike_count >= 1
    # analytic check: time to first spike = tau * ln(RI / (RI - (Vth - Vrest)))
    import math
    ri = sci.LIF_R * 2.0
    analytic = sci.LIF_TAU * math.log(ri / (ri - (sci.LIF_V_THRESH - sci.LIF_V_REST)))
    assert abs(above.spike_times[0] - analytic) < 0.5
    gaps = [b - a for a, b in zip(above.spike_times, above.spike_times[1:])]
    assert all(g >= sci.LIF_REFRACTORY for g in gaps)
    assert all(sci.LIF_V_RESET - 1e-9 <= v <= sci.LIF_V_THRESH + 1e-9 for v in above.trace)
    for bad in (-1, 11, float("nan")):
        with pytest.raises(ValueError):
            sci.simulate_lif(bad)
    with pytest.raises(ValueError):
        sci.simulate_lif(2.0, duration_ms=5)
    plot = sci.render_lif(above)
    lines = plot.split("\n")
    assert len(lines) == 4 and "▲" in lines[1] and len(lines[0]) == len(lines[1]) == len(lines[2])
    assert max(len(l) for l in lines) <= 60                    # fits a phone-width code block


def test_perceptron_learns_separable_gates_and_fails_xor():
    for gate in ("AND", "OR", "NAND"):
        r = sci.train_perceptron(gate)
        assert r.converged and r.predictions == sci.GATES[gate]
    x = sci.train_perceptron("XOR")
    assert not x.converged and x.errors_per_epoch[-1] > 0
    with pytest.raises(ValueError):
        sci.train_perceptron("NOPE")


def test_dna_tools_and_codon_table():
    assert len(sci.CODON_TABLE) == 64 and sci.CODON_TABLE["ATG"] == "M"
    assert [c for c, a in sci.CODON_TABLE.items() if a == "*"] == ["TAA", "TAG", "TGA"] or \
        sorted(c for c, a in sci.CODON_TABLE.items() if a == "*") == ["TAA", "TAG", "TGA"]
    assert sci.CODON_TABLE["TGG"] == "W" and sci.CODON_TABLE["GGG"] == "G" and sci.CODON_TABLE["TTT"] == "F"
    assert sci.reverse_complement("ATGC") == "GCAT"
    rep = sci.analyse_dna("atg gcc att gta atg ggc cgc tga")
    assert rep.protein == "MAIVMGR" and rep.hit_stop and rep.mrna.startswith("AUGGCC")
    assert round(sci.gc_content("GGCC"), 1) == 100.0
    for bad in ("", "ACGU", "A" * 301, "ACGX"):
        with pytest.raises(ValueError):
            sci.analyse_dna(bad)


def test_number_explorer():
    r = sci.explore_number(2024)
    assert r.factors == {2: 3, 11: 1, 23: 1} and r.divisor_count == 16 and not r.is_prime
    assert r.divisor_sum == 15 * 12 * 24
    assert sci.explore_number(97).is_prime and not sci.explore_number(1).is_prime
    assert sci.explore_number(27).collatz == 111 and sci.explore_number(360).divisor_sum == 1170
    assert sci.explore_number(36).totient == 12
    assert sci.explore_number(10 ** 12 - 11).n == 10 ** 12 - 11   # large input finishes
    assert sci.primes_up_to(30) == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for bad in (0, -5, 10 ** 12 + 1):
        with pytest.raises(ValueError):
            sci.explore_number(bad)


# -------------------------------------------------------------- moderation
def test_spam_tracker_and_filter():
    t = SpamTracker(max_messages=5, window_seconds=5)
    for i in range(5):
        assert not t.check(1, f"hello {i}", now=100 + i * 0.1).spam
    assert t.check(1, "again", now=100.6).spam                      # 6th message inside the window
    assert not t.check(2, "hi", now=100).spam
    assert t.check(3, "@a", mention_count=9).spam
    d = SpamTracker()
    assert not d.check(4, "same", now=0).spam and not d.check(4, "same", now=10).spam
    assert d.check(4, "same", now=20).spam
    assert not d.check(4, "same", now=200).spam                      # old messages expire
    f = ContentFilter(["badword", "bad phrase"])
    assert f.find("this has a BadWord here") == "badword" and f.find("a bad phrase!") == "bad phrase"
    assert f.find("badwords") is None and f.find("classy") is None
    assert ContentFilter().find("anything") is None
    assert normalise_filter_word("  Word ") == "word"
    with pytest.raises(ValueError):
        normalise_filter_word("x")


def test_spam_tracker_memory_is_bounded():
    t = SpamTracker(max_tracked_users=100)
    for uid in range(500):
        t.check(uid, "m", now=0.0)
    t.check(9999, "m", now=1000.0)
    assert len(t._events) <= 101


# ---------------------------------------------------------------- research
def test_showcase_and_opportunity_validation():
    ok = validate_submission("  My project ", "x" * 60, "https://github.com/a/b")
    assert ok == ("My project", "x" * 60, "https://github.com/a/b")
    assert validate_submission("T", "y" * 60, "")[2] is None
    for bad_link in ("javascript:alert(1)", "ftp://x.org/a", "http://", "https://nodot", "https://a b.com", "x" * 500):
        with pytest.raises(ValidationError):
            validate_url(bad_link)
    with pytest.raises(ValidationError):
        validate_submission("", "x" * 60, "")
    with pytest.raises(ValidationError):
        validate_submission("T", "short", "")
    assert validate_opportunity("Math olympiad", "https://example.org/o", "olympiad", None)[2] == "olympiad"
    with pytest.raises(ValidationError):
        validate_opportunity("Title", "https://example.org", "scam", None)
    with pytest.raises(ValidationError):
        validate_opportunity("Title", "", "research", None)


# ---------------------------------------------------------------------- AI
class _Resp:
    def __init__(self, text):
        self.output_text = text


def make_client(behaviour):
    calls = []

    class _Responses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            result = behaviour(len(calls))
            if isinstance(result, BaseException):
                raise result
            return _Resp(result)

    class _Client:
        responses = _Responses()

    return _Client(), calls


def make_ai(behaviour, **env):
    async def build():
        db = Database(":memory:")
        await db.connect()
        return db

    cfg = load_config({**GOOD_ENV, "AI_USER_COOLDOWN_SECONDS": "0", **env})
    db = run(build())
    client, calls = make_client(behaviour)
    return AIService(cfg, db, client=client), calls, db


def test_ai_success_uses_responses_api_correctly():
    ai, calls, _ = make_ai(lambda n: f"answer {n}")

    async def go():
        r = await ai.ask(1, "neuro", "What is a synapse?")
        assert r.text == "answer 1" and not r.cached
        kw = calls[0]
        assert kw["model"] == "some-model" and kw["store"] is False and kw["max_output_tokens"] > 0
        assert "NEURO MODE" in kw["instructions"] and "tools" not in kw          # no tools => no code execution
        assert kw["input"] == [{"role": "user", "content": "What is a synapse?"}]
        again = await ai.ask(2, "neuro", "what is  a synapse?")                 # cache hit, no new API call
        assert again.cached and len(calls) == 1
        fu = await ai.ask(1, "neuro", "and dendrites?", follow_up=True)
        assert len(calls) == 2 and len(calls[1]["input"]) == 3 and fu.text == "answer 2"
        med = await ai.ask(1, "med", "What does the liver do?")
        assert "MED MODE" in calls[2]["instructions"] and "Never diagnose" in calls[2]["instructions"]
        assert med.text

    run(go())


@pytest.mark.parametrize("name,fragment", [
    ("RateLimitError", "busy or over its quota"), ("APITimeoutError", "too long"),
    ("APIConnectionError", "couldn't reach"), ("AuthenticationError", "not configured correctly"),
    ("NotFoundError", "OPENAI_MODEL"), ("BadRequestError", "rephrasing"), ("SomethingElse", "unexpected")])
def test_ai_error_handling_is_friendly_and_leaks_nothing(name, fragment):
    exc = type(name, (Exception,), {})("secret details sk-test-key-123456")
    ai, calls, db = make_ai(lambda n: exc)

    async def go():
        with pytest.raises(AIUnavailable) as e:
            await ai.ask(1, "math", "Prove something")
        assert fragment in e.value.user_message and "sk-test" not in e.value.user_message
        # the failed call is refunded so users aren't charged for outages
        used, _ = await db.ai_usage_today(ai._today())
        assert used == 0

    run(go())


def test_ai_limits_and_input_validation():
    ai, calls, _ = make_ai(lambda n: "ok", AI_USER_DAILY_LIMIT="2", AI_GLOBAL_DAILY_LIMIT="3")

    async def go():
        await ai.ask(1, "math", "q one")
        await ai.ask(1, "math", "q two")
        with pytest.raises(AIUnavailable, match="today's AI question limit"):
            await ai.ask(1, "math", "q three")
        await ai.ask(2, "math", "q four")
        with pytest.raises(AIUnavailable, match="budget"):
            await ai.ask(3, "math", "q five")
        with pytest.raises(AIUnavailable, match="too long"):
            await ai.ask(9, "math", "x" * 5000)
        with pytest.raises(AIUnavailable):
            await ai.ask(9, "math", "   ")
        with pytest.raises(AIUnavailable):
            await ai.ask(9, "chemistry", "hello")
        assert len(calls) == 3

    run(go())


def test_ai_cooldown_and_disabled_and_empty():
    ai, _, _ = make_ai(lambda n: "ok", AI_USER_COOLDOWN_SECONDS="30")

    async def go():
        await ai.ask(1, "neuro", "first question")
        with pytest.raises(AIUnavailable, match="wait"):
            await ai.ask(1, "neuro", "second question")
    run(go())
    off_cfg = load_config({k: v for k, v in GOOD_ENV.items() if not k.startswith("OPENAI")})
    off = AIService(off_cfg, Database(":memory:"))
    assert not off.enabled
    with pytest.raises(AIUnavailable, match="not enabled"):
        run(off.ask(1, "neuro", "hello there"))
    ai2, _, _ = make_ai(lambda n: "   ")
    with pytest.raises(AIUnavailable, match="empty"):
        run(ai2.ask(1, "neuro", "hello there"))


def test_proof_review_and_helpers():
    ai, calls, _ = make_ai(lambda n: "**Summary** fine")
    r = run(ai.review_proof(1, "Show sqrt 2 is irrational", "Assume sqrt2 = p/q ..."))
    assert "informal AI review" in calls[0]["instructions"] and "not formal verification" in calls[0]["instructions"]
    assert "PROBLEM / CLAIM" in calls[0]["input"][0]["content"] and r.text
    long_text = "para\n\n" * 2000
    cut = truncate_for_discord(long_text)
    assert len(cut) <= 3900 and cut.endswith("*")
    assert truncate_for_discord("short") == "short"
    assert "unexpected" in classify_error(ValueError("x"))


def test_conversation_store_expiry_and_bounds():
    s = ConversationStore(max_turns=4, ttl_seconds=10, max_keys=3)
    s.add(1, "math", "q1", "a1", now=0)
    s.add(1, "math", "q2", "a2", now=1)
    s.add(1, "math", "q3", "a3", now=2)
    assert len(s.history(1, "math", now=3)) == 4               # capped at max_turns
    assert s.history(1, "math", now=100) == []                 # expired
    for uid in range(10):
        s.add(uid, "neuro", "q", "a", now=0)
    assert len(s._data) <= 3
    s.add(5, "neuro", "q", "a", now=0)
    s.clear(5)
    assert s.history(5, "neuro", now=0) == []


# --------------------------------------------------------------- diagrams
def test_all_geometry_diagrams_render():
    pytest.importorskip("PIL")
    from PIL import Image
    from services.diagrams import render_diagram
    c = load_content()
    drawn = 0
    for q in c.bank.geometry:
        png = render_diagram(q.diagram)
        if q.diagram:
            img = Image.open(io.BytesIO(png))
            assert img.size == (520, 380)
            assert len({img.getpixel((x, y)) for x in range(0, 520, 13) for y in range(0, 380, 13)}) > 3
            drawn += 1
        else:
            assert png is None
    assert drawn == 6
    assert render_diagram(None) is None and render_diagram({"type": "weird"}) is None


def test_g007_diagram_is_geometrically_faithful():
    import math
    q = [q for q in load_content().bank.geometry if q.id == "g007"][0]
    p = q.diagram["points"]
    A, B, C, I = p["A"], p["B"], p["C"], p["I"]

    def angle(v, a, b):
        v1, v2 = (a[0] - v[0], a[1] - v[1]), (b[0] - v[0], b[1] - v[1])
        return math.degrees(math.acos((v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))))

    assert abs(angle(A, B, C) - 50) < 0.1
    assert abs(angle(I, B, C) - 115) < 0.5                    # matches the answer key
    assert abs(angle(B, A, I) - angle(B, I, C)) < 0.1         # I lies on the bisector of B


# --------------------------------------------------- static / import checks
COG_FILES = sorted((ROOT / "cogs").glob("[a-z]*.py"))
ALL_PY = [p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_all_python_files_parse():
    assert len(ALL_PY) > 25
    for p in ALL_PY:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_no_hardcoded_secrets_in_repo():
    patterns = [re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), re.compile(r"[MNO][A-Za-z\d_-]{23,}\.[\w-]{6}\.[\w-]{27,}")]
    for p in ROOT.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".md", ".txt", ".json", ".example", ".pdf"} \
                and p.name != "test_basic.py":
            text = p.read_text(errors="ignore")
            for pat in patterns:
                assert not pat.search(text), f"possible secret in {p}"
    assert not (ROOT / ".env").exists()


def test_every_cog_has_setup_and_modules_import():
    for p in COG_FILES:
        tree = ast.parse(p.read_text())
        assert any(isinstance(n, ast.AsyncFunctionDef) and n.name == "setup" for n in tree.body), p.name
    from main import EXTENSIONS
    assert sorted(e.split(".")[1] for e in EXTENSIONS) == sorted(p.stem for p in COG_FILES)
    for mod in ("config", "database", "ui", "permissions", "quiz_ui", "main", *EXTENSIONS):
        importlib.import_module(mod)


def test_slash_commands_registered_and_limited():
    """Parse decorators: public commands match the spec and names/descriptions obey Discord limits."""
    top, descs = [], []
    for p in COG_FILES:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "command":
                        kw = {k.arg: k.value.value for k in dec.keywords if isinstance(k.value, ast.Constant)}
                        owner = dec.func.value
                        is_group_child = isinstance(owner, ast.Name) and owner.id != "app_commands"  # @mod.command / @mnm.command
                        descs.append(kw["description"])
                        assert re.fullmatch(r"[a-z0-9_-]{1,32}", kw["name"]), kw["name"]
                        assert 1 <= len(kw["description"]) <= 100, kw["name"]
                        if not is_group_child:
                            top.append(kw["name"])
    assert sorted(top) == ["ask", "challenge", "help", "lab", "leaderboard", "opportunities", "roles",
                           "showcase", "ticket"]
    assert len(descs) >= 25            # 9 public + /mod + /mnm subcommands


def test_component_custom_ids_unique_and_valid():
    ids = []
    for p in COG_FILES:
        ids += re.findall(r'custom_id\s*=\s*"([^"]+)"', p.read_text())
    assert ids and len(ids) == len(set(ids))
    assert all(len(i) <= 100 and i.startswith("mnm:") for i in ids)


def test_admin_and_mod_commands_are_permission_protected():
    """Every subcommand of /mod and /mnm must carry a staff/admin check."""
    for fname, deco in (("moderation.py", "staff_only"), ("admin.py", "admin_only")):
        tree = ast.parse((ROOT / "cogs" / fname).read_text())
        checked = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                is_cmd = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "command"
                             for d in node.decorator_list)
                if is_cmd:
                    names = [ast.unparse(d) for d in node.decorator_list]
                    assert any(deco in n for n in names), f"{fname}:{node.name} missing {deco}"
                    checked += 1
        assert checked >= 5


def test_no_blocking_calls_in_async_code():
    """time.sleep / requests / blocking sqlite3 use must not appear in cogs or services."""
    for p in [*COG_FILES, *(ROOT / "services").glob("*.py"), ROOT / "main.py"]:
        text = p.read_text()
        assert "time.sleep(" not in text and "import requests" not in text, p.name
    assert "sqlite3" not in (ROOT / "cogs" / "laboratories.py").read_text()


def test_log_redaction_filter():
    import logging
    from main import RedactingFilter
    f = RedactingFilter(["supersecrettoken123"])
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "token is %s", ("supersecrettoken123",), None)
    f.filter(rec)
    assert "supersecret" not in rec.getMessage() and "***" in rec.getMessage()

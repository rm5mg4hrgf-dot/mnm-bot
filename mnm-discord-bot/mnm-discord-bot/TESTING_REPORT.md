# MNM Discord Bot: Testing Report

Environment: Linux sandbox, Python 3.13, **no network** (so `discord.py` and the `openai` SDK could **not** be installed), no Discord account/server, no OpenAI key.

**Bottom line:** all logic that does not need Discord or OpenAI is tested for real and passes. The Discord-facing layer (cogs, buttons, modals) is verified only up to *import/decorator/static* level. **It has not been run against a live Discord server and must be live-tested before launch.**

## 1. Automated results

| Check | Result |
|---|---|
| `python -m pytest tests -q` | **46 passed, 0 failed** |
| `ruff check` (undefined names, unused imports, bugbear) | **0 findings** after fixes |
| `compileall` (syntax, every `.py` file) | pass |
| `scripts/export_code_pdf.py` build + verify | pass (83 pages, 33 source files, no credentials) |
| `scripts/setup_database.py` | pass (creates 10 tables) |

### What the 46 tests cover (all genuinely executed)
- **Configuration**: required variables, placeholders rejected, bad numbers, AI optional, untouched `.env.example` is rejected, `.gitignore` protects secrets.
- **Permissions**: staff/admin rules, role-hierarchy rules (self, owner, bot, equal rank, bot below target).
- **Database**: schema creation, **persistence across a simulated restart**, concurrent duplicate scoring (10 parallel submissions → exactly one scores), leaderboard (ranking, weekly/prefix filters, zero-score exclusion), trials (no resubmission), tickets, showcase (only one reviewer wins), AI usage limits and refunds, warnings, filter words, role map, opportunities.
- **Content**: every question well-formed; browser items fit Discord embed limits; **all math/olympiad/geometry answer keys independently recomputed in Python** and matching.
- **Challenge logic**: answer normalisation (`0.99` = `99/100`, `01` = `1`, exponent/huge inputs rejected), deterministic daily/Championship/trial selection, rotation through all three disciplines, **150 generated problems per topic verified against brute force**.
- **Simulations**: LIF neuron (sub-threshold silence, rate rises with current, first-spike time matches the analytic formula, refractory period respected, voltage bounded, input validation); perceptron (AND/OR/NAND converge, XOR does not); DNA toolkit (codon table, translation, validation); number explorer (factorisation, divisors, totient, Collatz 27 → 111 steps, 10^12-scale input).
- **Diagrams**: all 6 diagram specs render to valid 520×380 PNGs; the g007 figure was checked geometrically (angle A = 50°, angle BIC = 115°, I on the bisector).
- **Moderation logic**: spam flood/duplicate/mass-mention detection, expiry, bounded memory, whole-word content filter.
- **AI service (with a fake client)**: correct Responses-API arguments (`model`, `instructions`, `input`, `max_output_tokens`, `store=False`, no tools), mode-specific instructions, cache hit avoids a call, follow-up context, 7 error classes map to friendly messages with no secret leakage, failed calls refunded, per-user/global caps, cooldown, disabled mode, empty reply, proof-review prompt, truncation, context expiry/bounds.
- **Static/structural**: every file parses; no hard-coded secrets in the repo; every cog has `setup`; all modules import; **public slash commands are exactly the nine specified**; command names/descriptions obey Discord limits; component `custom_id`s unique and ≤ 100 chars; every `/mod` and `/mnm` subcommand carries a permission check; no blocking calls in async code; log redaction works.

## 2. Issues found and fixed during this pass
1. **Geometry g007 diagram was not faithful** (apex angle was ≈ 61°, not 50°; label overlapped a line). Rebuilt from exact trigonometry, interior points now labelled clear of lines, regression test added.
2. **LIF plot hid the charging curve** at higher currents (bin-max saturated). Switched to bin-mean with spike bins at full height, added aligned time axis and voltage legend (fits a phone-width code block).
3. Unused imports and variables removed (ruff); an unstable `hash()`-based id replaced with a deterministic CRC.
4. `showcase.submit` now defers before any network call (avoids Discord's 3-second timeout).
5. Ticket panel select now resets after use so it doesn't display a stale choice.
6. Test-harness bugs (stub decorators, command-selector logic) fixed.
7. `BrowserView` attribute names changed to avoid any future clash with `discord.ui.View` internals.

## 3. NOT verified: requires live testing
Everything below depends on real Discord/OpenAI behaviour that could not be exercised here:

- [ ] Bot starts and logs in; both privileged intents accepted; "Synced 11 slash commands" appears (9 public + `/mod` + `/mnm`).
- [ ] Every button, dropdown and modal in `/lab`, `/challenge`, `/ask`, `/leaderboard`, `/roles`, `/ticket`, `/showcase`, `/help`, `/opportunities` responds correctly; editing messages from modal submissions (answer modals) behaves as expected.
- [ ] Geometry diagrams attach, display and are cleared/replaced correctly when navigating.
- [ ] Persistent views survive a restart (ticket panel, role panel, welcome button, showcase Approve/Reject, ticket Close).
- [ ] Welcome message on a real join; role assignment and hierarchy refusals.
- [ ] Ticket flow: channel creation permissions, one-ticket rule, Close + transcript + deletion.
- [ ] Moderation: warn/timeout/ban/unban, auto-timeout at 3 warnings, anti-spam and filter actions, log channel output.
- [ ] Showcase submit → review → publish → DM.
- [ ] Olympiad Trials timing and rankings with real users.
- [ ] **OpenAI**: real Responses API call with your model (this is the call most likely to need adjusting if the SDK/model differs), including reasoning-model token budgets.
- [ ] `pip install -r requirements.txt` on FadeHost; SQLite file persistence across restarts on FadeHost.

## 4. Remaining limitations / assumptions
- **FadeHost** documentation could not be inspected; deployment steps and persistence behaviour are assumptions and are labelled as such.
- Organisation details come from the project brief. The live website was **not** relied on, and no achievements, partnerships or statistics were invented. Use `/mnm opportunity-add` for real opportunities.
- Educational content (neuroscience, medicine, maths) was written for this project; it is intentionally conservative and hedged, but should be reviewed by a subject expert before being treated as authoritative. Math keys are machine-verified; neuroscience/medicine keys are not.
- Medical content is education only. The bot never diagnoses, and the AI "Med Mode" refuses personal medical interpretation.
- Anti-spam state and AI conversation context are in memory (intentionally; they reset on restart). Scores, tickets, moderation records, AI usage counters and settings persist.
- Command sync happens on every start (fine for one server).
- The AI is an assistant, not an oracle: answers carry a disclaimer, and proof feedback is explicitly informal.

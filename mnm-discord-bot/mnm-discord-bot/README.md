# MediNeuroMatics (MNM) Official Discord Bot

> *Where medicine, the brain and math meet.* · Website: <https://mnmstem.study/>

MNM is a YIMO-affiliated student STEM initiative built around **Neuroscience**, **Medicine** and **Mathematics**. This bot turns a Discord server into an interactive academic headquarters: three laboratories, an AI study assistant, daily challenges, a research showcase, and the essential welcome, ticket, moderation and role systems.

It is driven by **buttons, dropdowns, modals and automatic events**. There are only nine public slash commands.

| Command | What it opens |
|---|---|
| `/help` | Overview of everything the bot does |
| `/lab` | The Neuron, Theorem and Vitalis laboratories |
| `/ask` | AI academic assistant (Neuro / Math / Med modes) |
| `/challenge` | Daily challenge, weekly Championship, your stats |
| `/leaderboard` | All-time, weekly and Championship rankings |
| `/ticket` | Private support ticket |
| `/showcase` | Submit a research project or build |
| `/roles` | Pick academic interest roles |
| `/opportunities` | Official MNM hub plus admin-verified links |

Protected command groups: `/mod` (warn, warnings, clear-warnings, timeout, untimeout, ban, unban) for staff, and `/mnm` (panels, role setup, team roles, opportunities, word filter, status) for administrators.

> Nothing in this bot is invented about MNM. Organisation facts come from the project brief; the opportunities list contains only links an administrator adds.

---

## Features

### The three laboratories (`/lab`)

**🧠 Neuron Lab (Neuroscience)**
- **Brain Atlas**: 9 regions with location, function and clinical notes.
- **Neural Explorer**: neuron, dendrites, myelin, action potential, synapse, neurotransmitters, excitation vs inhibition.
- **Memory Vault**: memory types, consolidation, LTP, plasticity, forgetting, study strategies.
- **NeuroChallenge**: multiple-choice quizzes at three difficulty levels with automatic scoring.
- **Neural Simulator**: a real leaky integrate-and-fire neuron (Euler integration, rheobase 1.5 nA, refractory period) drawn as a live plot, plus a perceptron that learns AND / OR / NAND and *fails* XOR, with the reason explained. Clearly labelled as simplified models.

**📐 Theorem Lab (Mathematics)**
- **Proof Forge**: submit a proof for informal AI feedback. Always labelled "not formal verification".
- **Geometry Lab**: 7 problems with Pillow-rendered diagrams.
- **Problem Reactor**: freshly generated algebra, geometry, number theory and combinatorics problems whose answers are computed by Python (practice only: no points).
- **Infinity Vault**: primes, Cantor, Fibonacci, Basel, plus open problems clearly marked OPEN (Collatz, twin primes, Goldbach), with a **Number Explorer** tool.
- **Olympiad Trials**: weekly 3-problem, 20-minute timed trial, one scored attempt, rankings by score then speed.

**🧬 Vitalis Lab (Medicine)**: educational content only; never diagnoses, prescribes or collects personal medical information.
- **Anatomy Vault**, **Pathology Files**, **Molecular Lab** (with a **DNA Toolkit**: reverse complement, mRNA, translation, GC%), **Clinical Chronicles** (fictional scenarios) and **Medical Frontiers** (each topic marked ESTABLISHED or EMERGING).

### Community
- **Daily challenge**: rotates Neuroscience → Mathematics → Medicine; one attempt, points by difficulty (5 / 10 / 15).
- **Championship**: weekly interdisciplinary set of 6 questions, ×2 points.
- **Leaderboard**: persisted in SQLite. A unique `(user, question)` key makes duplicate scoring impossible.
- **Research Showcase**: modal submission, validation, staff Approve/Reject buttons, publication to a channel, DM to the submitter.
- **Opportunities Hub**: link to the official site and admin-verified competitions, olympiads, research and programmes.

### Infrastructure
- **Welcome**: personalised embed, tracks, rules link, "Choose interests" button.
- **Tickets**: General Support, Applications, Partnerships, Other Enquiries; private channels visible only to the member and staff; Close button with confirmation; transcripts saved to the database and posted to the log channel.
- **Moderation**: warnings (auto-timeout at 3), timeouts, bans, role-hierarchy checks, anti-spam (flood, duplicates, mass mentions), admin-managed word filter, audit log.
- **Roles**: six interest roles (Neuroscience, Medicine, Mathematics, Research, Competitions, Technology). Team roles (Programs & Curriculum, Web & Tech, Media & Content, Design & Brand, Partnerships, Volunteering) are linked and assigned by administrators only. Roles with moderation-level permissions are refused.

### AI assistant and cost control
Uses the official OpenAI Python SDK and the **Responses API** with a model name from `OPENAI_MODEL` (nothing hard-coded). The model gets **no tools**, so it cannot run code or act on the server. Controls: per-user cooldown, per-user and global daily caps (persisted, so restarts don't reset them), input and output limits, a small LRU cache, bounded conversation memory with expiry, refunds on API failure, and friendly error messages that never leak details.

---

## Technology stack
Python 3.10+, `discord.py` 2.x, `openai` SDK, SQLite (standard library, WAL mode, async through worker threads), Pillow (optional, diagrams), `python-dotenv`. Dev only: `pytest`, `reportlab`, `Pygments`, `pypdf`.

## Repository structure
```
mnm-discord-bot/
├── main.py              entry point, logging (secret-redacting), cog loading
├── config.py            environment loading and validation
├── database.py          SQLite layer
├── permissions.py       staff/admin checks
├── ui.py  quiz_ui.py    visual identity, shared views, question UI
├── cogs/                welcome roles tickets moderation laboratories
│                        ai_assistant challenges leaderboard showcase general admin
├── services/            openai_service challenge_service science_tools diagrams
│                        moderation_service research_service content_service
├── data/initial_content/  JSON educational content and question bank
├── scripts/             setup_database.py  export_code_pdf.py
├── tests/               pytest suite
├── documentation/       CODE_DOCUMENTATION.pdf
├── TESTING_REPORT.md
├── .env.example  .gitignore  requirements.txt  requirements-dev.txt
```

---

## Setup

### 1. Create the Discord application
1. Open <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy it (you will paste it into `.env` or the host panel, never into code).
3. Still on **Bot**, enable **Server Members Intent** and **Message Content Intent**.
4. **OAuth2 → URL Generator**: scopes `bot` and `applications.commands`. Permissions: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Channels, Manage Roles, Moderate Members, Ban Members, Manage Messages. Open the generated URL to invite the bot.
5. In Server Settings → Roles, drag the bot's role **above** the roles it should manage.

### 2. Find channel and role IDs
Discord → **User Settings → Advanced → Developer Mode ON** (on iPad: Settings → Advanced). Then long-press / right-click a server, role, channel or category → **Copy ID**.

| Variable | What to copy |
|---|---|
| `GUILD_ID` | the server |
| `ADMIN_ROLE_ID` | your admin role (required) |
| `MODERATOR_ROLE_ID` | optional moderator role |
| `WELCOME_CHANNEL_ID`, `LOG_CHANNEL_ID`, `SHOWCASE_CHANNEL_ID` | text channels (keep the log channel staff-only) |
| `TICKET_CATEGORY_ID` | a **category** for ticket channels |
| `RULES_CHANNEL_ID`, `TRACKS_CHANNEL_ID` | optional, linked in the welcome message |

### 3. OpenAI
Create a key at <https://platform.openai.com/api-keys>. Set `OPENAI_MODEL` to a current model your account can use (see <https://platform.openai.com/docs/models>). Without these two variables the bot still runs; AI features are disabled with a clear message.

### 4. Configure
```bash
cp .env.example .env      # then edit .env and fill in your values
```
`.env` is in `.gitignore`. Placeholders such as `PASTE_YOUR_…` are treated as "not set" and reported at start-up.

### 5. Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/setup_database.py   # optional: checks the database path is writable
python main.py
```
Look for `Synced N slash commands to guild …`. Then in Discord run **`/mnm setup-roles`**, **`/mnm panel`** (Tickets, then Interest roles) in the channels you choose, and **`/mnm status`** to confirm configuration.

### Working from an iPad
You can do everything from a browser: edit files in GitHub's web editor (or github.dev), add the secrets in FadeHost's panel, and use Discord on the iPad to run the setup commands. You never need a local terminal.

---

## GitHub workflow
```bash
git init && git add . && git commit -m "MNM bot v1.0"
git branch -M main && git remote add origin https://github.com/<you>/mnm-discord-bot.git
git push -u origin main
```
Before every push, confirm `git status` does not list `.env` or any `.db` file.

## Deploying on FadeHost
> FadeHost's documentation could not be inspected while this project was built, so these steps describe typical Python bot hosting. **Check option names in your FadeHost panel.**

1. Upload or clone the repository onto your FadeHost server (Python 3.10+).
2. Install command: `pip install -r requirements.txt`. **Startup command: `python main.py`.**
3. In the panel's environment-variable / startup-variable section, add every variable from `.env.example` (at least `DISCORD_TOKEN`, `GUILD_ID`, `ADMIN_ROLE_ID`).
4. **Persistent storage:** scores, tickets and moderation records live in SQLite at `data/runtime/mnm.db`. Confirm that this folder survives restarts on your plan. If it does not, either set `DATABASE_PATH` to a persistent volume or move to a hosted database such as PostgreSQL. Only `database.py` touches storage, so the change is contained.
5. Start the server and watch the console, then run `/mnm status`.

Resource profile: one process, one SQLite connection, no polling loops, no background tasks beyond discord.py's gateway. Pillow is optional.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Configuration problems: …` on start | A required variable is missing or still a placeholder |
| `Enable 'Server Members Intent'…` | Turn on both privileged intents in the Developer Portal |
| Slash commands don't appear | Check the bot was invited with `applications.commands`, `GUILD_ID` is correct, and look for the "Synced" log line |
| Roles/tickets fail with permission errors | Move the bot's role higher; grant Manage Roles / Manage Channels |
| "AI assistant isn't enabled" | Set `OPENAI_API_KEY` and `OPENAI_MODEL` |
| "configured AI model is unavailable" | `OPENAI_MODEL` isn't available to your account; pick another |
| AI answers cut off | Raise `AI_MAX_OUTPUT_TOKENS` (reasoning models also use this budget) |
| Showcase says "isn't configured" | Set `LOG_CHANNEL_ID` and `SHOWCASE_CHANNEL_ID` |
| Scores vanish after restart | Your host's disk isn't persistent: see deployment step 4 |

## Security guidance
- Secrets only in environment variables. `.env`, databases and logs are git-ignored; log output redacts known secret values.
- If a token or key is ever exposed, **reset it immediately** (Developer Portal / OpenAI dashboard).
- Only the two privileged intents the features need are requested (Members, Message Content).
- Ticket transcripts and moderation records contain member data: restrict the log channel and the database file.
- All admin/moderation commands check permissions at runtime *and* are hidden from non-admins by Discord's default permissions.
- User input is length-limited and validated (links must be http/https, numeric answers are parsed without `eval`). The AI is given no tools.

## Tests and PDF
```bash
pip install -r requirements-dev.txt
python -m pytest tests -q                   # automated tests
python scripts/export_code_pdf.py           # regenerates and verifies documentation/CODE_DOCUMENTATION.pdf
```
See **TESTING_REPORT.md** for exactly what has and has not been verified.

## Licence and attribution
Educational content was written for this project; verify before treating it as authoritative. MediNeuroMatics and its website belong to their respective owners.

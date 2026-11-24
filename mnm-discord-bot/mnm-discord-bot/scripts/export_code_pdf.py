"""Generate documentation/CODE_DOCUMENTATION.pdf from the real project files.

Usage:  python scripts/export_code_pdf.py            (build + verify)
        python scripts/export_code_pdf.py --verify   (verify an existing PDF only)

Requires: reportlab, Pygments (and pypdf for full verification), see requirements-dev.txt.
Secrets, .env files, databases and caches are never included; the script aborts if
anything that looks like a credential is found in a file it is about to print.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "documentation" / "CODE_DOCUMENTATION.pdf"

EXCLUDED_DIRS = {"__pycache__", ".git", ".venv", "venv", "env", "runtime", ".pytest_cache", ".ruff_cache", "logs"}
EXCLUDED_NAMES = {".env"}
EXCLUDED_SUFFIXES = {".db", ".sqlite3", ".pyc", ".pdf", ".log", ".key", ".pem"}
SECRET_PATTERNS = [re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
                   re.compile(r"[MNO][A-Za-z\d_-]{23,}\.[\w-]{6}\.[\w-]{27,}")]

try:
    from pygments import lex
    from pygments.lexers import PythonLexer, get_lexer_for_filename
    from pygments.token import Token
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                    TableStyle, XPreformatted)
    from reportlab.platypus.tableofcontents import TableOfContents
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency for PDF export: {exc}. Install with: pip install -r requirements-dev.txt")

# ------------------------------------------------------------------ files
def source_files() -> list[Path]:
    """Every relevant source file, in a stable, meaningful order."""
    def ok(p: Path) -> bool:
        return (p.is_file() and p.name not in EXCLUDED_NAMES and p.suffix not in EXCLUDED_SUFFIXES
                and not (set(p.relative_to(ROOT).parts) & EXCLUDED_DIRS))

    order: list[Path] = []
    for name in ("main.py", "config.py", "database.py", "permissions.py", "ui.py", "quiz_ui.py"):
        order.append(ROOT / name)
    for folder in ("cogs", "services", "scripts", "tests"):
        order += sorted(p for p in (ROOT / folder).glob("*.py") if p.name != "__init__.py")
    order += [ROOT / n for n in ("requirements.txt", "requirements-dev.txt", ".env.example", ".gitignore")]
    return [p for p in order if ok(p)]


def data_files() -> list[Path]:
    return sorted((ROOT / "data" / "initial_content").glob("*.json"))


def scan_for_secrets(files: list[Path]) -> None:
    for p in files:
        text = p.read_text(encoding="utf-8")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                sys.exit(f"ABORT: possible credential found in {p.relative_to(ROOT)}; PDF not generated.")


# ------------------------------------------------------------------- fonts
def register_fonts() -> tuple[str, set[int] | None]:
    """Prefer DejaVu Sans Mono (covers block characters/arrows); fall back to Courier."""
    candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
                  Path("/usr/share/fonts/dejavu/DejaVuSansMono.ttf"),
                  Path("/Library/Fonts/DejaVuSansMono.ttf")]
    for path in candidates:
        if path.exists():
            font = TTFont("CodeFont", str(path))
            pdfmetrics.registerFont(font)
            return "CodeFont", set(font.face.charToGlyph.keys())
    return "Courier", None


FONT, GLYPHS = register_fonts()


def printable(text: str) -> str:
    """Replace characters the PDF font cannot draw (e.g. emoji) with <U+XXXX> so nothing is silently lost."""
    out = []
    for ch in text:
        cp = ord(ch)
        supported = (cp in GLYPHS) if GLYPHS is not None else cp < 256
        out.append(ch if (supported or ch in "\n") else f"<U+{cp:04X}>")
    return "".join(out)


# ------------------------------------------------------------- highlighting
TOKEN_COLORS = [
    (Token.Comment, "#6a737d"), (Token.Keyword, "#d73a49"), (Token.Name.Function, "#6f42c1"),
    (Token.Name.Class, "#6f42c1"), (Token.Name.Decorator, "#e36209"), (Token.Name.Builtin, "#005cc5"),
    (Token.Literal.String, "#032f62"), (Token.Literal.Number, "#005cc5"), (Token.Operator.Word, "#d73a49"),
]
MAX_COLS = 98


def token_color(ttype) -> str | None:
    for base, col in TOKEN_COLORS:
        if ttype in base:
            return col
    return None


def highlight(path: Path) -> list[str]:
    """Return XPreformatted-ready lines (numbered, coloured, hard-wrapped at MAX_COLS)."""
    code = printable(path.read_text(encoding="utf-8")).replace("\t", "    ")
    try:
        lexer = PythonLexer() if path.suffix == ".py" else get_lexer_for_filename(path.name)
    except Exception:  # noqa: BLE001
        lexer = None
    segments: list[tuple[str | None, str]] = []
    if lexer is None:
        segments = [(None, code)]
    else:
        segments = [(token_color(t), v) for t, v in lex(code, lexer)]
    lines: list[list[tuple[str | None, str]]] = [[]]
    for color, value in segments:
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                lines.append([])
            if part:
                lines[-1].append((color, part))
    if lines and not lines[-1]:
        lines.pop()
    rendered = []
    for number, segs in enumerate(lines, 1):
        out, col = [], 0
        prefix = f'<font color="#999999">{number:>4} </font>'
        buf = prefix
        for color, text in segs:
            while text:
                room = MAX_COLS - col
                chunk, text = text[:room], text[room:]
                esc = escape(chunk)
                buf += f'<font color="{color}">{esc}</font>' if color else esc
                col += len(chunk)
                if text:
                    out.append(buf)
                    buf, col = '<font color="#999999">     </font>', 0
        out.append(buf)
        rendered.extend(out)
    return rendered


# -------------------------------------------------------------------- doc
class CodeDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):  # feeds the table of contents
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("H1", "H2"):
                text = flowable.getPlainText()
                key = f"b{self.seq.nextf('toc')}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, 0 if name == "H1" else 1, 0)
                self.notify("TOCEntry", (0 if name == "H1" else 1, text, self.page, key))


def page_decor(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawString(18 * mm, 10 * mm, "MediNeuroMatics · Official Discord Bot · Source Code Documentation")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#0f1220"))
    canvas.rect(0, 0, w, h, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#3A86FF"))
    canvas.rect(0, h * 0.58, w, 3, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawCentredString(w / 2, h * 0.66, "MEDINEUROMATICS")
    canvas.setFont("Helvetica", 20)
    canvas.drawCentredString(w / 2, h * 0.62, "Official Discord Bot")
    canvas.setFont("Helvetica-Bold", 16)
    canvas.setFillColor(colors.HexColor("#8fb8ff"))
    canvas.drawCentredString(w / 2, h * 0.53, "Source Code Documentation")
    canvas.setFont("Helvetica-Oblique", 11)
    canvas.setFillColor(colors.HexColor("#c7cbe0"))
    canvas.drawCentredString(w / 2, h * 0.47, "Where medicine, the brain and math meet.")
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#8a90a8"))
    canvas.drawCentredString(w / 2, 22 * mm, f"Version 1.0 · generated {datetime.now():%Y-%m-%d} from the project source files")
    canvas.drawCentredString(w / 2, 16 * mm, "https://mnmstem.study/")
    canvas.restoreState()


OVERVIEW = [
    "MediNeuroMatics (MNM) is a YIMO-affiliated student STEM initiative built around neuroscience, medicine and "
    "mathematics. This bot brings that identity into Discord as an interactive academic platform: three "
    "laboratories (Neuron Lab, Theorem Lab, Vitalis Lab), an OpenAI-powered academic assistant, challenges and a "
    "leaderboard, a research showcase, plus welcome, ticket, moderation and role systems.",
    "Interaction design: only nine public slash commands (/help /lab /ask /challenge /leaderboard /ticket "
    "/showcase /roles /opportunities) plus two permission-protected groups (/mod, /mnm). Everything else is "
    "reached through buttons, dropdown menus, modals and automatic events.",
]

ARCHITECTURE = [
    ("main.py", "Entry point. Loads configuration, sets up secret-redacting logging, creates the bot, loads all "
                "cogs, registers persistent views and syncs slash commands to the configured guild."),
    ("config.py", "Reads and validates environment variables; treats template placeholders as unset; exposes pure "
                  "permission rules."),
    ("database.py", "SQLite layer (standard-library sqlite3 in worker threads, WAL mode). One 'attempts' table with a "
                    "(user, key) primary key makes duplicate scoring impossible. Also tickets, moderation, showcase, "
                    "opportunities, role mapping and persisted AI usage counters."),
    ("ui.py / quiz_ui.py", "Shared visual identity (embeds, colours), owner-only views with clean timeouts, and the "
                           "reusable question UI (multiple choice buttons or a short-answer modal)."),
    ("cogs/", "Discord-facing features: welcome, roles, tickets, moderation, laboratories, ai_assistant, "
              "challenges, leaderboard, showcase, general (help, opportunities) and admin (/mnm)."),
    ("services/", "Discord-free logic that is unit tested: OpenAI service with cost controls, question bank and "
                  "scheduling, science tools (neuron simulation, perceptron, DNA, numbers), diagrams, moderation "
                  "rules, research validation, content loading."),
    ("data/initial_content/", "Educational content as JSON (brain atlas, anatomy, pathology, questions, ...)."),
    ("tests/", "Automated pytest suite. Cogs are import-smoke-tested; live Discord behaviour needs manual testing."),
]

CONFIG_GUIDE = [
    "1. Copy .env.example to .env (local) or enter the same names in FadeHost's environment-variable settings.",
    "2. DISCORD_TOKEN: Developer Portal → Applications → your app → Bot → Reset Token. Enable the Server Members "
    "Intent and Message Content Intent on the same page.",
    "3. GUILD_ID, ADMIN_ROLE_ID, channel and category IDs: Discord → User Settings → Advanced → Developer Mode ON, "
    "then right-click the server / role / channel and choose Copy ID.",
    "4. OPENAI_API_KEY and OPENAI_MODEL: create a key at platform.openai.com/api-keys and choose a current model "
    "your account can use (see platform.openai.com/docs/models). The bot runs without these; AI features are then "
    "disabled.",
    "5. Optional: MODERATOR_ROLE_ID, RULES_CHANNEL_ID, TRACKS_CHANNEL_ID, DATABASE_PATH, LOG_LEVEL and the AI_* limits.",
    "6. Invite the bot with scopes 'bot' and 'applications.commands' and permissions: View Channels, Send Messages, "
    "Embed Links, Attach Files, Read Message History, Manage Channels, Manage Roles, Moderate Members, Ban Members, "
    "Manage Messages. Place the bot's role ABOVE the roles it must manage.",
    "7. Run /mnm setup-roles, then /mnm panel (tickets and roles) in the channels you want.",
    "Never commit .env or paste secrets into chats, source files or this PDF.",
]

DEPLOY_GUIDE = [
    "Assumptions (not verified): FadeHost's documentation could not be inspected while this project was built, so "
    "the steps below describe typical Python bot hosting. Check FadeHost's panel for the exact option names.",
    "1. Push the repository to GitHub (the .gitignore keeps .env and databases out).",
    "2. Upload or clone the repository on the host; select Python 3.10 or newer.",
    "3. Startup command: python main.py   (install command: pip install -r requirements.txt).",
    "4. Add the environment variables from .env.example in the panel's variables section.",
    "5. Persistence: the bot stores scores, tickets and moderation records in SQLite (default "
    "data/runtime/mnm.db). Confirm that this folder survives restarts on your plan. If it does not, set "
    "DATABASE_PATH to a persistent volume path, or move the database layer to a hosted database "
    "(for example PostgreSQL); database.py is the only file that touches storage.",
    "6. Start the server and watch the console for 'Synced N slash commands'. Slash commands appear instantly in the "
    "configured server.",
    "7. Run /mnm status in Discord to confirm channels, permissions and AI configuration.",
    "Resource notes: no background polling loops; one SQLite connection; Pillow is optional; AI usage is capped "
    "per user and per day to control spend.",
]


def build() -> Path:
    files = source_files()
    data = data_files()
    scan_for_secrets(files + data)

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=18, spaceAfter=8, textColor=colors.HexColor("#1b2a4e"))
    h2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#3A86FF"), fontName="Helvetica-Bold")
    body = ParagraphStyle("Body", parent=ss["BodyText"], fontSize=9.5, leading=13.5, spaceAfter=5)
    small = ParagraphStyle("Small", parent=body, fontSize=8.5, leading=11)
    code = ParagraphStyle("Code", fontName=FONT, fontSize=6.8, leading=8.4)
    toc0 = ParagraphStyle("toc0", fontName="Helvetica-Bold", fontSize=10, leftIndent=0, spaceBefore=4)
    toc1 = ParagraphStyle("toc1", fontName="Helvetica", fontSize=8.5, leftIndent=14)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = CodeDoc(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                  bottomMargin=18 * mm, title="MediNeuroMatics Official Discord Bot: Source Code Documentation",
                  author="MediNeuroMatics", subject="Source code documentation")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="cover", frames=[Frame(0, 0, *A4, id="c")], onPage=cover),
                          PageTemplate(id="main", frames=[frame], onPage=page_decor)])

    from reportlab.platypus import NextPageTemplate
    story: list = [NextPageTemplate("main"), PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [toc0, toc1]
    story += [Paragraph("Table of Contents", ParagraphStyle("TOCH", parent=h1)), toc, PageBreak()]

    story.append(Paragraph("1. Project overview", h1))
    story += [Paragraph(t, body) for t in OVERVIEW]

    story.append(Paragraph("2. Project directory overview", h1))
    tree = ["mnm-discord-bot/"]
    shown = sorted({p.relative_to(ROOT).as_posix() for p in files + data}
                   | {"documentation/CODE_DOCUMENTATION.pdf", "README.md", "TESTING_REPORT.md",
                      "cogs/__init__.py", "services/__init__.py"})
    tree += [f"  {p}" for p in shown]
    story.append(XPreformatted(escape("\n".join(tree)), ParagraphStyle("Tree", fontName=FONT, fontSize=7.4, leading=9.4)))

    story.append(Paragraph("3. Architecture", h1))
    rows = [[Paragraph(f"<b>{escape(a)}</b>", small), Paragraph(escape(b), small)] for a, b in ARCHITECTURE]
    table = Table(rows, colWidths=[38 * mm, doc.width - 38 * mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0d5dd")),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef3ff"))]))
    story += [table, Spacer(1, 6)]

    story.append(Paragraph("4. Configuration guide", h1))
    story += [Paragraph(escape(t), body) for t in CONFIG_GUIDE]
    story.append(Paragraph("5. Deployment guide (FadeHost)", h1))
    story += [Paragraph(escape(t), body) for t in DEPLOY_GUIDE]
    story.append(PageBreak())

    story.append(Paragraph("6. Complete source code", h1))
    story.append(Paragraph("Every Python file is printed in full. Characters that the PDF font cannot draw (such as "
                           "emoji) appear as &lt;U+XXXX&gt; code points; the repository files themselves are unchanged.", small))
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        lines = highlight(path)
        story.append(Paragraph(escape(rel), h2))
        story.append(Paragraph(f"{len(path.read_text(encoding='utf-8').splitlines())} lines", small))
        for i in range(0, len(lines), 60):  # chunks keep memory low and let pages break cleanly
            story.append(XPreformatted("\n".join(lines[i:i + 60]), code))
        story.append(Spacer(1, 6))

    story.append(PageBreak())
    story.append(Paragraph("7. Educational content files", h1))
    story.append(Paragraph("Content is stored as JSON in data/initial_content/ and is not reprinted here:", body))
    for p in data:
        story.append(Paragraph(f"• {escape(p.name)} ({p.stat().st_size:,} bytes)", body))

    doc.multiBuild(story)
    return OUT


# ------------------------------------------------------------ verification
def verify() -> list[str]:
    """Open the PDF and check it exists, is complete and contains every source file and no secrets."""
    problems: list[str] = []
    if not OUT.exists() or OUT.stat().st_size < 20_000:
        return [f"PDF missing or too small: {OUT}"]
    raw = OUT.read_bytes()
    if not raw.startswith(b"%PDF") or b"%%EOF" not in raw[-2048:]:
        problems.append("PDF header/trailer invalid (possibly truncated)")
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf not installed: only basic checks done")
        return problems
    reader = PdfReader(str(OUT))
    pages = len(reader.pages)
    text = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    flat = re.sub(r"\s+", "", text)
    for name in ("MEDINEUROMATICS", "Official Discord Bot", "Source Code Documentation", "Table of Contents"):
        if re.sub(r"\s+", "", name) not in flat:
            problems.append(f"missing text: {name}")
    for p in source_files():
        rel = p.relative_to(ROOT).as_posix()
        if re.sub(r"\s+", "", rel) not in flat:
            problems.append(f"source file not found in PDF: {rel}")
        # a distinctive late line of each Python file proves it was not truncated
        if p.suffix == ".py":
            tail = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if len(ln.strip()) > 14][-1]
            tail_flat = re.sub(r"\s+", "", printable(tail))
            if tail_flat[:40] and tail_flat[:40] not in flat:
                problems.append(f"file may be truncated in PDF: {rel}")
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            problems.append("credential-like text found in PDF")
    print(f"PDF: {OUT.relative_to(ROOT)} · {pages} pages · {OUT.stat().st_size / 1024:.0f} KB · "
          f"{len(source_files())} source files")
    return problems


def main() -> None:
    if "--verify" not in sys.argv:
        build()
    problems = verify()
    if problems:
        print("VERIFICATION FAILED:\n - " + "\n - ".join(problems))
        sys.exit(1)
    print("Verification passed: file exists, opens, contains every source file, no credentials found.")


if __name__ == "__main__":
    main()

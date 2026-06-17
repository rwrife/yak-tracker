# yak-tracker 🐃

> *"I just wanted to fix one bug. Three hours later I'd rewritten the build system."*

## 1. Pitch

`yak-tracker` is a local-first CLI that reconstructs the **story** of your coding day — not as a flat list of commits, but as a **yak-shaving tree** showing how a single intention ("fix login bug") spiraled down six levels of rabbit holes ("...so I upgraded Node, which broke the lockfile, which..."). It reads your shell history and git activity, runs everything through a **local LLM (Ollama)** so your raw terminal history never leaves the machine, and spits out a standup, a learning log, or a "what did past-me even do" explainer.

## 2. Trend inspiration

Pulled from a live scan of Hacker News (front page + Show HN) and Product Hunt on 2026-06-17:

- **"Running local models is good now"** — 1414 pts, #1 on HN front page. The whole premise (chew on private shell history locally) only became reasonable because small local models are finally good enough. <https://news.ycombinator.com/>
- **"But yak shaving is fun (2019)"** — 281 pts, resurfaced on front page. The exact metaphor `yak-tracker` is built around. <https://news.ycombinator.com/>
- **"Fata – Spaced repetition to fight skill rot from AI coding"** (116 pts) and **"Lathe – Use LLMs to learn a new domain, not skip past it"** (401 pts) — Show HN. A clear 2026 anxiety: AI writes the code, devs lose the thread of *what they did and why*. yak-tracker's "learning log" output targets this directly.
- **"Trace – Offline meeting transcripts you can flag mid-call"** (202 pts) and **"Kage – Shadow any website to a single binary for offline viewing"** (695 pts) — Show HN. Local/offline-first is the dominant aesthetic right now.
- **"Paca – Lightweight Jira alternative"** (172 pts) and a **"no overengineering" SQLite task queue** (74 pts) — Show HN. Appetite for small, single-purpose, no-cloud tools.

## 3. Why it's different

- **Not a standup generator.** Tools like `git-standup`, `gitlog`, and a dozen "AI commit summarizers" produce a *flat list* of what landed. yak-tracker models the **detour structure** — the tangents, the dead-ends, the "I went 4 deep and came back" — which is the part humans actually forget.
- **Not just git.** It correlates `git` events with **shell history** (npm installs, failed builds, `cd` into unrelated repos, that one `rm -rf node_modules` of despair) to reconstruct intent, not just outcomes. Commit-only tools miss the 80% of the day that never became a commit.
- **Local by default, on purpose.** Shell history is sensitive (tokens in env exports, paths, side projects). Cloud "AI standup" SaaS products want you to upload it. yak-tracker runs on Ollama; the privacy stance *is* the feature, riding the #1 trend of the day.
- **Against the repo's own house style.** Existing tool-lab repos (commit-roast, merge-oracle, link-coroner, stash-stash) are spooky/forensic *git toys*. yak-tracker is a local-LLM *journaling/reflection* tool. Different category, different muscle.

## 4. MVP scope (v0.1)

The smallest useful thing:

- `yak today` — parse today's shell history (bash/zsh) + `git reflog`/commits across configured repos.
- Group raw events into **sessions** by time-gap heuristic (e.g., >25 min idle = new session).
- Build a simple **tree**: root = inferred intention, children = detected detours (heuristic: directory changes, package installs, error→fix loops).
- Pipe the structured tree to **Ollama** with a prompt that narrates it as a short yak-shaving story.
- Render the tree in the terminal with nice indentation + a one-paragraph summary.
- `--format standup|story|learning` to switch the output persona.
- Config file (`~/.config/yak-tracker/config.toml`) for repo paths, Ollama model, idle threshold.
- Graceful fallback: if Ollama isn't running, still print the raw structured tree (no LLM narration).

## 5. Tech stack

Boring, fast, easy to ship:

- **Python 3.11+** — fastest path to parse messy history files; great stdlib for dates/subprocess; trivial to extend. (Most contributors will read it.)
- **`typer`** for the CLI — clean subcommands, auto `--help`, minimal boilerplate.
- **`rich`** for terminal rendering — trees, panels, color out of the box.
- **`httpx`** to hit the local **Ollama** HTTP API (`/api/generate`). No heavyweight LLM SDK.
- **`tomllib`** (stdlib) for config; **`pytest`** for tests.
- Packaged with **`uv`** / `pyproject.toml`. Single entry point `yak`.

Justification: this is a glue tool over text + a local HTTP endpoint. Python wins on iteration speed and contributor reach; no compiled-language ceremony needed for v0.1.

## 6. Architecture

```
yak-tracker/
  yak_tracker/
    __init__.py
    cli.py            # typer app, subcommands (today, story, config)
    config.py         # load/merge ~/.config/yak-tracker/config.toml
    collectors/
      shell.py        # parse bash/zsh history (with timestamps where available)
      git.py          # walk configured repos: commits, reflog, branch switches
    sessionize.py     # bucket raw events into time-gapped sessions
    tree.py           # build the yak-shaving tree (intention -> detours)
    narrate.py        # build prompt, call Ollama, fall back to raw render
    render.py         # rich rendering of tree + summary
  tests/
  pyproject.toml
  README.md
  PLAN.md
```

Data flow: **collectors** → normalized `Event` list → **sessionize** → **tree** → (**narrate** via Ollama) → **render**.

Key modules: `collectors/*` (pluggable sources), `tree.py` (the differentiating logic), `narrate.py` (the LLM seam — swappable backend later).

## 7. Milestones

1. **M1 — Scaffold + hello-world.** `pyproject.toml`, `yak` entry point, `typer` app, `yak --version` and `yak hello` work. CI runs `pytest` (one trivial test).
2. **M2 — Shell history collector.** Parse bash + zsh history into normalized `Event` objects (cmd, timestamp when available, cwd best-effort). `yak raw` dumps today's events.
3. **M3 — Git collector + sessionizer.** Walk configured repos for commits/reflog; merge with shell events; bucket into time-gapped sessions. `yak sessions` lists them.
4. **M4 — Yak-shaving tree.** Heuristics to infer a session's root intention and nest detours (dir changes, installs, error→fix loops). `yak today` renders the tree with `rich`.
5. **M5 — Ollama narration + formats.** `narrate.py` calls local Ollama; `--format standup|story|learning`; graceful no-Ollama fallback. Config file support.
6. **M6 — Polish + package.** README with demo, sample config, install docs, JSON output (`--json`), and a `--since`/`--date` flag. Tag v0.1.0.

## 8. Backlog / future features (v0.2+)

1. **`yak week`** — roll up a whole week into a tangent heatmap (which days were rabbit-hole days).
2. **Fish + nushell collectors** — broaden shell support.
3. **Editor signal** — optional VS Code/Neovim activity log import for finer intent.
4. **"Yak score"** — a daily metric for how deep your average detour went (gamify focus).
5. **Browser-history correlation** — opt-in: tie Stack Overflow / docs tabs to the detour that caused them.
6. **Pluggable LLM backends** — llama.cpp, LM Studio, or a redacting cloud option.
7. **Markdown export → Obsidian/daily-notes** — drop the learning log straight into a notes vault.
8. **`yak blame <file>` reflection** — "why did I touch this file 9 times today?"
9. **Slack/Discord standup poster** — push the `standup` format to a channel each morning.
10. **Redaction pass** — scrub secrets/tokens from shell history before they ever reach the prompt.
11. **TUI mode** — interactive collapsible tree (textual) instead of static render.
12. **Multi-day "saga" view** — stitch a feature's whole multi-day journey into one narrative.

## 9. Out of scope

- No cloud sync, accounts, or hosted SaaS backend.
- No real-time shell hooking/daemon — v0.1 reads history after the fact, it does not instrument your shell live.
- No team analytics / manager dashboards (this is a tool for *you*, not surveillance).
- No bundled model weights — the user brings their own Ollama install.
- No Windows-native shell (PowerShell/cmd) parsing in v0.1; bash/zsh first.
- No git *content* analysis (diffs/AST) — we model the journey, not the code quality.

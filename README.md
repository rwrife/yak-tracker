# yak-tracker 🐃

**Reconstruct the *story* of your coding day — tangents and all — 100% locally.**

You sat down to fix one bug. Hours later you'd upgraded Node, fought the lockfile, and rage-deleted `node_modules`. `yak-tracker` reads your shell history + git activity, reconstructs the **yak-shaving tree** of how you got from A to "wait, what was I doing?", and narrates it with a **local LLM (Ollama)** — so your raw terminal history never leaves your machine.

Three outputs from the same data:
- **standup** — what you actually shipped, for the morning sync.
- **story** — the funny narrative of the day's rabbit holes.
- **learning** — what you learned along the way (fight the AI-coding skill rot).

## Status

🚧 Early — building toward v0.1. Working today: the CLI scaffold (M1), the
**shell-history collector (M2)** — `yak raw` parses your bash/zsh history into
normalized events — the **git collector + sessionizer (M3)** — `yak
sessions` interleaves git commits/reflog with shell history and buckets it into
time-gapped work sessions — the **yak-shaving tree (M4)** — `yak today`
reconstructs each session as an intention with its rabbit holes nested beneath
it — and **Ollama narration (M5)** — `yak today --format standup|story|learning`
narrates the tree with your local LLM, with a config file and graceful offline
fallback. See [`PLAN.md`](./PLAN.md) for the roadmap and milestones.

```bash
yak --version   # 🐃 it's alive
yak today                 # reconstruct + narrate today's coding day
yak today --format standup  # just the shippable bullets
yak raw         # parse today's shell history into a table
yak sessions    # group today's shell + git activity into work sessions
yak config      # show the resolved configuration
```

## `yak raw` — shell history collector

`yak raw` reads your shell history (bash or zsh), normalizes each command into a
timestamped event, and prints today's activity as a table:

```bash
yak raw                          # today's events, auto-detecting your shell
yak raw --date 2026-06-17        # a specific day (YYYY-MM-DD)
yak raw --shell zsh              # force the history grammar
yak raw --histfile ~/.bash_history   # parse a specific file
yak raw --include-undated        # also show commands with no timestamp
```

Timestamps appear when the history format records them:

- **zsh** with `setopt EXTENDED_HISTORY` (the `: <epoch>:<elapsed>;cmd` format).
- **bash** with `HISTTIMEFORMAT` set (bash writes `#<epoch>` lines).

Plain bash history has no timestamps, so date-filtering can't apply — use
`--include-undated` to dump everything. Multi-line commands are stitched back
together, and the collector degrades gracefully (prints a friendly note) when no
history file is found.

## `yak sessions` — git collector + sessionizer

`yak sessions` merges your shell history with **git activity** (commits +
reflog: branch switches, checkouts, resets) across one or more repos, then
splits the combined timeline into **work sessions** wherever there's a long idle
gap. It's the first glimpse of the day's shape — when you were heads-down vs.
when you stepped away.

```bash
yak sessions                         # today, current repo + shell history
yak sessions --repo ~/code/app -r ~/code/lib   # include several repos
yak sessions --date 2026-06-17       # a specific day (YYYY-MM-DD)
yak sessions --idle-gap 15           # split on 15-min gaps (default 25)
yak sessions --no-git                # shell history only
yak sessions --no-shell              # git activity only
```

Each row shows a session's start/end, duration, event count, and which sources
fed it (e.g. `shell:zsh`, `git:app`). Only **timestamped** events can be placed
on the timeline — undated commands (plain bash history) are shown by `yak raw`
but skipped here. Non-repo paths and missing history degrade gracefully to an
empty result rather than erroring.

A session boundary is any gap **strictly greater** than `--idle-gap` minutes
between consecutive events (default 25). Repos default to the current directory.

## `yak today` — the yak-shaving tree, narrated

`yak today` is the headline view. It collects the day's shell + git activity,
buckets it into sessions (same engine as `yak sessions`), reconstructs each
session as a **tree** — a root *intention* with the rabbit holes you fell into
hanging off (and nested within) it — then hands that tree to your **local Ollama**
model to narrate in one of three personas.

```bash
yak today                         # today, narrated as a story (default)
yak today --format standup        # terse, shippable bullets for the sync
yak today --format story          # the funny rabbit-hole saga
yak today --format learning       # what you learned (fight skill rot)
yak today --no-llm                # skip Ollama; print the raw tree only
yak today --model mistral         # pick the Ollama model (overrides config)
yak today --ollama-host http://box:11434   # point at a remote Ollama
yak today --date 2026-06-17       # a specific day (YYYY-MM-DD)
yak today --repo ~/code/app -r ~/code/lib  # include several repos
yak today --idle-gap 15           # split sessions on 15-min gaps (default 25)
yak today --no-git                # shell history only
```

**Privacy is the point.** Only a compact, summarised *outline* of the tree is
sent, and only to your local Ollama endpoint — never the raw history, never the
cloud. If Ollama isn't running (or you pass `--no-llm`), `yak today` degrades
gracefully: it prints a short notice and falls back to the raw tree below, so you
always get something useful.

```
🐂 Yak-shaving story ─────────────────────────────────────
  You sat down to fix the login bug. Three installs and one
  rage-deleted node_modules later, you'd wandered into
  shared-utils to build some Rust. Classic.
─────────────────────────────────────────────── llama3 ───
```

Under the narration, the tree itself is built with simple, explainable
heuristics:

- **Root intention** — the first commit subject in the session, or the first
  substantive shell command (bare `cd`/`ls` are skipped).
- **Detours** open a new node when the work changes context, glyphed by kind:
  - 📦 **install** — `npm/pip/cargo/brew…` dependency churn
  - 🔥 **error-fix** — forced recovery / cleanup (`rm -rf node_modules`,
    `git reset --hard`, `--force`)
  - 📂 **dir-change** — `cd` elsewhere, or git activity in a different repo
  - 🔀 **branch-switch** — reflog `checkout: moving from … to …`

Consecutive detours of the *same* kind nest **deeper** (the classic
"…which needed…which needed…" spiral), so the shape of a rabbit hole is visible
at a glance. (`--no-llm` shows this tree directly, with a per-session footer of
total events and depth.)

## `yak config` — settings & defaults

yak-tracker reads an optional TOML config so you don't have to retype `--repo`,
`--model`, and friends. `yak config` prints the **resolved** settings and where
they came from (and doubles as a config linter — bad keys/types are reported as
warnings, never crashes).

```bash
yak config          # show effective settings + source
yak config --path   # just print the config file path
```

The file lives at `~/.config/yak-tracker/config.toml` by default (override with
`$YAK_TRACKER_CONFIG`, or it honours `$XDG_CONFIG_HOME`). All keys are optional:

```toml
# ~/.config/yak-tracker/config.toml
repos       = ["~/code/yak-tracker", "~/code/other"]  # default scan targets
idle_gap    = 25                       # minutes; new session after this gap
model       = "llama3"                 # Ollama model for narration
ollama_host = "http://localhost:11434" # where Ollama lives
timeout     = 60                       # seconds before falling back
format      = "story"                  # default `yak today` persona
```

CLI flags always win over the file (e.g. `--idle-gap 15` overrides `idle_gap`).

## Why local-first?

Your shell history has tokens, paths, and side projects in it. Cloud "AI standup" tools want you to upload all of that. yak-tracker runs against your local Ollama instance instead. Privacy is the feature.

## Planned usage

```bash
yak today                      # render + narrate today — available now ✅
yak today --format standup     # just the shippable bullet points — available now ✅
yak today --format story       # the rabbit-hole saga — available now ✅
yak today --format learning    # what you learned today — available now ✅
yak sessions                   # list time-gapped work sessions — available now ✅
yak raw                        # dump normalized events (no LLM) — available now ✅
yak config                     # show resolved configuration — available now ✅
```

## Requirements

- Python 3.11+
- bash or zsh history
- [Ollama](https://ollama.com) running locally (optional — `yak today` falls
  back to the raw tree if absent, and `--no-llm` skips it entirely)

## Install

> Requires Python 3.11+. [`uv`](https://docs.astral.sh/uv/) is recommended but optional.

```bash
# from a clone
git clone https://github.com/rwrife/yak-tracker
cd yak-tracker
uv run yak --version
```

Or install into the current environment with pip:

```bash
pip install -e .
yak --version
```

## Development

```bash
uv sync --extra dev      # create .venv with dev deps
uv run pytest            # run the test suite
uv run ruff check .      # lint
```

Without `uv`:

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

CI runs `ruff` + `pytest` on Python 3.11 and 3.12 for every push and PR.

## License

MIT

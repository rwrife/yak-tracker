# Changelog

All notable changes to **yak-tracker** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Pluggable LLM backends** — narration is no longer hard-wired to Ollama. A
  new `NarrationBackend` protocol backs two implementations: the existing
  `ollama` backend (default) and an `openai_compat` backend that hits any
  OpenAI-compatible `/v1/chat/completions` endpoint (LM Studio, `llama.cpp`
  server, local proxies). Select via config (`backend`, `llm_base_url`) or the
  new `--backend` / `--llm-base-url` flags on `yak today`, `yak tui`, and `yak
  blame`. `openai_compat` reads an optional bearer token from
  `$YAK_LLM_API_KEY` (sent only as an `Authorization` header, never logged or
  stored). The graceful raw-tree fallback is preserved for every backend, and
  the README warns that a non-local endpoint breaks the privacy guarantee. (#33)
- **`yak tui`** — explore the yak-shaving forest in an interactive
  [textual](https://textual.textualize.io/) app instead of the static render.
  Collapse/expand any session or detour (`e`/`c` to do it wholesale), and cycle
  the footer summary between the `standup`/`story`/`learning` personas with `f`.
  Runs the **same pipeline** as `yak today` and honours `--date`, `--repo`,
  `--format`, `--idle-gap`, and `--no-llm`; the footer narrates via Ollama when
  reachable and falls back to a deterministic outline otherwise (offline
  included). Empty days render a friendly leaf. `textual` is an **optional**
  dependency — install it with `pip install 'yak-tracker[tui]'`; running `yak
  tui` without it prints an actionable hint rather than a traceback. (#31)
- **`yak today --export md`** — write a day straight into an Obsidian / daily-
  notes vault as a dated `YYYY-MM-DD.md`. The file carries **YAML front-matter**
  (date, the day's **yak score**, session and max-detour-depth counts, body
  format) so Obsidian/Dataview can index it, and a body in the chosen `--format`
  — the narrated prose when a local Ollama is available, or a deterministic
  outline of the yak-shaving forest offline, so an export always has content.
  Each day maps to one file, rewritten **in place** on re-run (idempotent). Goes
  to `--out` or the configured `vault_path`; the filename comes from
  `filename_template` (`{date}` placeholder, default `{date}.md`, subdirs
  allowed). New config keys `vault_path` / `filename_template`. (#10)
- **`yak config --init`** — write a starter config. Drops a fully-commented
  `config.toml` at the resolved path (creating the directory if needed), with
  every key set to its built-in default so it changes nothing until edited — the
  one-step way for a fresh install to get a config without hunting down the XDG
  location. Refuses to overwrite an existing file unless `--force` is given. The
  same content still ships in `examples/config.toml` for copying by hand. (M6)
- **`yak score`** — daily focus metric. Distils a day to a single 0–100 score
  (higher = more focused) computed from the average and max **detour depth** of
  each session's rabbit holes; a day with no tangents scores `100`. Prints a
  banded one-line summary for a single day, rides along as the footer of
  `yak today`, and `--history` charts the trend as a unicode sparkline with
  average / most-focused / deepest-rabbit-hole callouts. Shares the same
  local-only collection engine, no narration. Flags: `--date`, `--history`,
  `--since`, `--repo`, `--idle-gap`, `--no-git`, `--no-shell`, `--no-redact`.
  The scoring formula is documented in the README. (v0.2 backlog, landed early)

## [0.1.0] — 2026-06-26

First release. yak-tracker reconstructs the **story** of your coding day — the
intention you started with and the rabbit holes you fell into — from your shell
history and git activity, and narrates it with a **local LLM (Ollama)**. Nothing
leaves the machine.

### Added

- **CLI scaffold** (`yak`) built on Typer, with `--version`/`-V`, a `version`
  command, and a `hello` smoke command. (M1)
- **`yak raw`** — shell-history collector. Parses bash/zsh history into
  normalized, timestamped events and prints a day's activity as a table.
  Understands zsh `EXTENDED_HISTORY` and bash `HISTTIMEFORMAT` timestamps,
  stitches multi-line commands, and degrades gracefully when no history is
  found. Flags: `--date`, `--shell`, `--histfile`, `--include-undated`. (M2)
- **`yak sessions`** — git collector + sessionizer. Merges shell history with
  git commits/reflog (branch switches, checkouts, resets) across one or more
  repos and buckets the combined timeline into time-gapped work sessions.
  Flags: `--date`, `--repo`, `--idle-gap`, `--no-git`, `--no-shell`. (M3)
- **`yak today`** — the headline view. Reconstructs each session as a
  **yak-shaving tree** (a root intention with detours nested beneath), glyphed
  by detour kind: 📦 install, 🔥 error-fix, 📂 dir-change, 🔀 branch-switch.
  Same-kind detours nest deeper to show the "…which needed…which needed…"
  spiral. (M4)
- **Ollama narration** — `yak today --format standup|story|learning` narrates
  the tree with your local LLM. Only a compact outline is sent, and only to the
  local endpoint; if Ollama is unreachable (or `--no-llm` is passed), it falls
  back to the raw tree with a notice. Flags: `--model`, `--ollama-host`,
  `--no-llm`. (M5)
- **Configuration** — optional TOML config at
  `~/.config/yak-tracker/config.toml` (honours `$YAK_TRACKER_CONFIG` and
  `$XDG_CONFIG_HOME`) for repos, idle gap, model, host, timeout, default format,
  and redaction. `yak config` prints the resolved settings and doubles as a
  linter; `yak config --path` prints just the path. A starter config ships in
  [`examples/config.toml`](./examples/config.toml). (M5)
- **`yak demo`** — a built-in sample day that runs the *real* pipeline with zero
  setup (no shell history, no Ollama), so you see value the moment you install.
  Supports `--json` and `--since`. (M6)
- **`--json` export** — `yak today --json` (and `yak demo --json`) emit the
  yak-shaving forest as self-describing, machine-readable JSON (schema version,
  date, `generated_at`, rollup summary, per-session trees with stable detour
  `kind`s) for scripting and notes-vault export. (M6)
- **Multi-day `--since N`** — reconstruct the last N days at once (oldest
  first); with `--json` this emits an array of day-documents. (M6)
- **`yak week`** — a weekly tangent-depth heatmap: each day's deepest rabbit
  hole on a colour-ramped bar, with the week's single deepest shave called out.
  Shares the same local-only collection engine, no narration. (v0.2 backlog,
  landed early)
- **Redaction pass** — every collected command is scrubbed for secrets before it
  is shown, narrated, or exported. Catches provider tokens (AWS/GitHub/Slack/
  Google/OpenAI/Stripe, JWTs), `KEY=value` credentials, `Authorization:`
  headers, and `user:password@` URLs, replacing the secret with a
  `«REDACTED:<rule>»` tag while preserving structure. On by default; opt out
  with `--no-redact` or `redact = false`. (v0.2 backlog, landed early)

### Packaging

- Installable via `pipx install git+https://github.com/rwrife/yak-tracker`,
  `uv run yak`, or `pip install -e .`. Single `yak` entry point.
- Builds a wheel and sdist with Hatchling. Requires Python 3.11+.
- CI runs `ruff` + `pytest` on Python 3.11 and 3.12. 192 tests, ruff clean.

[Unreleased]: https://github.com/rwrife/yak-tracker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rwrife/yak-tracker/releases/tag/v0.1.0

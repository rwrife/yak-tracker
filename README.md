# yak-tracker 🐃

**Reconstruct the *story* of your coding day — tangents and all — 100% locally.**

You sat down to fix one bug. Hours later you'd upgraded Node, fought the lockfile, and rage-deleted `node_modules`. `yak-tracker` reads your shell history + git activity, reconstructs the **yak-shaving tree** of how you got from A to "wait, what was I doing?", and narrates it with a **local LLM (Ollama)** — so your raw terminal history never leaves your machine.

Three outputs from the same data:
- **standup** — what you actually shipped, for the morning sync.
- **story** — the funny narrative of the day's rabbit holes.
- **learning** — what you learned along the way (fight the AI-coding skill rot).

## Status

🚧 Early — building toward v0.1. Working today: the CLI scaffold (M1) and the
**shell-history collector (M2)** — `yak raw` parses your bash/zsh history into
normalized events. Sessionizer, yak-shaving tree, and Ollama narration land
next. See [`PLAN.md`](./PLAN.md) for the roadmap and milestones.

```bash
yak --version   # 🐃 it's alive
yak hello       # placeholder until `yak today` lands
yak raw         # parse today's shell history into a table
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

## Why local-first?

Your shell history has tokens, paths, and side projects in it. Cloud "AI standup" tools want you to upload all of that. yak-tracker runs against your local Ollama instance instead. Privacy is the feature.

## Planned usage

```bash
yak today                      # render today's yak-shaving tree + summary
yak today --format standup     # just the shippable bullet points
yak today --format story       # the rabbit-hole saga
yak today --format learning    # what you learned today
yak sessions                   # list time-gapped work sessions
yak raw                        # dump normalized events (no LLM) — available now ✅
```

## Requirements (planned)

- Python 3.11+
- bash or zsh history
- [Ollama](https://ollama.com) running locally (optional — falls back to a raw tree if absent)

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

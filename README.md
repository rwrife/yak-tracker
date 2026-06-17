# yak-tracker 🐃

**Reconstruct the *story* of your coding day — tangents and all — 100% locally.**

You sat down to fix one bug. Hours later you'd upgraded Node, fought the lockfile, and rage-deleted `node_modules`. `yak-tracker` reads your shell history + git activity, reconstructs the **yak-shaving tree** of how you got from A to "wait, what was I doing?", and narrates it with a **local LLM (Ollama)** — so your raw terminal history never leaves your machine.

Three outputs from the same data:
- **standup** — what you actually shipped, for the morning sync.
- **story** — the funny narrative of the day's rabbit holes.
- **learning** — what you learned along the way (fight the AI-coding skill rot).

## Status

🚧 Early — building toward v0.1. See [`PLAN.md`](./PLAN.md) for the roadmap and milestones.

## Why local-first?

Your shell history has tokens, paths, and side projects in it. Cloud "AI standup" tools want you to upload all of that. yak-tracker runs against your local Ollama instance instead. Privacy is the feature.

## Planned usage

```bash
yak today                      # render today's yak-shaving tree + summary
yak today --format standup     # just the shippable bullet points
yak today --format story       # the rabbit-hole saga
yak today --format learning    # what you learned today
yak sessions                   # list time-gapped work sessions
yak raw                        # dump normalized events (no LLM)
```

## Requirements (planned)

- Python 3.11+
- bash or zsh history
- [Ollama](https://ollama.com) running locally (optional — falls back to a raw tree if absent)

## License

MIT

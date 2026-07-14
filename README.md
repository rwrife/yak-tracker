# yak-tracker 🐃

**Reconstruct the *story* of your coding day — tangents and all — 100% locally.**

You sat down to fix one bug. Hours later you'd upgraded Node, fought the lockfile, and rage-deleted `node_modules`. `yak-tracker` reads your shell history + git activity, reconstructs the **yak-shaving tree** of how you got from A to "wait, what was I doing?", and narrates it with a **local LLM (Ollama)** — so your raw terminal history never leaves your machine.

Three outputs from the same data:
- **standup** — what you actually shipped, for the morning sync.
- **story** — the funny narrative of the day's rabbit holes.
- **learning** — what you learned along the way (fight the AI-coding skill rot).

## Status

✅ **v0.1.0 is here.** The full milestone arc has shipped: the CLI scaffold
(M1), the **shell-history collector (M2)** — `yak raw` parses your bash/zsh
history into normalized events — the **git collector + sessionizer (M3)** — `yak
sessions` interleaves git commits/reflog with shell history and buckets it into
time-gapped work sessions — the **yak-shaving tree (M4)** — `yak today`
reconstructs each session as an intention with its rabbit holes nested beneath
it — **Ollama narration (M5)** — `yak today --format standup|story|learning`
narrates the tree with your local LLM, with a config file and graceful offline
fallback — and **packaging polish (M6)**: `yak demo` shows a built-in sample day
with zero setup, `yak today --json` and `--since N` cover scripting and
multi-day rollups, and the tool installs cleanly via pipx/uv/pip. Two v0.2
backlog features landed early too: **`yak week`** rolls a whole week into a
tangent-depth heatmap, a **redaction pass** scrubs secrets before they ever
leave the box, and **`yak score`** distils a day to a single 0–100 focus number.
See [`CHANGELOG.md`](./CHANGELOG.md) for the full release notes
and [`PLAN.md`](./PLAN.md) for the roadmap.

```bash
yak --version   # 🐃 it's alive
yak demo                  # see a sample day instantly — no setup needed
yak today                 # reconstruct + narrate today's coding day
yak today --format standup  # just the shippable bullets
yak today --json            # machine-readable forest (for scripting)
yak tui                   # explore the tree interactively (collapse/expand)
yak week                  # a week of rabbit holes as a depth heatmap
yak score                 # a single 0–100 focus score for today
yak blame path/to/file    # why one file kept pulling you back
yak raw         # parse today's shell history into a table
yak sessions    # group today's shell + git activity into work sessions
yak config      # show the resolved configuration
```

## Try it instantly — `yak demo`

No shell history yet? No Ollama? `yak demo` runs a **built-in sample day**
through the exact same pipeline as `yak today` — so you can see what yak-tracker
produces the moment you install it, with zero setup and nothing sent anywhere:

```bash
pipx install git+https://github.com/rwrife/yak-tracker
yak demo
```

The sample is the canonical spiral: you sat down to fix a login bug, fell into
an `npm` upgrade, which broke the lockfile, which earned a rage-deleted
`node_modules`, after which you wandered into *another* repo to build some Rust —
and only then shipped:

```
🐂 Yak-shaving (demo) — 2026-06-17
#1 🐂 fix: reject empty password on login (09:02)
├── • npm test (09:04)
├── 📦 npm install jsonwebtoken@latest (09:07)
│   └── 📦 npm install (09:09)
│       └── • npm dedupe (09:12)
├── 🔥 rm -rf node_modules (09:18)
│   └── • rm package-lock.json (09:19)
├── 📦 npm install (09:24)
├── 📂 cd ../shared-utils (09:31)
├── 📦 cargo add serde (09:33)
│   ├── • cargo build (09:36)
│   └── • commit 9f8e7d6 chore(utils): add serde derive (09:41)
└── 📂 cd ../webapp (09:48)
    ├── • npm test (09:52)
    └── • commit c4d5e6f test: cover empty-password path (09:55)
  14 event(s), 3 level(s) deep

#2 🐂 release: cut v0.1.0 (14:19)
└── 🔀 switched branch main → release-0.1 (14:10)
    ├── • npm run build (14:12)
    └── • git tag v0.1.0 (14:22)
  3 event(s), 2 level(s) deep
```

Every detour kind shows up — 📦 install, 🔥 error-fix, 📂 dir-change,
🔀 branch-switch — and same-kind detours nest into the classic
"…which needed…which needed…" spiral. `yak demo --json` emits the same
machine-readable forest as `yak today --json`, and `yak demo --since 7` replays
the day across a week. When you're ready, point `yak today` at your *own* day.

## `yak raw` — shell history collector

`yak raw` reads your shell history (bash or zsh), normalizes each command into a
timestamped event, and prints today's activity as a table:

```bash
yak raw                          # today's events, auto-detecting your shell
yak raw --date 2026-06-17        # a specific day (YYYY-MM-DD)
yak raw --shell zsh              # force the history grammar
yak raw --histfile ~/.bash_history   # parse a specific file
yak raw --include-undated        # also show commands with no timestamp
yak raw --no-redact              # show raw commands without scrubbing secrets
```

**Secrets are scrubbed by default.** Before any command is shown (or later fed
to the LLM / `--json` export), yak-tracker runs a redaction pass that replaces
API keys, tokens, `KEY=value` credentials, `Authorization:` headers, and
`user:password@` URLs with a `«REDACTED:…»` tag — keeping the command's *shape*
so the story still reads, but never the secret. Pass `--no-redact` (or set
`redact = false` in config) only when you explicitly want the raw text. See
[Redaction](#redaction--secrets-never-leave-the-box) below.

Timestamps appear when the history format records them:

- **zsh** with `setopt EXTENDED_HISTORY` (the `: <epoch>:<elapsed>;cmd` format).
- **bash** with `HISTTIMEFORMAT` set (bash writes `#<epoch>` lines).
- **fish** — every entry in `fish_history` carries a first-class `when:` epoch,
  so fish always has real per-command timestamps (and `cwd` when fish recorded
  `paths:`).
- **nushell** with the **SQLite** history backend (`history.sqlite3`), which
  stores `command_line`, `cwd` **and** `start_timestamp`. Nushell's plaintext
  `history.txt` backend has no timestamps.

Plain bash history has no timestamps, so date-filtering can't apply — use
`--include-undated` to dump everything. Multi-line commands are stitched back
together, and the collector degrades gracefully (prints a friendly note) when no
history file is found.

### Supported shells

Beyond bash and zsh, `yak today`, `yak sessions`, and `yak blame` now also
auto-detect **fish** and **nushell** history and merge every shell into one
time-ordered stream — no flags needed:

- **fish**: `$XDG_DATA_HOME/fish/fish_history` (default
  `~/.local/share/fish/fish_history`).
- **nushell**: `$XDG_CONFIG_HOME/nushell/` (default `~/.config/nushell/`),
  preferring `history.sqlite3` over `history.txt` when both exist.

Shells you don't use are silently skipped — if fish or nushell isn't installed,
those collectors simply contribute nothing and never error. (`yak raw` remains
focused on a single bash/zsh file; `--histfile` still targets exactly that file.)

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
yak today --model mistral         # pick the model (overrides config)
yak today --ollama-host http://box:11434   # point at a remote Ollama
yak today --backend openai_compat # narrate via an OpenAI-compatible endpoint
yak today --llm-base-url http://localhost:1234/v1  # LM Studio / llama.cpp server
yak today --date 2026-06-17       # a specific day (YYYY-MM-DD)
yak today --since 7               # the last 7 days, oldest first
yak today --json                  # machine-readable forest (for scripting)
yak today --export md --out ~/notes   # write today's note to a vault
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

### Pluggable LLM backends

Ollama is the default, but the LLM seam is swappable. Point `yak` at any
**OpenAI-compatible** `/v1/chat/completions` endpoint — **LM Studio**, a
**`llama.cpp` server**, or a local proxy — with the `openai_compat` backend:

```bash
# LM Studio (default local server port)
yak today --backend openai_compat --llm-base-url http://localhost:1234/v1 --model your-model

# llama.cpp server
yak today --backend openai_compat --llm-base-url http://localhost:8080/v1
```

Or set it once in config (`yak config`):

```toml
backend      = "openai_compat"
llm_base_url = "http://localhost:1234/v1"
model        = "your-model"
```

| Backend | Endpoint | Base URL default | Works with |
| --- | --- | --- | --- |
| `ollama` (default) | `/api/generate` | `http://localhost:11434` (or `ollama_host`) | Ollama |
| `openai_compat` | `/v1/chat/completions` | `http://localhost:1234/v1` | LM Studio, llama.cpp server, local proxies, any OpenAI-compatible API |

**API key (optional):** `openai_compat` reads a bearer token from the
`YAK_LLM_API_KEY` environment variable and sends it only as an `Authorization`
header — it is never written to config or logged. Leave it unset for local
servers that don't need auth.

> ⚠️ **Privacy warning:** pointing `openai_compat` at a **non-local** endpoint
> (a hosted API on the internet) sends your narrated outline off your machine
> and **breaks yak-tracker's local-first privacy guarantee**. Keep the base URL
> on `localhost`/your LAN to stay private. The default Ollama backend never
> leaves the box.

Either way, the graceful-degradation contract holds: if the chosen backend is
unreachable, `yak` prints a short notice and falls back to the raw structured
tree.

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

### Scripting: `--json` and `--since`

For automation, export, or piping into a notes vault, `--json` emits the
yak-shaving forest as machine-readable JSON instead of the rich render (it
implies `--no-llm` — narration is prose, not data):

```bash
yak today --json                       # today's forest as JSON
yak today --json --date 2026-06-17     # a specific day
yak today --json --since 7             # last 7 days → a JSON array (one per day)
yak today --json | jq '.summary'       # pipe straight into jq
```

Each document is self-describing — a `schema` version, the `date` it covers, a
`generated_at` UTC stamp, a rollup `summary` (`sessions`/`events`/`max_depth`),
and a `sessions` array. Every session carries its inferred `intention`, quick
stats, and the full nested `tree`; detour `kind`s are the stable strings
`root` / `step` / `install` / `error-fix` / `dir-change` / `branch-switch`, so
consumers can switch on them. A single day prints one object; `--since N` prints
an array of N day-documents (oldest first).

`--since N` also works for the human render — it reconstructs the last N days in
sequence, so `yak today --since 7` walks your whole week of rabbit holes.

### Export: `--export md` → Obsidian / daily notes

Drop the day straight into a notes vault as a dated markdown file instead of
printing it. `yak today --export md` writes `<date>.md` with **YAML
front-matter** (the date, the day's **yak score**, session/detour counts) and a
body in your chosen `--format` — so Obsidian, Dataview, and friends can index it:

```bash
yak today --export md --out ~/notes              # write today's note to ~/notes
yak today --export md                            # use the configured vault_path
yak today --export md --date 2026-06-17           # a specific day
yak today --export md --since 7 --out ~/notes      # one file per day, last 7
yak today --export md --format learning            # learning-log body
yak today --export md --template 'daily/{date}.md' # custom filename/subdir
```

The body is the narrated prose when a local Ollama is available; offline (or
with `--no-llm`) it falls back to a deterministic outline of the yak-shaving
forest, so an export always has content. Each day maps to **one file**, rewritten
in place on re-run (idempotent — no duplicate notes). The destination is `--out`
or the configured `vault_path`, and the filename comes from `filename_template`
(default `{date}.md`, its only placeholder being `{date}`).

```yaml
---
date: "2026-06-17"
title: "Yak-shaving — 2026-06-17"
yak_score: 69
sessions: 1
max_detour_depth: 2
format: "learning"
narrated: true
tags: [yak-tracker]
---
```

## `yak tui` — explore the tree interactively

The static `yak today` render is perfect for a glance, but a deep day — every
detour expanded — is a wall of text. **`yak tui`** hands the *same* yak-shaving
forest to an interactive [textual](https://textual.textualize.io/) app you can
actually sit in: collapse the sessions you don't care about, expand the one
rabbit hole you do, and cycle the footer summary between personas live.

```bash
yak tui                          # explore today
yak tui --date 2026-06-17        # a specific day (YYYY-MM-DD)
yak tui --format standup         # open the footer on the standup persona
yak tui --repo ~/code/app -r ~/code/lib   # include several repos
yak tui --no-llm                 # skip Ollama; footer shows the raw outline
yak tui --idle-gap 15            # split sessions on 15-min gaps (default 25)
```

**Keys:**

- `↑`/`↓` + `Enter` — move and collapse/expand a single node
- `e` / `c` — expand / collapse everything
- `f` — cycle the footer summary (`standup` → `story` → `learning`)
- `q` — quit

The footer honours `--format` for the persona shown first. When a local Ollama
is reachable it narrates that persona; the others (and everything under
`--no-llm` or when Ollama is down) fall back to a deterministic outline, so the
TUI always has something to show — offline included. Empty days render a
friendly *"no sessions for this day"* leaf rather than a blank screen.

> 📸 *Asciinema/screenshot: launch `yak demo` in one pane, then `yak tui` in
> another to record the collapse/expand flow.* (A recording lives in the
> project's media once captured.)

### Optional extra

`textual` is **not** pulled in by the lean core install. Add it with the `tui`
extra:

```bash
pip install 'yak-tracker[tui]'
# or, from a checkout:
uv sync --extra tui
```

If you run `yak tui` without it, yak tells you exactly that instead of crashing.

Like `yak week` and `yak score`, the TUI shares the **exact same pipeline** as
`yak today` (shell + git → sessions → trees), so what you explore is what you'd
have narrated.

## `yak week` — weekly tangent heatmap

`yak week` zooms out from a single day to a **week at a glance**: it
reconstructs each day in the window, then renders a heatmap of how deep that
day's deepest rabbit hole went — so the heads-down days and the
spiralled-all-afternoon days are instantly distinguishable. The single deepest
yak-shave of the whole week is called out underneath.

```bash
yak week                          # the last 7 days, ending today
yak week --since 14               # a two-week window instead
yak week --date 2026-06-17        # the 7 days ending on a specific date
yak week --repo ~/code/app -r ~/code/lib   # include several repos
yak week --no-git                 # shell history only
yak week --idle-gap 15            # split sessions on 15-min gaps (default 25)
```

Each row is one day, oldest first, with its **tangent depth** (the deepest
single-session chain that day), a colour-ramped heat bar (cold grey → hot red
for the week's worst day), session/detour counts, and the intention behind that
day's deepest shave. Quiet days with no timestamped activity still appear as
empty rows, so the gaps are visible too.

`yak week` is the multi-day companion to `yak today`: it shares the exact same
local-only collection and sessionizing engine, but skips Ollama narration — the
week view is about *shape*, not prose. The window defaults to 7 days; `--since
N` overrides the span (e.g. `--since 30` for a month).

## `yak score` — daily focus metric

`yak score` boils the whole day down to **one number**: a 0–100 *focus score*
that gamifies staying on task. `100` is a laser-focused day with no rabbit
holes; the score falls as your work spirals deeper and more often into tangents.
Think of it as a credit score for *not* yak-shaving — bigger is better.

```bash
yak score                         # today's focus score
yak score --date 2026-06-17       # a specific day (YYYY-MM-DD)
yak score --history               # a sparkline of the last 14 days
yak score --history --since 30    # ...over a month instead
yak score --repo ~/code/app -r ~/code/lib   # include several repos
yak score --no-git                # shell history only
yak score --idle-gap 15           # split sessions on 15-min gaps (default 25)
```

A single day prints the score with a plain-language band and the depth stats
behind it:

```
💡 Yak score: 69/100 some detours — avg detour 1.2, deepest 2 across 1 session(s).
```

`--history` charts your focus trend as a unicode sparkline with average / most-
focused / deepest-rabbit-hole callouts, so you can see at a glance whether the
week held together or unravelled (quiet days with no activity show as gaps):

```
💡 Yak score — 2026-06-04 → 2026-06-17 (14d)
  ...per-day table...
  Focus: ▇▇▅ ▆█▇▁▄▇█▅▆  (low → high)
  Average: 78/100 focused over 11 active day(s).
  ✨ Most focused: 100/100 on Wed 2026-06-11.
  🔥 Deepest rabbit hole: 41/100 on Mon 2026-06-09.
```

The day's score also rides along as the footer of `yak today`, so the headline
view closes with your number. Like `yak week`, scoring uses the exact same
local-only collection engine and never touches Ollama.

### How the score is computed

The score looks at the **detours** in each session — the rabbit holes that branch
off your root intention (installs, forced fixes, wandering into other repos,
branch hops). Plain in-line steps don't count; only a genuine tangent does. For
each session we measure two things about its detours:

- **average detour depth** — the mean depth of each top-level detour, where a
  detour with nothing nested under it is depth `1` and one that spawned a
  sub-detour that spawned another is depth `3`. *"On a typical tangent, how deep
  did I go?"*
- **max detour depth** — the single deepest detour chain in the session. *"How
  bad did the worst rabbit hole get?"*

A session with **no detours** scores a clean `100`. Otherwise the two depths are
turned into a penalty and decayed onto the 0–100 scale:

```
penalty   = 2.0 * avg_depth + 1.0 * max_depth
score     = round( 100 / (1 + penalty / 10) )
```

The typical tangent is weighted twice as heavily as the single worst one, and
the decay (rather than a straight subtraction) keeps the score bounded in
`[0, 100]` for *any* depth while making the **first** level of yak-shaving cost
more than the tenth — diminishing returns on going ever deeper. A **day's** score
is the plain average of its sessions' scores, so one deep session can't tank an
otherwise focused day; a day with no sessions at all has *no* score (it shows as
a quiet day rather than a misleading `100`). As reference points: one shallow
one-level detour lands around **77**, a couple of moderate tangents around
**59**, and a genuinely gnarly afternoon (avg ≈ 3, deepest ≈ 6) around **45**.

## `yak blame` — per-file detour reflection

Where `yak today` answers *what was my day*, **`yak blame <file>`** answers
*what was the deal with **this one file***. Point it at a path and it
reconstructs every detour that touched the file — the git commits that modified
it (following renames) plus the shell commands that referenced it — groups the
matches into sessions, and headlines the churn (*"cli.py — touched in 4 sessions
across 3 detours"*). A local Ollama then narrates one paragraph on **why this
file kept pulling you back**, degrading gracefully to the raw timeline when
Ollama isn't reachable.

```bash
yak blame yak_tracker/cli.py            # the churn story for one file
yak blame src/app.py --since 7          # widen the window to the last 7 days
yak blame src/app.py --repo ~/code/app  # point yak at the repo that owns it
yak blame src/app.py --no-shell         # git commits only (skip shell refs)
yak blame src/app.py --no-llm           # raw timeline, no narration
yak blame src/app.py --json             # machine-readable churn (implies --no-llm)
```

The timeline is a per-session tree: 📦 marks a commit that modified the file,
`>` marks a shell command that referenced it. `--json` emits
`{path, relpath, repo, touch_count, session_count, sessions[]}` for scripting.
A path outside every tracked repo fails cleanly with a hint to pass `--repo`;
like the rest of yak, collection is 100% local and only a summarised outline is
sent to your own Ollama.

## `yak config` — settings & defaults

yak-tracker reads an optional TOML config so you don't have to retype `--repo`,
`--model`, and friends. `yak config` prints the **resolved** settings and where
they came from (and doubles as a config linter — bad keys/types are reported as
warnings, never crashes).

```bash
yak config          # show effective settings + source
yak config --path   # just print the config file path
yak config --init   # write a starter config to that path (--force to overwrite)
```

The fastest way to get a config on a fresh install is `yak config --init`: it
drops a fully-commented starter file at the resolved path (creating the
directory if needed), with every key set to its default so it changes nothing
until you edit it. It won't clobber an existing config unless you pass
`--force`. Prefer to do it by hand? The same content ships in
[`examples/config.toml`](./examples/config.toml).

The file lives at `~/.config/yak-tracker/config.toml` by default (override with
`$YAK_TRACKER_CONFIG`, or it honours `$XDG_CONFIG_HOME`). All keys are optional:

```toml
# ~/.config/yak-tracker/config.toml
repos       = ["~/code/yak-tracker", "~/code/other"]  # default scan targets
idle_gap    = 25                       # minutes; new session after this gap
model       = "llama3"                 # LLM model for narration
backend     = "ollama"                 # "ollama" or "openai_compat"
ollama_host = "http://localhost:11434" # where Ollama lives (ollama backend)
# llm_base_url = "http://localhost:1234/v1"  # override backend base URL
timeout     = 60                       # seconds before falling back
format      = "story"                  # default `yak today` persona
redact      = true                     # scrub secrets before they leave the box
vault_path  = "~/notes"                # default dir for `yak today --export md`
filename_template = "{date}.md"        # export filename; {date} = YYYY-MM-DD
```

CLI flags always win over the file (e.g. `--idle-gap 15` overrides `idle_gap`).

## Why local-first?

Your shell history has tokens, paths, and side projects in it. Cloud "AI standup" tools want you to upload all of that. yak-tracker runs against your local Ollama instance instead. Privacy is the feature.

### Redaction — secrets never leave the box

"Local" still includes the prompt handed to Ollama, the `--json` you might drop
into a notes vault, and the `yak raw` table you might paste into a bug report. So
**every collected command is run through a redaction pass before any of that**,
replacing the secret-looking bits with a `«REDACTED:<rule>»` tag while preserving
the surrounding structure (the variable name, the `Bearer` prefix, the URL host)
so the narrated story still makes sense.

What gets caught, out of the box:

- **Provider tokens** — AWS access keys (`AKIA…`), GitHub (`ghp_…`), Slack
  (`xoxb-…`), Google (`AIza…`), OpenAI (`sk-…`), Stripe (`sk_live_…`), JWTs.
- **Credentials in assignments** — `export FOO_TOKEN=…`, `--password …`,
  `API_KEY=…` and friends (any sensitively-named variable or flag).
- **HTTP auth** — `Authorization: Bearer/Basic/Token …` header values.
- **URL credentials** — the `user:password@host` form.
- **Generic high-entropy blobs** — long random token-ish strings as a last resort.

It's **on by default** and conservative-by-design (it would rather redact a bit
too much than leak). Turn it off per-run with `--no-redact`, or globally with
`redact = false` in your config — you have to opt *out*, never in.

## At a glance

```bash
yak today                      # render + narrate today ✅
yak today --format standup     # just the shippable bullet points ✅
yak today --format story       # the rabbit-hole saga ✅
yak today --format learning    # what you learned today ✅
yak week                       # a week of rabbit holes as a depth heatmap ✅
yak score                      # a single 0–100 daily focus score ✅
yak sessions                   # list time-gapped work sessions ✅
yak raw                        # dump normalized events (no LLM) ✅
yak demo                       # a built-in sample day, zero setup ✅
yak config                     # show resolved configuration ✅
yak config --init              # write a starter config file ✅
```

## Requirements

- Python 3.11+
- bash or zsh history
- [Ollama](https://ollama.com) running locally (optional — `yak today` falls
  back to the raw tree if absent, and `--no-llm` skips it entirely)

## Install

> Requires Python 3.11+. [`uv`](https://docs.astral.sh/uv/) is recommended but optional.

The quickest way to get the `yak` command on your PATH is [pipx](https://pipx.pypa.io):

```bash
pipx install git+https://github.com/rwrife/yak-tracker
yak --version
yak demo        # see a sample day immediately — no history/Ollama needed
```

Or run straight from a clone with `uv` (no install step):

```bash
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

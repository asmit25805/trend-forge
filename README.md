# TrendForge 🔥

> Finds a trending GitHub repo every day, understands what makes it compelling, and ships an original inspired project — fully autonomously.

Built by [@asmit25805](https://github.com/asmit25805) · Powered by [Cerebras](https://cloud.cerebras.ai) · Runs daily via GitHub Actions

---

## What it does

Every day at 04:00 UTC, TrendForge:

1. Scans GitHub for trending repos across 8 categories (AI, CLI, LLM, agent, web, API, developer tools, automation)
2. Picks one it hasn't seen before — never repeats a source
3. Reads the actual source code (not just the README) and analyzes the architecture with Cerebras
4. Generates a completely original project inspired by the concept — different enough to stand alone
5. Pushes it to GitHub with real code, tests, CI, LICENSE, and a full README
6. Logs the run and marks the source repo as seen

No human in the loop. No copy-paste. Every output is an original project.

---

## Architecture

```
GitHub Trending Search (8 categories, round-robin)
             │
             ▼
     Pick unseen repo
     (seen_repos.json)
             │
             ▼
  Cerebras analyzes source code
  (README + file tree + actual files)
             │
             ▼
  Phase 0 — Design doc
  (API contracts, data models, module interfaces)
             │
             ▼
  Phase 1 — File tree plan
  (10–14 files, enforced types/models file)
             │
             ▼
  Phase 2 — Generate each file
  (3000-char sibling context, full path list)
             │
             ▼
  Phase 2b — Stub detection & recovery
  (3-stage: regen × 2 → reviewer fallback → skip)
             │
             ▼
  Phase 3 — Validator
  (fixes imports, URLs, fake emails, broken tests)
             │
             ▼
  Push to GitHub
  (Git Tree API — atomic commits, no race conditions)
  ┌──────────────────────────────┐
  │  STANDALONE mode             │  One new repo per project
  │  MONOREPO mode               │  All projects → trendforge-output/<date>-<name>/
  └──────────────────────────────┘
             │
             ▼
  Update seen_repos.json + run_log.json
  Commit state back to trend-forge repo
```

---

## Pipeline files

```
trend-forge/
├── src/
│   ├── main.py          — Orchestrator: runs the full pipeline end to end
│   ├── trending.py      — GitHub Search API, round-robin category rotation, dedup
│   ├── analyzer.py      — Reads source code, calls Cerebras, returns concept JSON
│   ├── generator.py     — 4-phase generator (design → plan → generate → validate)
│   └── pusher.py        — Git Tree API push, monorepo/standalone mode switch
├── .github/
│   └── workflows/
│       └── daily.yml    — Cron at 04:00 UTC, commits state files back
├── seen_repos.json       — Auto-updated: source repos already processed
├── run_log.json          — Auto-updated: full history of every run
└── requirements.txt
```

---

## Generator deep dive

The most complex part of TrendForge is the 4-phase generator. Each phase feeds the next:

**Phase 0 — Design doc**
Cerebras reads the analysis and produces a full architectural spec: core abstractions with method signatures, data models with field types, module interfaces with import graphs, data flow, error handling strategy, and key design decisions. This is the blueprint every later phase references.

**Phase 1 — File tree plan**
Turns the design doc into a concrete file list. Enforces a shared types/models file for the target language (`src/types.ts`, `src/core/models.py`, `internal/types.go`) so import errors don't cascade. Requires 2 tech-specific and 2 problem-specific topics — no generic tags.

**Phase 2 — File generation**
Writes each file sequentially. Every call gets: the full design doc, the complete list of planned file paths (so imports are never invented), and a 3000-char preview of the last 4 files written (so naming and style stay consistent). Each file gets 6000 max tokens and `temperature=0.25` for determinism.

**Phase 2b — Stub detection and recovery**
After generation, every file is scanned for stub markers (`raise NotImplementedError`, `# TODO`, `placeholder`, `pass\n`, etc.). For any file that fails:
- Attempt 1: re-run `generate_file` (5s sleep)
- Attempt 2: re-run `generate_file` (15s sleep)
- Fallback: reviewer-mode rewrite at `temperature=0.2` with full sibling context and the module spec from the design doc
- Final: skip the file entirely — a missing file causes an explicit ImportError; a broken stub silently poisons the repo

**Phase 3 — Validator**
A final review pass at `temperature=0.15`. Fixes: wrong imports, mismatched function names, meaningless test assertions, broken README URLs, fake emails, wrong install commands, missing API Reference sections. Has an 8000 token budget and saves both updated and newly created files.

---

## Output modes

### Standalone mode (default)
Each run creates a fresh top-level GitHub repo. Good for showcasing individual projects.

```
github.com/asmit25805/skillforge-cli      ← Day 1
github.com/asmit25805/flowforge           ← Day 2
github.com/asmit25805/llm-router          ← Day 3
```

### Monorepo mode (`TRENDFORGE_MONOREPO=1`)
All projects land in one repo as dated subfolders. The root README is auto-updated as a running index. Good for keeping your main GitHub profile clean.

```
github.com/asmit25805/trendforge-output/
  ├── 2026-06-28-skillforge-cli/
  ├── 2026-06-27-flowforge/
  └── 2026-06-26-llm-router/
```

Switch modes by setting `TRENDFORGE_MONOREPO=1` in your repo secrets — no code changes needed.

---

## Setup

### 1. Fork or clone this repo

```bash
git clone https://github.com/asmit25805/trend-forge
cd trend-forge
```

### 2. Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `PAT_TOKEN` | GitHub Personal Access Token with `repo` + `workflow` scopes |
| `GH_USERNAME` | Your GitHub username |
| `CEREBRAS_API_KEY` | From [cloud.cerebras.ai](https://cloud.cerebras.ai) |
| `TRENDFORGE_MONOREPO` | `1` for monorepo mode, `0` or omit for standalone |

> Use a PAT, not the default `GITHUB_TOKEN`. The built-in token can't create new repos or push to other repos on your account.

### 3. Run manually to test

Go to **Actions → TrendForge Daily Pipeline → Run workflow**

Watch the logs — a full run takes 8–15 minutes depending on file count and Cerebras queue time.

### 4. Automatic daily runs

The pipeline fires every day at **04:00 UTC** automatically. No action needed after setup.

---

## Run locally

```bash
pip install -r requirements.txt

export PAT_TOKEN=ghp_your_token
export GH_USERNAME=your_username
export CEREBRAS_API_KEY=your_cerebras_key
export TRENDFORGE_MONOREPO=1   # optional

python src/main.py
```

---

## Key engineering decisions

**Why Cerebras instead of OpenAI/Anthropic?**
Speed. Cerebras inference runs at ~1000–2000 tokens/second on their hardware. A full 4-phase generation run (design + plan + 12 files + validator) completes in under 10 minutes. The same run on GPT-4o would take 30–45 minutes and cost significantly more per run.

**Why Git Tree API instead of Contents API?**
The Contents API has a race condition on newly created repos — pushing to `.github/workflows/` while the branch is still indexing returns 404. The Git Tree API is atomic: one call creates the entire tree and commits it in one shot, regardless of directory depth.

**Why a shared types/models file?**
Cerebras models frequently import types across files without planning them, which causes CI failures when the import target doesn't exist. Phase 1 enforces a single canonical types file (`src/types.ts`, `src/core/models.py`, etc.) and Phase 2 tells every file generator exactly where to import from.

**Why 3 stub recovery stages instead of just retrying?**
Different failure modes need different approaches. The generator fails with stubs when it runs out of token budget or gets confused about context — retrying with the same prompt often fails again. The reviewer-mode fallback uses a completely different framing ("this file failed, rewrite from scratch") at lower temperature, which succeeds in a majority of cases where both generator attempts failed.

**Why skip rather than keep a stub?**
A missing file causes an explicit `ImportError` at import time — easy to see, easy to fix. A stub with `pass` or `raise NotImplementedError` passes import and fails silently at runtime or in tests with a confusing error. The validator catches broken imports from skipped files. It can't easily catch a stub that looks like real code from a 400-char snapshot.

---

## What gets generated

Each output project includes:

- Full working source code (not scaffolding) across 10–14 files
- README with overview, install, quickstart, architecture diagram, and API reference
- CI workflow (GitHub Actions) configured for the project's language
- Test suite with real assertions (minimum 6 tests per file)
- Language-appropriate dependency file (`requirements.txt`, `package.json`, `go.mod`, etc.)
- MIT LICENSE
- CONTRIBUTING.md
- Issue templates (bug report + feature request)

What it never includes: copied source code, fake emails, placeholder text, stub implementations, or any mention of AI generation.

---

## Projects generated so far

| Project | Description | Category | Inspired by |
|---------|-------------|----------|-------------|
| [native-mathml-optimizer](https://github.com/asmit25805/native-mathml-optimizer) | TeX→MathML lexer/parser/renderer in TypeScript | developer-tools | — |
| [codepilot-cli](https://github.com/asmit25805/codepilot-cli) | AI-powered refactor tool for TypeScript codebases | cli | — |
| [flowforge](https://github.com/asmit25805/flowforge) | Filesystem-driven workflow engine with SQLite | automation | — |
| [llm-router](https://github.com/asmit25805/llm-router) | LLM proxy with plugin architecture and policy engine | llm | — |
| [autocode-orchestrator](https://github.com/asmit25805/autocode-orchestrator) | 0 stubs, 3 validator fixes — cleanest run | agent | — |
| [browser-llm-proxy](https://github.com/asmit25805/browser-llm-proxy) | OpenAI-compatible proxy for web LLMs | api | — |
| [testpilot](https://github.com/asmit25805/testpilot) | Terminal test generator and runner | developer-tools | — |

---

## Ethics

TrendForge never copies source code. It reads a trending repo to understand the concept and architecture, then generates an entirely original project. Every generated README credits the source repo for inspiration.

The goal is to explore what autonomous software generation looks like at scale — not to flood GitHub with noise, which is why monorepo mode exists.

---

## License

© 2026 [asmit25805](https://github.com/asmit25805) · [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)

You are free to read this code, learn from it, and build something inspired by the ideas here.
You may not copy, redistribute, or use this codebase (or substantial parts of it) for commercial purposes without explicit permission.

If you build something inspired by TrendForge, a mention or link back is appreciated but not required.

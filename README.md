# TrendForge 🔥

> Finds trending GitHub repos daily, analyzes what makes them compelling, and generates an **original inspired project** — then pushes it straight to your GitHub account.

Powered by **Cerebras** (ultra-fast inference) + **GitHub API**. Runs automatically every day via GitHub Actions.

---

## How it works

```
GitHub Trending Search
        ↓
  Pick unseen repo (never repeats)
        ↓
  Cerebras analyzes the concept
        ↓
  Cerebras generates original code
        ↓
  Push new repo to your GitHub
        ↓
  Log run + mark repo as seen
```

## Setup

### 1. Clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/trend-forge
cd trend-forge
```

### 2. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|--------|-------|
| `PAT_TOKEN` | A GitHub Personal Access Token with `repo` scope |
| `GITHUB_USERNAME` | Your GitHub username (e.g. `asmit25805`) |
| `CEREBRAS_API_KEY` | Your Cerebras API key from [cloud.cerebras.ai](https://cloud.cerebras.ai) |

> ⚠️ Use a **PAT** (Personal Access Token), not the default `GITHUB_TOKEN`, because we need to create repos on your account. The built-in `GITHUB_TOKEN` can't do that.

### 3. Run manually to test
Go to **Actions → TrendForge Daily Pipeline → Run workflow**

### 4. It runs every day at 6:00 AM IST automatically (no action needed)

---

## What gets generated

Each run picks one trending repo from categories like:
`ai · developer-tools · web · cli · automation · llm · agent · api`

Cerebras analyzes it → produces an original project with:
- Full working code (not a skeleton)
- README with "Inspired by" credit
- Proper topics/tags on the new GitHub repo

---

## Files

```
trend-forge/
├── src/
│   ├── main.py          # Pipeline orchestrator
│   ├── trending.py      # GitHub trending repo fetcher
│   ├── analyzer.py      # Cerebras repo analyzer
│   ├── generator.py     # Cerebras original code generator
│   └── pusher.py        # GitHub repo creator + file pusher
├── .github/
│   └── workflows/
│       └── daily.yml    # GitHub Actions cron job
├── seen_repos.json      # Auto-updated: never repeats a source repo
├── run_log.json         # Auto-updated: history of all runs
└── requirements.txt
```

---

## Run locally

```bash
pip install -r requirements.txt

export GITHUB_TOKEN=ghp_your_token
export GITHUB_USERNAME=asmit25805
export CEREBRAS_API_KEY=your_cerebras_key

python src/main.py
```

---

## Ethics note

TrendForge **never copies source code**. It analyzes the *concept* of a trending repo and generates a completely original project. The generated README always credits the source repo for inspiration.

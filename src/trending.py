"""
Fetches trending GitHub repos by category using GitHub Search API.
Avoids repos already seen (tracked in seen_repos.json).
Feature: Smart category rotation — cycles evenly so profile stays diverse.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
SEEN_FILE = Path(__file__).parent.parent / "seen_repos.json"

CATEGORIES = [
    "ai",
    "developer-tools",
    "web",
    "cli",
    "automation",
    "llm",
    "agent",
    "api",
]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


# ── Seen + rotation state ─────────────────────────────────────────────────────

def load_seen() -> dict:
    """
    Returns full state dict:
      {
        "repos": ["owner/repo", ...],       # all processed source repos
        "last_category_index": 3            # which category was used last
      }
    Backwards compatible — if file is just a list, migrates it.
    """
    if not SEEN_FILE.exists():
        return {"repos": [], "last_category_index": -1}
    raw = json.loads(SEEN_FILE.read_text())
    if isinstance(raw, list):
        # Migrate old format
        return {"repos": raw, "last_category_index": -1}
    return raw


def save_seen(state: dict):
    SEEN_FILE.write_text(json.dumps(state, indent=2))


def get_next_category(state: dict) -> tuple[str, int]:
    """
    Returns (category, new_index) using round-robin rotation.
    Starts from the category after the last used one.
    """
    last = state.get("last_category_index", -1)
    next_index = (last + 1) % len(CATEGORIES)
    return CATEGORIES[next_index], next_index


# ── GitHub API ────────────────────────────────────────────────────────────────

def fetch_trending(category: str, limit: int = 5) -> list[dict]:
    """Fetch top starred repos in a topic created in the last 30 days."""
    since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    query = f"topic:{category} created:>{since} stars:>75"
    url = "https://api.github.com/search/repositories"
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": limit,
    }
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        {
            "full_name": r["full_name"],
            "html_url": r["html_url"],
            "description": r.get("description") or "",
            "stars": r["stargazers_count"],
            "language": r.get("language") or "Unknown",
            "topics": r.get("topics", []),
            "readme_url": f"https://api.github.com/repos/{r['full_name']}/readme",
            "category": category,
        }
        for r in items
    ]


def get_readme(repo: dict) -> str:
    """Fetch and decode the README for a repo."""
    try:
        resp = requests.get(
            repo["readme_url"],
            headers={**HEADERS, "Accept": "application/vnd.github.raw"},
        )
        if resp.status_code == 200:
            return resp.text[:6000]
    except Exception:
        pass
    return ""


def pick_unseen_repo(state: dict, per_category: int = 5) -> tuple[dict | None, int]:
    """
    Pick an unseen trending repo using round-robin category rotation.
    Tries the next category first, then falls back to others if needed.
    Returns (repo, new_category_index) or (None, unchanged_index).
    """
    seen_repos = set(state.get("repos", []))
    last_index = state.get("last_category_index", -1)

    # Build rotation order: start from next category, wrap around
    rotation = [(last_index + 1 + i) % len(CATEGORIES) for i in range(len(CATEGORIES))]

    for idx in rotation:
        category = CATEGORIES[idx]
        print(f"[trending] Checking category: {category}")
        try:
            repos = fetch_trending(category, limit=per_category)
        except Exception as e:
            print(f"[trending] Error fetching {category}: {e}")
            continue

        for repo in repos:
            if repo["full_name"] not in seen_repos:
                repo["readme"] = get_readme(repo)
                print(f"[trending] ✓ Found unseen repo in '{category}': {repo['full_name']}")
                return repo, idx

    return None, last_index

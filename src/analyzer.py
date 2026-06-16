"""
Analyzes trending repos using Cerebras.
Reads README + actual source code files + directory tree for deep understanding.
"""

import os
import json
import requests
from cerebras.cloud.sdk import Cerebras

client = Cerebras(api_key=os.environ["CEREBRAS_API_KEY"])
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# Extensions worth reading — skip assets, locks, binaries
READABLE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".java", ".cpp", ".c", ".h", ".rb", ".sh", ".toml",
    ".yaml", ".yml", ".json", ".md", ".txt", ".env.example",
}

# Files to skip even if extension matches
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "poetry.lock", "go.sum",
    "Pipfile.lock", "composer.lock", ".DS_Store",
}


# ── GitHub source code fetching ───────────────────────────────────────────────

def fetch_file_tree(full_name: str) -> list[dict]:
    """
    Fetch the full file tree of a repo using the Git Trees API.
    Returns list of {path, type, size} for all files.
    """
    url = f"https://api.github.com/repos/{full_name}/git/trees/HEAD?recursive=1"
    try:
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return []
        tree = resp.json().get("tree", [])
        return [
            {"path": item["path"], "type": item["type"], "size": item.get("size", 0)}
            for item in tree
            if item["type"] == "blob"
        ]
    except Exception:
        return []


def pick_files_to_read(tree: list[dict], max_files: int = 6) -> list[str]:
    """
    Pick the most informative source files to read.
    Priority: entry points > core logic > config > tests (last).
    Skips huge files (>30KB), lock files, and non-readable extensions.
    """
    import os as _os

    priority = []
    secondary = []
    skip_dirs = {"node_modules", ".git", "dist", "build", "__pycache__", ".next", "vendor"}

    for item in tree:
        path = item["path"]
        size = item.get("size", 0)

        # Skip directories we don't care about
        parts = path.split("/")
        if any(p in skip_dirs for p in parts):
            continue

        # Skip huge files and lock files
        if size > 30_000:
            continue
        filename = _os.path.basename(path)
        if filename in SKIP_NAMES:
            continue

        ext = _os.path.splitext(filename)[1].lower()
        if ext not in READABLE_EXTENSIONS:
            continue

        # Prioritise entry points and core modules
        name_lower = filename.lower()
        is_entry = any(n in name_lower for n in ["main", "index", "app", "cli", "server", "run", "core", "engine"])
        is_test = any(n in name_lower for n in ["test", "spec", "__test__"])
        is_config = any(n in name_lower for n in ["config", "setting", "env", ".toml", ".yaml"])

        if is_entry and not is_test:
            priority.append(path)
        elif not is_test and not is_config:
            secondary.append(path)

    # Take up to max_files: prioritise entry points, fill with secondary
    chosen = priority[:3] + secondary[:max_files - len(priority[:3])]
    return chosen[:max_files]


def fetch_file_content(full_name: str, path: str) -> str:
    """Fetch raw content of a single file from GitHub."""
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    try:
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/vnd.github.raw"})
        if resp.status_code == 200:
            return resp.text[:4000]  # cap per file at 4KB
    except Exception:
        pass
    return ""


def fetch_recent_commits(full_name: str, limit: int = 5) -> list[str]:
    """Fetch recent commit messages to understand development direction."""
    url = f"https://api.github.com/repos/{full_name}/commits"
    try:
        resp = requests.get(url, headers=HEADERS, params={"per_page": limit})
        if resp.status_code == 200:
            return [
                c["commit"]["message"].split("\n")[0]  # first line only
                for c in resp.json()
            ]
    except Exception:
        pass
    return []


def gather_repo_context(repo: dict) -> dict:
    """
    Pull everything useful from the trending repo:
    - README (already fetched)
    - Directory tree (structure overview)
    - Top source files (actual implementation)
    - Recent commit messages (development direction)
    """
    full_name = repo["full_name"]
    print(f"[analyzer] Fetching source context for {full_name}...")

    tree = fetch_file_tree(full_name)
    print(f"[analyzer]   File tree: {len(tree)} files total")

    files_to_read = pick_files_to_read(tree, max_files=6)
    print(f"[analyzer]   Reading {len(files_to_read)} source files: {files_to_read}")

    source_files = {}
    for path in files_to_read:
        content = fetch_file_content(full_name, path)
        if content:
            source_files[path] = content

    commits = fetch_recent_commits(full_name, limit=5)
    print(f"[analyzer]   Recent commits: {len(commits)}")

    # Build a clean directory tree summary (top 40 paths)
    tree_summary = "\n".join(
        item["path"] for item in tree[:40]
    )

    return {
        "tree_summary": tree_summary,
        "source_files": source_files,
        "recent_commits": commits,
    }


# ── Cerebras analysis ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior software architect reverse-engineering trending GitHub repositories.
You have access to actual source code, not just the README.
Extract deep technical understanding: architecture patterns, implementation choices, what makes this genuinely good.
Respond ONLY with valid JSON."""

ANALYSIS_SCHEMA = """
{
  "concept": "one-sentence description of what the tool does",
  "problem_solved": "specific pain point with technical detail",
  "why_trending": "concrete technical reasons — not just 'simple' or 'fast'",
  "core_features": ["specific feature with impl detail", "..."],
  "tech_stack": ["lang", "framework", "key library"],
  "architecture_pattern": "e.g. pipeline, agent loop, plugin system, event-driven",
  "key_implementation_insights": ["interesting impl choice 1", "interesting impl choice 2"],
  "target_users": "who uses this and in what workflow",
  "inspiration_angle": "a concrete original twist — reference specific patterns seen in the code"
}
"""


def analyze_repo(repo: dict) -> dict:
    """
    Deep analysis of a trending repo using Cerebras.
    Reads actual source code for genuine technical understanding.
    """
    context = gather_repo_context(repo)

    # Build source code section
    source_section = ""
    for path, content in context["source_files"].items():
        source_section += f"\n--- {path} ---\n{content}\n"

    commits_section = "\n".join(f"  • {c}" for c in context["recent_commits"])

    prompt = f"""Analyze this trending GitHub repository. You have its actual source code.

REPO: {repo['full_name']} (⭐{repo['stars']}, {repo['language']})
Topics: {', '.join(repo['topics'])}
Description: {repo['description']}
Category: {repo['category']}

DIRECTORY STRUCTURE (first 40 files):
{context['tree_summary']}

RECENT COMMITS:
{commits_section}

README:
{repo.get('readme', '')[:2000]}

SOURCE CODE:
{source_section}

Based on the actual code above, return ONLY JSON matching this schema:
{ANALYSIS_SCHEMA}

Be specific — reference actual patterns, function names, or architecture choices you saw in the code."""

    response = client.chat.completions.create(
        model="gpt-oss-120b",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=1500,
        temperature=0.3,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    analysis = json.loads(raw)
    analysis["source_repo"] = repo["full_name"]
    analysis["source_url"] = repo["html_url"]
    analysis["source_stars"] = repo["stars"]
    analysis["category"] = repo["category"]

    # Pass source context through to generator for even richer generation
    analysis["_source_context"] = {
        "tree_summary": context["tree_summary"],
        "source_files": context["source_files"],
    }

    return analysis

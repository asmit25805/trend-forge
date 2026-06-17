"""
Analyzes trending repos using Cerebras.
Reads README + actual source code files + directory tree for deep understanding.
"""

import os
import json
import time
import requests
from cerebras.cloud.sdk import Cerebras

client = Cerebras(
    api_key=os.environ["CEREBRAS_API_KEY"],
    timeout=60,
    max_retries=0,
)
GITHUB_TOKEN = os.environ["PAT_TOKEN"]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

READABLE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".java", ".cpp", ".c", ".h", ".rb", ".sh", ".toml",
    ".yaml", ".yml", ".json", ".md", ".txt", ".env.example",
}

SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "poetry.lock", "go.sum",
    "Pipfile.lock", "composer.lock", ".DS_Store",
}

# Directories that contain tooling/config noise, not the actual project logic
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__",
    ".next", "vendor", ".claude", ".cursor", ".idea", ".vscode",
    ".github", "migrations", "fixtures", "assets", "static",
}


# ── GitHub source code fetching ───────────────────────────────────────────────

def fetch_file_tree(full_name: str) -> list[dict]:
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
    import os as _os

    priority = []
    secondary = []

    for item in tree:
        path = item["path"]
        size = item.get("size", 0)

        parts = path.split("/")

        # Skip noise directories — this is the key fix that prevents the
        # analyzer from reading .claude/skills, .github/workflows, etc.
        # instead of actual source code.
        if any(p in SKIP_DIRS for p in parts):
            continue

        if size > 30_000:
            continue

        filename = _os.path.basename(path)
        if filename in SKIP_NAMES:
            continue

        ext = _os.path.splitext(filename)[1].lower()
        if ext not in READABLE_EXTENSIONS:
            continue

        # Skip markdown/yaml that are clearly docs or config, not logic
        if ext in (".md", ".yml", ".yaml", ".json") and len(parts) <= 1:
            continue

        name_lower = filename.lower()
        is_entry = any(n in name_lower for n in [
            "main", "index", "app", "cli", "server", "run", "core",
            "engine", "agent", "pipeline", "orchestrat",
        ])
        is_test = any(n in name_lower for n in ["test", "spec", "__test__"])
        is_config = ext in (".toml", ".yaml", ".yml", ".json", ".env")

        if is_entry and not is_test:
            priority.append(path)
        elif not is_test and not is_config:
            secondary.append(path)

    chosen = priority[:3] + secondary[:max_files - len(priority[:3])]
    return chosen[:max_files]


def fetch_file_content(full_name: str, path: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    try:
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/vnd.github.raw"})
        if resp.status_code == 200:
            return resp.text[:4000]
    except Exception:
        pass
    return ""


def fetch_recent_commits(full_name: str, limit: int = 5) -> list[str]:
    url = f"https://api.github.com/repos/{full_name}/commits"
    try:
        resp = requests.get(url, headers=HEADERS, params={"per_page": limit})
        if resp.status_code == 200:
            return [
                c["commit"]["message"].split("\n")[0]
                for c in resp.json()
            ]
    except Exception:
        pass
    return []


def gather_repo_context(repo: dict) -> dict:
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

    # Only include actual source paths in the tree summary, not noise dirs
    source_tree = [
        item for item in tree
        if not any(p in SKIP_DIRS for p in item["path"].split("/"))
    ]
    tree_summary = "\n".join(item["path"] for item in source_tree[:40])

    return {
        "tree_summary": tree_summary,
        "source_files": source_files,
        "recent_commits": commits,
    }


# ── JSON recovery ─────────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    idx = raw.rfind("}")
    if idx != -1:
        return raw[: idx + 1]
    return raw


# ── Cerebras call with retry ──────────────────────────────────────────────────

def _call(messages: list, max_tokens: int = 2048, temperature: float = 0.3) -> str:
    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-oss-120b",
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            err = str(e).lower()
            is_retryable = any(x in err for x in (
                "429", "too_many_requests", "queue_exceeded", "timeout", "timed out"
            ))
            if is_retryable and attempt < max_retries - 1:
                wait = 15 * (attempt + 1)
                print(f"  [analyzer] rate limit, waiting {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Cerebras: max retries exceeded in analyzer")


# ── Cerebras analysis ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior software architect reverse-engineering trending GitHub repositories.
You have access to actual source code, not just the README.
Extract deep technical understanding: architecture patterns, implementation choices, what makes this genuinely useful.
Respond ONLY with valid JSON. Never truncate — always close every brace and bracket."""

ANALYSIS_SCHEMA = """
{
  "concept": "one-sentence description of what the tool does technically",
  "problem_solved": "specific developer pain point this solves, with technical detail",
  "why_trending": "concrete technical reasons grounded in the actual code you read",
  "core_features": ["feature tied to a specific file or function you saw", "..."],
  "tech_stack": ["lang", "framework", "key library"],
  "architecture_pattern": "e.g. pipeline, agent loop, plugin system, event-driven",
  "key_implementation_insights": [
    "specific non-obvious impl choice with the function/file name",
    "another specific insight"
  ],
  "target_users": "who uses this and in what workflow",
  "inspiration_angle": "a concrete original twist on this idea — different enough to be its own project"
}
"""


def analyze_repo(repo: dict) -> dict:
    context = gather_repo_context(repo)

    source_section = ""
    for path, content in context["source_files"].items():
        source_section += f"\n--- {path} ---\n{content}\n"

    if not source_section.strip():
        source_section = "(no readable source files found — base analysis on README and description)"

    commits_section = "\n".join(f"  • {c}" for c in context["recent_commits"])

    prompt = f"""Analyze this trending GitHub repository. You have its actual source code.

REPO: {repo['full_name']} (⭐{repo['stars']}, {repo['language']})
Topics: {', '.join(repo['topics'])}
Description: {repo['description']}
Category: {repo['category']}

DIRECTORY STRUCTURE (source files only):
{context['tree_summary']}

RECENT COMMITS:
{commits_section}

README:
{repo.get('readme', '')[:2000]}

SOURCE CODE:
{source_section}

Based on the actual code above, return ONLY JSON matching this schema:
{ANALYSIS_SCHEMA}

Rules:
- Reference actual function names, file paths, or patterns you saw in the code
- The inspiration_angle must be different enough to be a separate project, not a fork
- Never use filler phrases like "simple", "easy to use", "lightweight" without backing them up
- Produce complete valid JSON — do not truncate"""

    raw = _call(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2048,
        temperature=0.3,
    )

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError:
        raw = _repair_json(raw)
        analysis = json.loads(raw)

    analysis["source_repo"] = repo["full_name"]
    analysis["source_url"] = repo["html_url"]
    analysis["source_stars"] = repo["stars"]
    analysis["category"] = repo["category"]

    analysis["_source_context"] = {
        "tree_summary": context["tree_summary"],
        "source_files": context["source_files"],
    }

    return analysis

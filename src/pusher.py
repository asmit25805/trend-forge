"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups to look like real development history.

Key fix: .github/ files are pushed immediately after README in the same
initial batch — GitHub's Contents API reliably handles them once the
main branch exists.
"""

import os
import time
import base64
import requests
from datetime import datetime

GITHUB_TOKEN = os.environ["PAT_TOKEN"]
GITHUB_USERNAME = os.environ["GH_USERNAME"]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def create_repo(name: str, description: str, topics: list[str]) -> str:
    url = "https://api.github.com/user/repos"
    payload = {
        "name": name,
        "description": description,
        "private": False,
        "auto_init": False,
        "has_issues": True,
        "has_projects": False,
        "has_wiki": False,
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    if resp.status_code == 422:
        suffix = datetime.utcnow().strftime("%m%d%H%M")
        payload["name"] = f"{name}-{suffix}"
        resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    full_name = resp.json()["full_name"]
    print(f"[pusher] Created: https://github.com/{full_name}")
    if topics:
        _set_topics(full_name, topics)
    return full_name


def _set_topics(full_name: str, topics: list[str]):
    url = f"https://api.github.com/repos/{full_name}/topics"
    clean = [t.lower().replace(" ", "-")[:50] for t in topics[:20]]
    requests.put(url, headers=HEADERS, json={"names": clean})


def _push_file(full_name: str, path: str, content: str, message: str, max_retries: int = 4):
    """Push a single file with retry on 429 rate limit only."""
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": encoded, "branch": "main"}

    existing = requests.get(url, headers=HEADERS)
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]

    for attempt in range(max_retries):
        resp = requests.put(url, headers=HEADERS, json=payload)
        if resp.status_code in (200, 201):
            return
        if resp.status_code == 429:
            wait = (attempt + 1) * 10
            print(f"  [rate limit] waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()

    raise RuntimeError(f"Failed to push {path} after {max_retries} attempts")


def push_project(project: dict) -> str:
    """
    Push all files in a logical commit order.

    Commit order:
      1. README.md                  — creates the main branch
      2. .github/ files             — pushed immediately after, branch now exists
      3. Config/dependency files    — package.json, requirements.txt etc
      4. Core source files          — src/core, engine, pipeline etc
      5. Supporting modules         — utils, helpers, plugins etc
      6. Tests                      — test/, spec/
      7. Docs and examples          — docs/, examples/
      8. Everything else
    """
    full_name = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    readme = project.get("readme", f"# {project['repo_name']}\n")
    files: dict = project.get("files", {})

    # Sort all files into ordered buckets
    github_files   = {p: c for p, c in files.items() if p.startswith(".github/")}
    config_files   = {p: c for p, c in files.items() if _is_config(p)}
    core_files     = {p: c for p, c in files.items() if _is_core(p)}
    support_files  = {p: c for p, c in files.items() if _is_support(p)}
    test_files     = {p: c for p, c in files.items() if _is_test(p)}
    doc_files      = {p: c for p, c in files.items() if _is_doc(p)}
    assigned = (
        set(github_files) | set(config_files) | set(core_files) |
        set(support_files) | set(test_files) | set(doc_files)
    )
    other_files = {p: c for p, c in files.items() if p not in assigned}

    batches = [
        ("Initial commit",                        {"README.md": readme}),
        ("ci: add GitHub Actions workflow",        github_files),
        ("chore: project setup and dependencies", config_files),
        ("feat: implement core engine",            core_files),
        ("feat: add supporting modules",           support_files),
        ("test: add test suite",                   test_files),
        ("docs: add examples and documentation",   doc_files),
        ("feat: add remaining modules",            other_files),
    ]

    total = 1 + sum(len(b[1]) for b in batches[1:])
    print(f"[pusher] Pushing {total} files across {len([b for b in batches if b[1]])} commits...")

    for commit_msg, batch in batches:
        if not batch:
            continue
        print(f"\n[pusher] Commit: '{commit_msg}' ({len(batch)} file(s))")
        for path, content in batch.items():
            if not isinstance(content, str):
                content = str(content)
            _push_file(full_name, path, content, commit_msg)
            print(f"  ✓ {path}")
            time.sleep(1)
        time.sleep(2)

    repo_url = f"https://github.com/{full_name}"
    print(f"\n[pusher] ✅ Done! Live at: {repo_url}")
    return repo_url


# ── File classification helpers ───────────────────────────────────────────────

def _is_config(path: str) -> bool:
    name = path.split("/")[-1].lower()
    return any(name == f for f in [
        "package.json", "requirements.txt", "go.mod", "cargo.toml",
        "pyproject.toml", ".gitignore", ".env.example", "makefile",
        "tsconfig.json", "license", "licence", "setup.py", "setup.cfg",
        "dockerfile", ".dockerignore", "docker-compose.yml",
    ])

def _is_core(path: str) -> bool:
    parts = path.lower().split("/")
    return any(seg in parts for seg in ["core", "engine", "pipeline", "runtime", "bootstrap"]) \
        and not _is_test(path)

def _is_support(path: str) -> bool:
    parts = path.lower().split("/")
    return any(seg in parts for seg in [
        "util", "utils", "helper", "helpers", "common", "shared",
        "plugin", "plugins", "agent", "agents", "cli", "cmd",
        "api", "route", "routes", "server", "handler", "handlers",
        "model", "models", "schema", "schemas", "config", "logger",
    ]) and not _is_test(path) and not _is_core(path)

def _is_test(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return any(seg in parts for seg in ["test", "tests", "spec", "specs", "__tests__"]) \
        or ".test." in name or ".spec." in name

def _is_doc(path: str) -> bool:
    parts = path.lower().split("/")
    return any(seg in parts for seg in ["doc", "docs", "example", "examples", "demo", "notebook"])

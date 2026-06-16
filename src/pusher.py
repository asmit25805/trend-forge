"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups to look like real development history.
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


def _push_file(full_name: str, path: str, content: str, message: str):
    """
    Push a single file. Retries on 429 (rate limit) and 404 (branch not
    indexed yet).
    """
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": encoded, "branch": "main"}

    existing = requests.get(url, headers=HEADERS)
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]

    wait_404 = [5, 10, 15, 20, 30, 45]
    wait_429 = [10, 20, 30, 40]

    attempt_404 = 0
    attempt_429 = 0

    while True:
        resp = requests.put(url, headers=HEADERS, json=payload)

        if resp.status_code in (200, 201):
            return

        if resp.status_code == 429:
            if attempt_429 >= len(wait_429):
                resp.raise_for_status()
            w = wait_429[attempt_429]
            print(f"  [429 rate limit] waiting {w}s...")
            time.sleep(w)
            attempt_429 += 1
            continue

        if resp.status_code == 404:
            if attempt_404 >= len(wait_404):
                resp.raise_for_status()
            w = wait_404[attempt_404]
            print(f"  [404 {path}] branch not indexed yet, waiting {w}s...")
            time.sleep(w)
            attempt_404 += 1
            continue

        resp.raise_for_status()


def push_project(project: dict) -> str:
    """
    Push all files in a logical commit order.

    README and .github/ files go together in the first commit so the branch
    is fully created before any subsequent commits touch nested paths.
    Everything else follows grouped into meaningful commits.
    """
    full_name = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    readme = project.get("readme", f"# {project['repo_name']}\n")
    files: dict = project.get("files", {})

    # Bucket files by type — order matters for commit history
    github_files  = {p: c for p, c in files.items() if p.startswith(".github/")}
    config_files  = {p: c for p, c in files.items() if _is_config(p)}
    core_files    = {p: c for p, c in files.items() if _is_core(p)}
    support_files = {p: c for p, c in files.items() if _is_support(p)}
    test_files    = {p: c for p, c in files.items() if _is_test(p)}
    doc_files     = {p: c for p, c in files.items() if _is_doc(p)}
    assigned = (
        set(github_files) | set(config_files) | set(core_files) |
        set(support_files) | set(test_files) | set(doc_files)
    )
    other_files = {p: c for p, c in files.items() if p not in assigned}

    # FIX: .github/ files are pushed in the same first commit as README so
    # the branch is fully indexed before any later commits try to write to it.
    # Pushing .github/workflows/* in a separate second commit caused a 404
    # race condition because GitHub hadn't finished indexing the new branch.
    initial_batch = {"README.md": readme, **github_files}

    batches = [
        ("Initial commit",                        initial_batch),
        ("chore: project setup and dependencies", config_files),
        ("feat: implement core engine",            core_files),
        ("feat: add supporting modules",           support_files),
        ("test: add test suite",                   test_files),
        ("docs: add examples and documentation",   doc_files),
        ("feat: add remaining modules",            other_files),
    ]

    total = sum(len(b[1]) for b in batches)
    active = [b for b in batches if b[1]]
    print(f"[pusher] Pushing {total} files across {len(active)} commits...")

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
    return name in {
        "package.json", "requirements.txt", "go.mod", "cargo.toml",
        "pyproject.toml", ".gitignore", ".env.example", "makefile",
        "tsconfig.json", "license", "licence", "setup.py", "setup.cfg",
        "dockerfile", ".dockerignore", "docker-compose.yml",
    }

def _is_core(path: str) -> bool:
    parts = path.lower().split("/")
    keywords = {"core", "engine", "pipeline", "runtime", "bootstrap"}
    return any(p in keywords for p in parts) and not _is_test(path)

def _is_support(path: str) -> bool:
    parts = path.lower().split("/")
    keywords = {
        "util", "utils", "helper", "helpers", "common", "shared",
        "plugin", "plugins", "agent", "agents", "cli", "cmd", "bin",
        "api", "route", "routes", "server", "handler", "handlers",
        "model", "models", "schema", "schemas", "config", "logger",
        "state", "store", "service", "services",
    }
    return any(p in keywords for p in parts) and not _is_test(path) and not _is_core(path)

def _is_test(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return (
        any(p in {"test", "tests", "spec", "specs", "__tests__"} for p in parts)
        or ".test." in name
        or ".spec." in name
    )

def _is_doc(path: str) -> bool:
    parts = path.lower().split("/")
    return any(p in {"doc", "docs", "example", "examples", "demo", "notebook"} for p in parts)

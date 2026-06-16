"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups to look like real development history.

Uses the Git Tree API to push each batch as a single atomic commit, which
avoids the race condition where the Contents API 404s on nested paths
(e.g. .github/workflows/) because the branch isn't indexed yet.
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


# ── Repo creation ─────────────────────────────────────────────────────────────

def create_repo(name: str, description: str, topics: list[str]) -> tuple[str, str]:
    """Returns (full_name, init_commit_sha) — the auto-init commit to build on."""
    url = "https://api.github.com/user/repos"
    payload = {
        "name": name,
        "description": description,
        "private": False,
        "auto_init": True,   # provisions the git object store immediately
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

    # Fetch the auto-init commit SHA so our first batch can use it as parent.
    # Retry briefly — GitHub may not have written the ref yet.
    init_sha = None
    for _ in range(8):
        r = requests.get(
            f"https://api.github.com/repos/{full_name}/git/refs/heads/main",
            headers=HEADERS,
        )
        if r.status_code == 200:
            init_sha = r.json()["object"]["sha"]
            break
        time.sleep(2)

    if not init_sha:
        raise RuntimeError(f"[pusher] Could not resolve auto-init commit for {full_name}")

    return full_name, init_sha


def _set_topics(full_name: str, topics: list[str]):
    url = f"https://api.github.com/repos/{full_name}/topics"
    clean = [t.lower().replace(" ", "-")[:50] for t in topics[:20]]
    requests.put(url, headers=HEADERS, json={"names": clean})


# ── Git Tree API helpers ──────────────────────────────────────────────────────

def _create_blob(full_name: str, content: str) -> str:
    """Upload file content as a blob and return its SHA."""
    url = f"https://api.github.com/repos/{full_name}/git/blobs"
    payload = {
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "encoding": "base64",
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["sha"]


def _create_tree(full_name: str, base_tree_sha: str | None, blobs: list[dict]) -> str:
    url = f"https://api.github.com/repos/{full_name}/git/trees"
    tree = [
        {"path": b["path"], "mode": "100644", "type": "blob", "sha": b["blob_sha"]}
        for b in blobs
    ]
    payload = {"tree": tree}
    if base_tree_sha:
        payload["base_tree"] = base_tree_sha
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["sha"]


def _create_commit(full_name: str, message: str, tree_sha: str, parent_sha: str | None) -> str:
    url = f"https://api.github.com/repos/{full_name}/git/commits"
    payload = {"message": message, "tree": tree_sha}
    if parent_sha:
        payload["parents"] = [parent_sha]
    else:
        payload["parents"] = []
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["sha"]


def _update_ref(full_name: str, commit_sha: str, create: bool = False):
    # Always force-update — ref already exists from auto-init.
    # force=True covers both advancing the branch and overwriting it.
    url = f"https://api.github.com/repos/{full_name}/git/refs/heads/main"
    payload = {"sha": commit_sha, "force": True}
    resp = requests.patch(url, headers=HEADERS, json=payload)
    resp.raise_for_status()


def _push_batch(full_name: str, batch: dict[str, str], message: str, parent_sha: str | None) -> str:
    blobs = []
    for path, content in batch.items():
        if not isinstance(content, str):
            content = str(content)
        blob_sha = _create_blob(full_name, content)
        blobs.append({"path": path, "blob_sha": blob_sha})
        print(f"  ✓ {path}")

    base_tree = None
    if parent_sha:
        url = f"https://api.github.com/repos/{full_name}/git/commits/{parent_sha}"
        r = requests.get(url, headers=HEADERS)
        r.raise_for_status()
        base_tree = r.json()["tree"]["sha"]

    tree_sha = _create_tree(full_name, base_tree, blobs)
    commit_sha = _create_commit(full_name, message, tree_sha, parent_sha)
    _update_ref(full_name, commit_sha)

    return commit_sha


# ── Main push entry point ─────────────────────────────────────────────────────

def push_project(project: dict) -> str:
    full_name, parent_sha = create_repo(   # seed parent_sha from auto-init commit
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    readme = project.get("readme", f"# {project['repo_name']}\n")
    files: dict = project.get("files", {})

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
        parent_sha = _push_batch(full_name, batch, commit_msg, parent_sha)
        time.sleep(1)

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

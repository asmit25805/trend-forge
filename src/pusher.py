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
    """
    Creates an empty repo, seeds the git DB with an empty root commit,
    and returns (full_name, root_commit_sha) ready for _push_batch.
    """
    url = "https://api.github.com/user/repos"
    payload = {
        "name": name,
        "description": description,
        "private": False,
        "auto_init": False,   # we seed the git DB ourselves
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

    # Seed the git object store with an empty tree + root commit + main ref.
    # This avoids all 409/404 races: we own every object in the repo from
    # the start, and parent_sha is always a SHA we created ourselves.
    root_sha = _seed_repo(full_name)
    return full_name, root_sha


def _seed_repo(full_name: str) -> str:
    """Create an empty tree → root commit → refs/heads/main. Returns commit SHA."""
    # 1. Empty tree (git's well-known empty tree SHA works via the API too,
    #    but creating our own is safer across GitHub's internal routing)
    r = requests.post(
        f"https://api.github.com/repos/{full_name}/git/trees",
        headers=HEADERS,
        json={"tree": []},
    )
    r.raise_for_status()
    tree_sha = r.json()["sha"]

    # 2. Root commit with no parents
    r = requests.post(
        f"https://api.github.com/repos/{full_name}/git/commits",
        headers=HEADERS,
        json={"message": "root", "tree": tree_sha, "parents": []},
    )
    r.raise_for_status()
    commit_sha = r.json()["sha"]

    # 3. Create main branch pointing at it
    r = requests.post(
        f"https://api.github.com/repos/{full_name}/git/refs",
        headers=HEADERS,
        json={"ref": "refs/heads/main", "sha": commit_sha},
    )
    r.raise_for_status()

    return commit_sha


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


def _create_tree(full_name: str, base_tree_sha: str, blobs: list[dict]) -> str:
    """Create a git tree built on top of base_tree_sha."""
    url = f"https://api.github.com/repos/{full_name}/git/trees"
    tree = [
        {"path": b["path"], "mode": "100644", "type": "blob", "sha": b["blob_sha"]}
        for b in blobs
    ]
    resp = requests.post(url, headers=HEADERS, json={"tree": tree, "base_tree": base_tree_sha})
    resp.raise_for_status()
    return resp.json()["sha"]


def _create_commit(full_name: str, message: str, tree_sha: str, parent_sha: str) -> str:
    """Create a commit object and return its SHA."""
    url = f"https://api.github.com/repos/{full_name}/git/commits"
    resp = requests.post(url, headers=HEADERS, json={
        "message": message,
        "tree": tree_sha,
        "parents": [parent_sha],
    })
    resp.raise_for_status()
    return resp.json()["sha"]


def _update_ref(full_name: str, commit_sha: str):
    """Advance refs/heads/main to commit_sha."""
    url = f"https://api.github.com/repos/{full_name}/git/refs/heads/main"
    resp = requests.patch(url, headers=HEADERS, json={"sha": commit_sha, "force": True})
    resp.raise_for_status()


def _push_batch(full_name: str, batch: dict[str, str], message: str, parent_sha: str) -> str:
    """
    Push a batch of files as a single atomic commit using the Git Tree API.
    Returns the new commit SHA to use as parent for the next batch.
    parent_sha is always a valid SHA we created — never None.
    """
    # 1. Upload blobs
    blobs = []
    for path, content in batch.items():
        if not isinstance(content, str):
            content = str(content)
        blob_sha = _create_blob(full_name, content)
        blobs.append({"path": path, "blob_sha": blob_sha})
        print(f"  ✓ {path}")

    # 2. Fetch parent's tree SHA to use as base
    r = requests.get(
        f"https://api.github.com/repos/{full_name}/git/commits/{parent_sha}",
        headers=HEADERS,
    )
    r.raise_for_status()
    base_tree_sha = r.json()["tree"]["sha"]

    # 3. Build tree, commit, advance ref
    tree_sha = _create_tree(full_name, base_tree_sha, blobs)
    commit_sha = _create_commit(full_name, message, tree_sha, parent_sha)
    _update_ref(full_name, commit_sha)

    return commit_sha


# ── Main push entry point ─────────────────────────────────────────────────────

def push_project(project: dict) -> str:
    full_name, parent_sha = create_repo(
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

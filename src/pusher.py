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

_MIT_TEMPLATE = """MIT License

Copyright (c) {year} {username}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

_CONTRIBUTING_TEMPLATE = """# Contributing to {repo_name}

Thank you for taking the time to contribute!

## How to Report Bugs

- Check the [existing issues]({repo_url}/issues) before opening a new one.
- Clearly describe the problem and include steps to reproduce it.
- Include your OS, runtime version, and any relevant logs.

## Making Pull Requests

1. Fork the repository and create your branch from `main`.
2. Install dependencies and verify the test suite passes locally.
3. Write tests for any new behaviour you introduce.
4. Ensure your code follows the existing style (see linting config).
5. Write clear, descriptive commit messages.
6. Open a pull request targeting `main` and describe your changes.

## Good First Issues

Issues labelled [`good first issue`]({repo_url}/issues?q=label%3A%22good+first+issue%22) are a great place to start.
They are self-contained and well-scoped for new contributors.

## Development Setup

```bash
git clone {repo_url}.git
cd {repo_name}
```

Install dependencies per the README, then run the test suite to confirm everything works before making changes.
"""

_BUG_REPORT_TEMPLATE = """---
name: Bug report
about: Report a reproducible bug
title: '[Bug] '
labels: bug
assignees: ''
---

## Describe the bug
A clear and concise description of what the bug is.

## Steps to reproduce
1. ...
2. ...
3. ...

## Expected behaviour
What you expected to happen.

## Actual behaviour
What actually happened.

## Environment
- OS:
- Runtime version:
- Package version:

## Additional context
Any other context, logs, or screenshots.
"""

_FEATURE_REQUEST_TEMPLATE = """---
name: Feature request
about: Suggest a new feature or improvement
title: '[Feature] '
labels: enhancement
assignees: ''
---

## Problem
Describe the problem or limitation this feature would solve.

## Proposed solution
Describe what you would like to happen.

## Alternatives considered
Any alternative approaches you have thought about.

## Additional context
Any other context, mockups, or examples.
"""


def _mit_license() -> str:
    return _MIT_TEMPLATE.format(
        year=datetime.utcnow().year,
        username=GITHUB_USERNAME,
    )


def _contributing(repo_name: str) -> str:
    repo_url = f"https://github.com/{GITHUB_USERNAME}/{repo_name}"
    return _CONTRIBUTING_TEMPLATE.format(repo_name=repo_name, repo_url=repo_url)


# ── Identity check ─────────────────────────────────────────────────────────

def _verify_authenticated_user():
    r = requests.get("https://api.github.com/user", headers=HEADERS)
    r.raise_for_status()
    actual = r.json()["login"]
    if actual.lower() != GITHUB_USERNAME.lower():
        raise RuntimeError(
            f"[pusher] PAT_TOKEN belongs to '{actual}', not GH_USERNAME="
            f"'{GITHUB_USERNAME}'. Refusing to push to avoid hitting the wrong account."
        )


def _check_workflow_scope(files: dict):
    if not any(p.startswith(".github/workflows/") for p in files):
        return
    r = requests.get("https://api.github.com/user", headers=HEADERS)
    r.raise_for_status()
    scopes_header = r.headers.get("X-OAuth-Scopes")
    if scopes_header is None:
        return
    scopes = {s.strip() for s in scopes_header.split(",") if s.strip()}
    if "workflow" not in scopes:
        raise RuntimeError(
            "[pusher] PAT_TOKEN doesn't have the 'workflow' scope. "
            f"Current scopes: {scopes_header or '(none)'}. "
            "Fix: GitHub → Settings → Developer settings → Personal access tokens "
            "→ edit token → check 'workflow' → regenerate → update PAT_TOKEN secret."
        )


def preflight_check():
    _verify_authenticated_user()
    _check_workflow_scope({".github/workflows/ci.yml": ""})


# ── Repo creation ─────────────────────────────────────────────────────────────

def create_repo(name: str, description: str, topics: list[str]) -> tuple[str, str]:
    url = "https://api.github.com/user/repos"
    payload = {
        "name": name,
        "description": description,
        "private": False,
        "auto_init": True,
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

    init_sha = _wait_for_init_commit(full_name)
    return full_name, init_sha


def _wait_for_init_commit(full_name: str) -> str:
    for attempt in range(12):
        r = requests.get(
            f"https://api.github.com/repos/{full_name}/git/refs/heads/main",
            headers=HEADERS,
        )
        if r.status_code == 200:
            sha = r.json()["object"]["sha"]
            print(f"[pusher] git store ready (init commit: {sha[:7]})")
            return sha
        wait = 3 * (attempt + 1)
        print(f"[pusher] waiting for git store... {wait}s (attempt {attempt + 1}/12)")
        time.sleep(wait)
    raise RuntimeError(f"[pusher] git store never became ready for {full_name}")


def _set_topics(full_name: str, topics: list[str]):
    url = f"https://api.github.com/repos/{full_name}/topics"
    clean = [t.lower().replace(" ", "-")[:50] for t in topics[:20]]
    requests.put(url, headers=HEADERS, json={"names": clean})


# ── Git Tree API helpers ──────────────────────────────────────────────────────

def _create_blob(full_name: str, content: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/git/blobs"
    payload = {
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "encoding": "base64",
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()["sha"]


def _create_tree(full_name: str, base_tree_sha: str, blobs: list[dict]) -> str:
    url = f"https://api.github.com/repos/{full_name}/git/trees"
    tree = [
        {"path": b["path"], "mode": "100644", "type": "blob", "sha": b["blob_sha"]}
        for b in blobs
    ]
    resp = requests.post(url, headers=HEADERS, json={
        "tree": tree,
        "base_tree": base_tree_sha,
    })
    resp.raise_for_status()
    return resp.json()["sha"]


def _create_commit(full_name: str, message: str, tree_sha: str, parent_sha: str) -> str:
    url = f"https://api.github.com/repos/{full_name}/git/commits"
    resp = requests.post(url, headers=HEADERS, json={
        "message": message,
        "tree": tree_sha,
        "parents": [parent_sha],
    })
    resp.raise_for_status()
    return resp.json()["sha"]


def _update_ref(full_name: str, commit_sha: str):
    url = f"https://api.github.com/repos/{full_name}/git/refs/heads/main"
    resp = requests.patch(url, headers=HEADERS, json={"sha": commit_sha, "force": True})
    resp.raise_for_status()


def _get_commit_tree(full_name: str, commit_sha: str) -> str:
    for attempt in range(6):
        r = requests.get(
            f"https://api.github.com/repos/{full_name}/git/commits/{commit_sha}",
            headers=HEADERS,
        )
        if r.status_code == 200:
            return r.json()["tree"]["sha"]
        time.sleep(3 * (attempt + 1))
    r.raise_for_status()


def _push_batch(full_name: str, batch: dict[str, str], message: str, parent_sha: str) -> str:
    blobs = []
    for path, content in batch.items():
        if not isinstance(content, str):
            content = str(content)
        blob_sha = _create_blob(full_name, content)
        blobs.append({"path": path, "blob_sha": blob_sha})
        print(f"  ✓ {path}")

    base_tree_sha = _get_commit_tree(full_name, parent_sha)
    tree_sha = _create_tree(full_name, base_tree_sha, blobs)
    commit_sha = _create_commit(full_name, message, tree_sha, parent_sha)
    _update_ref(full_name, commit_sha)
    return commit_sha


# ── Main push entry point ─────────────────────────────────────────────────────

def push_project(project: dict) -> str:
    _verify_authenticated_user()
    _check_workflow_scope(project.get("files", {}))

    full_name, parent_sha = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    repo_name = project["repo_name"]
    readme = project.get("readme") or f"# {repo_name}\n"
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

    # Initial commit: README + LICENSE + CONTRIBUTING.md + issue templates + CI
    # All of these go in the first commit so the repo looks complete from day one
    initial_batch = {
        "README.md": readme,
        "LICENSE": _mit_license(),
        "CONTRIBUTING.md": _contributing(repo_name),
        ".github/ISSUE_TEMPLATE/bug_report.md": _BUG_REPORT_TEMPLATE,
        ".github/ISSUE_TEMPLATE/feature_request.md": _FEATURE_REQUEST_TEMPLATE,
        **github_files,
    }

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
    # Also treat direct src/ files as core (e.g. src/catalog.ts, src/types.ts)
    # so they land in 'feat: implement core engine' not 'feat: add remaining modules'
    is_direct_src = len(parts) == 2 and parts[0] == "src"
    return (any(p in keywords for p in parts) or is_direct_src) and not _is_test(path)

def _is_support(path: str) -> bool:
    parts = path.lower().split("/")
    keywords = {
        "util", "utils", "helper", "helpers", "common", "shared",
        "plugin", "plugins", "agent", "agents", "cli", "cmd", "bin",
        "api", "route", "routes", "server", "handler", "handlers",
        "model", "models", "schema", "schemas", "config", "logger",
        "state", "store", "service", "services", "skill", "skills",
        "runner", "runners", "step", "steps", "tool", "tools", "lib",
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

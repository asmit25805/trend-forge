"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups to look like real development history.
"""

import os
import time
import base64
import requests
from datetime import datetime, timedelta
import random

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_USERNAME = os.environ["GH_USERNAME"]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# How files get grouped into commit batches — ordered to look like real dev flow
COMMIT_GROUPS = [
    {
        "match": lambda p: p in ("README.md",),
        "message": "Initial commit",
    },
    {
        "match": lambda p: any(p.endswith(f) for f in [
            "requirements.txt", "package.json", "go.mod", "Cargo.toml",
            "pyproject.toml", ".gitignore", ".env.example", "Makefile",
        ]),
        "message": "chore: project setup and dependencies",
    },
    {
        "match": lambda p: p.startswith(".github/"),
        "message": "ci: add GitHub Actions workflow",
    },
    {
        "match": lambda p: any(seg in p for seg in ["core", "engine", "pipeline", "runtime"]),
        "message": "feat: implement core engine",
    },
    {
        "match": lambda p: any(seg in p for seg in ["util", "helper", "common", "shared"]),
        "message": "feat: add utility modules",
    },
    {
        "match": lambda p: any(seg in p for seg in ["cli", "cmd", "command"]),
        "message": "feat: add CLI interface",
    },
    {
        "match": lambda p: any(seg in p for seg in ["api", "route", "server", "handler"]),
        "message": "feat: add API layer",
    },
    {
        "match": lambda p: any(seg in p for seg in ["model", "schema", "type"]),
        "message": "feat: define data models",
    },
    {
        "match": lambda p: any(seg in p for seg in ["config", "setting"]),
        "message": "feat: add configuration handling",
    },
    {
        "match": lambda p: any(seg in p for seg in ["test", "spec"]),
        "message": "test: add test suite",
    },
    {
        "match": lambda p: any(seg in p for seg in ["doc", "example", "demo", "notebook"]),
        "message": "docs: add examples and documentation",
    },
]

FALLBACK_GROUP = {"message": "feat: add remaining modules"}


def _group_files(files: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """
    Group files into ordered commit batches based on their path.
    Each group gets one commit message. Files not matching any group
    fall into a final catch-all commit.
    """
    assigned = set()
    groups: list[tuple[str, list]] = []

    for group in COMMIT_GROUPS:
        matched = [
            (path, content)
            for path, content in files.items()
            if group["match"](path) and path not in assigned
        ]
        if matched:
            for path, _ in matched:
                assigned.add(path)
            groups.append((group["message"], matched))

    # Catch-all for anything unmatched
    remaining = [(p, c) for p, c in files.items() if p not in assigned]
    if remaining:
        groups.append((FALLBACK_GROUP["message"], remaining))

    return groups


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
        suffix = datetime.utcnow().strftime("%m%d")
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
    """Push a single file. Handles create and update."""
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": encoded, "branch": "main"}

    existing = requests.get(url, headers=HEADERS)
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]

    resp = requests.put(url, headers=HEADERS, json=payload)
    resp.raise_for_status()


def push_project(project: dict) -> str:
    """
    Create the repo and push all files in grouped commits
    so the history looks like real incremental development.
    """
    full_name = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    # Build the full file dict: README first, then everything else
    readme = project.get("readme", f"# {project['repo_name']}\n")
    all_files = {"README.md": readme, **project.get("files", {})}

    # Group into logical commits
    groups = _group_files(all_files)
    total_files = sum(len(g[1]) for g in groups)
    print(f"[pusher] Pushing {total_files} files across {len(groups)} commits...")

    pushed = 0
    for commit_msg, file_batch in groups:
        print(f"\n[pusher] Commit: '{commit_msg}' ({len(file_batch)} file(s))")
        for path, content in file_batch:
            if not isinstance(content, str):
                content = str(content)
            _push_file(full_name, path, content, commit_msg)
            print(f"  ✓ {path}")
            pushed += 1
            time.sleep(0.5)  # avoid GitHub secondary rate limit

        # Small pause between commit groups — looks more natural too
        time.sleep(1.0)

    repo_url = f"https://github.com/{full_name}"
    print(f"\n[pusher] ✅ {pushed} files pushed across {len(groups)} commits")
    print(f"[pusher] Live at: {repo_url}")
    return repo_url

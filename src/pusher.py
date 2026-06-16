"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups to look like real development history.
Handles 404s on nested paths (GitHub needs a moment after repo creation)
and 429 rate limits with exponential backoff.
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

COMMIT_GROUPS = [
    {
        "match": lambda p: p == "README.md",
        "message": "Initial commit",
    },
    {
        "match": lambda p: any(p.endswith(f) for f in [
            "requirements.txt", "package.json", "go.mod", "Cargo.toml",
            "pyproject.toml", ".gitignore", ".env.example", "Makefile",
            "tsconfig.json", "LICENSE",
        ]),
        "message": "chore: project setup and dependencies",
    },
    {
        "match": lambda p: p.startswith(".github/"),
        "message": "ci: add GitHub Actions workflow",
    },
    {
        "match": lambda p: any(seg in p for seg in ["core", "engine", "pipeline", "runtime", "bootstrap"]),
        "message": "feat: implement core engine",
    },
    {
        "match": lambda p: any(seg in p for seg in ["util", "helper", "common", "shared", "logger", "environment"]),
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
        "match": lambda p: any(seg in p for seg in ["plugin", "agent"]),
        "message": "feat: add plugin system",
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


def _group_files(files: dict) -> list[tuple[str, list]]:
    assigned = set()
    groups = []

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

    remaining = [(p, c) for p, c in files.items() if p not in assigned]
    if remaining:
        groups.append((FALLBACK_GROUP["message"], remaining))

    return groups


def _push_file_with_retry(full_name: str, path: str, content: str, message: str, max_retries: int = 6):
    """
    Push a single file with retry on both 429 (rate limit) and 404
    (GitHub sometimes needs a moment after repo creation for nested paths).
    """
    url = f"https://api.github.com/repos/{full_name}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {"message": message, "content": encoded, "branch": "main"}

    existing = requests.get(url, headers=HEADERS)
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]

    for attempt in range(max_retries):
        resp = requests.put(url, headers=HEADERS, json=payload)

        if resp.status_code in (200, 201):
            return  # success

        if resp.status_code == 429:
            wait = (attempt + 1) * 10
            print(f"  [rate limit] waiting {wait}s (attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait)
            continue

        if resp.status_code == 404 and attempt < max_retries - 1:
            # GitHub repo/branch not fully ready yet — wait and retry
            wait = (attempt + 1) * 5
            print(f"  [404 on {path}] repo not ready yet, waiting {wait}s...")
            time.sleep(wait)
            continue

        # Any other error — raise immediately
        resp.raise_for_status()

    raise RuntimeError(f"Failed to push {path} after {max_retries} attempts")


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


def push_project(project: dict) -> str:
    full_name = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    readme = project.get("readme", f"# {project['repo_name']}\n")
    all_files = {"README.md": readme, **project.get("files", {})}

    groups = _group_files(all_files)
    total = sum(len(g[1]) for g in groups)
    print(f"[pusher] Pushing {total} files across {len(groups)} commits...")

    # Push README first to create the main branch, then wait for GitHub
    # to fully initialise the repo before pushing nested paths
    print(f"\n[pusher] Commit: 'Initial commit' (1 file)")
    _push_file_with_retry(full_name, "README.md", readme, "Initial commit")
    print(f"  ✓ README.md")
    print(f"[pusher] Waiting for repo to initialise...")
    time.sleep(5)  # give GitHub a moment before pushing nested paths

    pushed = 1
    for commit_msg, file_batch in groups:
        # Skip README — already pushed above
        batch = [(p, c) for p, c in file_batch if p != "README.md"]
        if not batch:
            continue

        print(f"\n[pusher] Commit: '{commit_msg}' ({len(batch)} file(s))")
        for path, content in batch:
            if not isinstance(content, str):
                content = str(content)
            _push_file_with_retry(full_name, path, content, commit_msg)
            print(f"  ✓ {path}")
            pushed += 1
            time.sleep(1)  # steady pace between files

        time.sleep(2)  # pause between commit groups

    repo_url = f"https://github.com/{full_name}"
    print(f"\n[pusher] ✅ {pushed} files pushed across {len(groups)} commits")
    print(f"[pusher] Live at: {repo_url}")
    return repo_url

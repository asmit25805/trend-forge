"""
Creates a new GitHub repo and pushes all generated files.
Spreads commits across logical groups (setup / core / support / tests / docs)
so the resulting history reads as a normal incremental build rather than a
single dump commit.

Uses the Git Tree API to push each batch as a single atomic commit, which
avoids the race condition where the Contents API 404s on nested paths
(e.g. .github/workflows/) because the branch isn't indexed yet.

Usage:
    export PAT_TOKEN=ghp_xxx
    export GH_USERNAME=your-username
    python push_to_github.py ./my_generated_project my-repo-name \
        --description "Short description" --topics ai cli automation
"""

import os
import sys
import time
import base64
import argparse
import requests
from datetime import datetime

GITHUB_TOKEN = os.environ["PAT_TOKEN"]
GITHUB_USERNAME = os.environ["GH_USERNAME"]

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ── Identity check ─────────────────────────────────────────────────────────

def _verify_authenticated_user():
    """
    Confirm the PAT actually belongs to GH_USERNAME before creating or
    touching any repo. Prevents pushing to the wrong account on a stale
    or misconfigured token.
    """
    r = requests.get("https://api.github.com/user", headers=HEADERS)
    r.raise_for_status()
    actual = r.json()["login"]
    if actual.lower() != GITHUB_USERNAME.lower():
        raise RuntimeError(
            f"[pusher] PAT_TOKEN belongs to '{actual}', not GH_USERNAME="
            f"'{GITHUB_USERNAME}'. Refusing to push to avoid hitting the "
            "wrong account."
        )


# ── Repo creation ─────────────────────────────────────────────────────────────

def create_repo(name: str, description: str, topics: list[str]) -> tuple[str, str]:
    """
    Creates a repo with auto_init=True (provisions git DB immediately),
    waits for the initial commit to be readable, and returns
    (full_name, init_commit_sha) to use as parent for the first batch.
    """
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

    # Poll until the auto-init commit is readable — this is the only reliable
    # way to know the git DB is fully provisioned and safe to write to.
    init_sha = _wait_for_init_commit(full_name)
    return full_name, init_sha


def _wait_for_init_commit(full_name: str) -> str:
    """Poll refs/heads/main until the auto-init commit SHA is available."""
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
    """Force-advance refs/heads/main to commit_sha."""
    url = f"https://api.github.com/repos/{full_name}/git/refs/heads/main"
    resp = requests.patch(url, headers=HEADERS, json={"sha": commit_sha, "force": True})
    resp.raise_for_status()


def _get_commit_tree(full_name: str, commit_sha: str) -> str:
    """Return the tree SHA of a given commit, with retry for propagation lag."""
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
    """
    Push a batch of files as a single atomic commit using the Git Tree API.
    Returns the new commit SHA to use as parent for the next batch.
    parent_sha is always a valid SHA — seeded from the auto-init commit.
    """
    # 1. Upload blobs
    blobs = []
    for path, content in batch.items():
        if not isinstance(content, str):
            content = str(content)
        blob_sha = _create_blob(full_name, content)
        blobs.append({"path": path, "blob_sha": blob_sha})
        print(f"  ✓ {path}")

    # 2. Fetch parent commit's tree SHA to use as base
    base_tree_sha = _get_commit_tree(full_name, parent_sha)

    # 3. Build tree, commit, advance ref
    tree_sha = _create_tree(full_name, base_tree_sha, blobs)
    commit_sha = _create_commit(full_name, message, tree_sha, parent_sha)
    _update_ref(full_name, commit_sha)

    return commit_sha


# ── Local file loading ─────────────────────────────────────────────────────

DEFAULT_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    ".idea", ".vscode", "dist", "build", ".pytest_cache", ".mypy_cache",
}


def _load_files_from_dir(root_dir: str, exclude_dirs: set[str] | None = None) -> dict[str, str]:
    """
    Walk root_dir and return {relative_path: content} for every text file.
    Skips VCS/dependency/cache directories and any unreadable binary files
    (printing a notice rather than failing the whole run).
    """
    exclude_dirs = exclude_dirs or DEFAULT_EXCLUDE_DIRS
    files: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [
            d for d in dirnames
            if d not in exclude_dirs and not d.startswith(".")
        ]
        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(abs_path, root_dir).replace(os.sep, "/")
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    files[rel_path] = f.read()
            except (UnicodeDecodeError, PermissionError) as e:
                print(f"[pusher] skipping unreadable file {rel_path}: {e}")
                continue

    return files


# ── Main push entry point ─────────────────────────────────────────────────────

def push_project(project: dict) -> str:
    _verify_authenticated_user()

    full_name, parent_sha = create_repo(
        project["repo_name"],
        project.get("description", ""),
        project.get("topics", []),
    )

    # .get("readme") (no default arg) so an explicit None falls through to
    # the f-string default instead of writing the literal string "None".
    readme = project.get("readme") or f"# {project['repo_name']}\n"
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


# ── CLI entry point ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Create a GitHub repo and push a local project directory "
                     "as a series of logically-grouped commits."
    )
    parser.add_argument("directory", help="Local path containing the project files to push")
    parser.add_argument("repo_name", help="Name for the new GitHub repository")
    parser.add_argument("--description", default="", help="Repo description")
    parser.add_argument("--topics", nargs="*", default=[], help="Repo topics/tags")
    parser.add_argument(
        "--readme",
        default=None,
        help="Path to a README file to use instead of any README.md found in `directory`",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"[pusher] directory not found: {args.directory}")

    files = _load_files_from_dir(args.directory)
    if not files:
        sys.exit(f"[pusher] no readable files found under {args.directory}")

    readme_content = None
    if args.readme:
        with open(args.readme, "r", encoding="utf-8") as f:
            readme_content = f.read()
    elif "README.md" in files:
        readme_content = files.pop("README.md")

    project = {
        "repo_name": args.repo_name,
        "description": args.description,
        "topics": args.topics,
        "readme": readme_content,
        "files": files,
    }

    push_project(project)


if __name__ == "__main__":
    main()

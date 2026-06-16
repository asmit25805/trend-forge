"""
TrendForge — Main pipeline orchestrator.
Runs the full cycle: find trending repo → analyze → generate → push.
"""

import sys
import json
from datetime import datetime
from pathlib import Path

from trending import pick_unseen_repo, load_seen, save_seen
from analyzer import analyze_repo
from generator import generate_project
from pusher import push_project

LOG_FILE = Path(__file__).parent.parent / "run_log.json"


def load_log() -> list:
    if LOG_FILE.exists():
        return json.loads(LOG_FILE.read_text())
    return []


def save_log(log: list):
    LOG_FILE.write_text(json.dumps(log, indent=2))


def run():
    print(f"\n{'='*60}")
    print(f"TrendForge — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # 1. Load state (seen repos + last category index)
    state = load_seen()
    seen_count = len(state.get("repos", []))
    last_cat_idx = state.get("last_category_index", -1)
    from trending import CATEGORIES
    last_cat = CATEGORIES[last_cat_idx] if last_cat_idx >= 0 else "none"
    print(f"[main] {seen_count} repos processed so far. Last category: {last_cat}\n")

    # 2. Find unseen trending repo (round-robin by category)
    repo, new_cat_idx = pick_unseen_repo(state)
    if not repo:
        print("[main] No new unseen trending repos found today. Try again tomorrow.")
        sys.exit(0)

    from trending import CATEGORIES as CATS
    print(f"[main] Selected: {repo['full_name']} ⭐{repo['stars']} (category: {CATS[new_cat_idx]})")
    print(f"[main] URL: {repo['html_url']}\n")

    # 3. Analyze with Cerebras
    print("[main] Analyzing with Cerebras...")
    analysis = analyze_repo(repo)
    print(f"[main] Concept: {analysis['concept']}")
    print(f"[main] Why trending: {analysis['why_trending']}")
    print(f"[main] Angle: {analysis['inspiration_angle']}\n")

    # 4. Generate original project (3-phase)
    print("[main] Generating project...")
    project = generate_project(analysis)
    print(f"[main] Project name: {project['repo_name']}")
    print(f"[main] Files: {list(project['files'].keys())}\n")

    # 5. Push to GitHub
    print("[main] Pushing to GitHub...")
    repo_url = push_project(project)

    # 6. Update state — mark repo seen + advance category index
    state["repos"] = list(set(state.get("repos", [])) | {repo["full_name"]})
    state["last_category_index"] = new_cat_idx
    save_seen(state)

    # 7. Log the run
    log = load_log()
    log.append({
        "date": datetime.utcnow().isoformat(),
        "category": CATS[new_cat_idx],
        "source_repo": repo["full_name"],
        "source_stars": repo["stars"],
        "generated_repo": project["repo_name"],
        "generated_url": repo_url,
        "concept": analysis["concept"],
        "inspiration_angle": analysis["inspiration_angle"],
    })
    save_log(log)

    print(f"\n{'='*60}")
    print(f"✅ Done! {repo_url}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run()

"""
Four-phase project generator using Cerebras.

Phase 0: Design doc — API contracts, data models, module interfaces
Phase 1: Plan       — file tree informed by design doc + source code
Phase 2: Generate   — each file written against the spec
Phase 3: Validate   — static consistency check

No AI watermarks. Comments read like a specific human developer wrote them.
"""

import os
import json
import time
from cerebras.cloud.sdk import Cerebras

client = Cerebras(
    api_key=os.environ["CEREBRAS_API_KEY"],
    timeout=60,
    max_retries=0,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _call(messages: list, max_tokens: int = 2048, temperature: float = 0.5) -> str:
    for attempt in range(4):
        try:
            resp = client.chat.completions.create(
                model="gpt-oss-120b",
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e).lower()
            retryable = any(x in err for x in (
                "429", "too_many_requests", "queue_exceeded", "timeout", "timed out"
            ))
            if retryable and attempt < 3:
                wait = 15 * (attempt + 1)
                print(f"  [rate limit] waiting {wait}s (attempt {attempt+1}/4)...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Cerebras: max retries exceeded")


def _repair_json(raw: str) -> str:
    for closing in ("}", "]"):
        idx = raw.rfind(closing)
        if idx != -1:
            candidate = raw[:idx + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return raw


def _parse_json(raw: str) -> dict | list:
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_repair_json(raw))


def _source_context_block(analysis: dict) -> str:
    ctx = analysis.get("_source_context", {})
    if not ctx:
        return ""
    block = "REFERENCE SOURCE CODE (study patterns, never copy verbatim):\n"
    block += f"\nDirectory structure:\n{ctx.get('tree_summary', '')[:600]}\n"
    for path, content in ctx.get("source_files", {}).items():
        block += f"\n--- {path} ---\n{content[:1000]}\n"
    return block


# ── Category style profiles ───────────────────────────────────────────────────

CATEGORY_PROFILES = {
    "cli": {
        "language_hint": "Python or Go",
        "structure_hint": "cmd/ for subcommands, internal/ for logic, pkg/ for reusable parts",
        "quality_bar": "Sub-100ms startup. Shell completions. --help is comprehensive. Error messages tell you exactly what to fix.",
        "style": "Unix philosophy. Composable. Every flag has a short form.",
        "code_patterns": "argparse/cobra subcommands, os.path.expanduser for config, rich/lipgloss for output",
    },
    "ai": {
        "language_hint": "Python",
        "structure_hint": "src/models/, src/pipelines/, src/utils/, examples/",
        "quality_bar": "Type-annotated throughout. Async-first where latency matters. Works without GPU.",
        "style": "Research-grade but ships. Real retry logic, real timeouts, real error messages.",
        "code_patterns": "pydantic BaseSettings, loguru/structlog, httpx for async HTTP, ABC for base classes",
    },
    "llm": {
        "language_hint": "Python",
        "structure_hint": "src/agents/, src/prompts/, src/memory/, src/tools/",
        "quality_bar": "Agent loop handles failures gracefully. Token counting is real. Memory persists.",
        "style": "Production agent patterns. State is explicit. Every tool call is logged.",
        "code_patterns": "ABC for agent base, @dataclass for tool schemas, sqlite3 for memory",
    },
    "agent": {
        "language_hint": "Python or TypeScript",
        "structure_hint": "src/agents/, src/tools/, src/memory/, src/runtime/",
        "quality_bar": "Plan/act/observe loop is inspectable. Tools declare their schema. Human checkpoints are real.",
        "style": "Debuggable by design. Every agent decision is logged with reasoning.",
        "code_patterns": "event emitter for agent lifecycle, json schema for tool validation, sqlite for persistence",
    },
    "web": {
        "language_hint": "TypeScript with Next.js or Python with FastAPI",
        "structure_hint": "app/ with routing, components/, lib/, api/",
        "quality_bar": "No hydration errors. Loading states everywhere. Error boundaries that recover.",
        "style": "Production patterns. Real optimistic updates. Accessible by default.",
        "code_patterns": "zod for validation, React Query/SWR for data, Tailwind for styling",
    },
    "api": {
        "language_hint": "Python (FastAPI) or TypeScript (Hono/Express)",
        "structure_hint": "src/routes/, src/middleware/, src/models/, src/services/",
        "quality_bar": "Every endpoint has a schema. 4xx errors have actionable messages. Health check endpoint exists.",
        "style": "REST done right. Idempotent where possible. Pagination on all list endpoints.",
        "code_patterns": "pydantic/zod for validation, dependency injection, middleware chain",
    },
    "developer-tools": {
        "language_hint": "Python, TypeScript, or Go",
        "structure_hint": "src/core/, src/plugins/, src/reporters/, bin/",
        "quality_bar": "Zero config to get a useful result. Errors point to the exact line.",
        "style": "Opinionated defaults, escapable via config.",
        "code_patterns": "plugin registry with hooks, config file discovery walking up dirs, multiple output formats",
    },
    "automation": {
        "language_hint": "Python",
        "structure_hint": "src/tasks/, src/triggers/, src/integrations/, src/scheduler/",
        "quality_bar": "Tasks are idempotent. Every run is logged with duration and result. Dry-run always available.",
        "style": "Reliability over cleverness. Every side effect is logged before it happens.",
        "code_patterns": "dataclass for task definition, threading for concurrent tasks, sqlite for run history",
    },
}

DEFAULT_PROFILE = {
    "language_hint": "Python or TypeScript",
    "structure_hint": "src/ for logic, tests/ for tests, docs/ for docs",
    "quality_bar": "Type-annotated. Real error handling. Comprehensive README.",
    "style": "Clean, idiomatic, production-quality.",
    "code_patterns": "standard library first, then well-known packages",
}


def get_profile(category: str) -> dict:
    return CATEGORY_PROFILES.get(category, DEFAULT_PROFILE)


# ── Phase 0: Design document ──────────────────────────────────────────────────

DESIGN_SYSTEM = """You are a principal engineer designing a new open-source project.
Your design documents are specific, concrete, and implementable.
Respond ONLY with valid JSON."""


def design_project(analysis: dict) -> dict:
    profile = get_profile(analysis["category"])
    source_block = _source_context_block(analysis)
    gh_user = os.environ.get("GH_USERNAME", "")

    prompt = f"""Design a new open-source project inspired by this trending repo.

INSPIRATION: {analysis['source_repo']} (⭐{analysis['source_stars']})
What it does: {analysis['concept']}
Why it's trending: {analysis['why_trending']}
Architecture: {analysis.get('architecture_pattern', 'N/A')}
Key insights from its code: {', '.join(analysis.get('key_implementation_insights', []))}
Your original angle: {analysis['inspiration_angle']}
Category: {analysis['category']}
GitHub user who will own this: {gh_user}

{source_block}

QUALITY BAR: {profile['quality_bar']}
CODE PATTERNS: {profile['code_patterns']}

Design an ORIGINAL project (not a clone). Think: what would a senior engineer build
over a weekend that solves a related problem in a different way?

Return ONLY this JSON:
{{
  "project_name": "kebab-case",
  "tagline": "one sharp sentence — what it does and why it matters",
  "language": "primary language",
  "github_user": "{gh_user}",
  "core_abstractions": [
    {{
      "name": "ClassName or module_name",
      "role": "what this abstraction does",
      "key_methods": ["method_name(args) -> return_type: what it does"]
    }}
  ],
  "data_models": [
    {{
      "name": "ModelName",
      "fields": ["field_name: type — what it represents"],
      "used_by": ["which modules"]
    }}
  ],
  "module_interfaces": [
    {{
      "file": "src/core/engine.py",
      "exports": ["ClassName", "function_name"],
      "imports_from": ["other/module.py"],
      "key_logic": "2-3 sentences on what the non-obvious logic does"
    }}
  ],
  "data_flow": "step-by-step: input → processing → output",
  "error_handling_strategy": "how errors propagate and surface to the user",
  "key_design_decisions": ["specific decision and the concrete reason why"]
}}"""

    print("[generator] Phase 0: Designing architecture...")
    raw = _call(
        [{"role": "system", "content": DESIGN_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.5,
    )
    return _parse_json(raw)


# ── Phase 1: File tree plan ───────────────────────────────────────────────────

PLAN_SYSTEM = """You are a senior engineer turning a design document into a file tree.
Every file must be justified by the design doc.
Respond ONLY with valid JSON."""


def plan_project(analysis: dict, design: dict) -> dict:
    profile = get_profile(analysis["category"])
    gh_user = design.get("github_user") or os.environ.get("GH_USERNAME", "")

    abstractions = "\n".join(
        f"  - {a['name']}: {a['role']}" for a in design.get("core_abstractions", [])
    )
    modules = "\n".join(
        f"  - {m['file']}: exports {m['exports']}" for m in design.get("module_interfaces", [])
    )

    prompt = f"""Turn this design into a concrete file tree.

PROJECT: {design['project_name']}
GitHub: github.com/{gh_user}/{design['project_name']}
Tagline: {design['tagline']}
Language: {design['language']}

CORE ABSTRACTIONS:
{abstractions}

MODULE INTERFACES (these files MUST exist):
{modules}

Data flow: {design['data_flow']}

CATEGORY: {analysis['category']}
Structure: {profile['structure_hint']}

RULES:
- Every file in module_interfaces must appear in the file tree
- Also include: README.md, .github/workflows/ci.yml, dependency file, 2 test files, 1 example
- 10-14 files total
- "purpose" must name specific classes/functions from the design doc

Return ONLY this JSON:
{{
  "repo_name": "{design['project_name']}",
  "description": "{design['tagline']}",
  "language": "{design['language']}",
  "topics": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "file_tree": [
    {{"path": "README.md", "purpose": "specific to this project"}},
    {{"path": "src/core/engine.py", "purpose": "implements EngineClass — specific detail"}}
  ]
}}"""

    raw = _call(
        [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.4,
    )
    return _parse_json(raw)


# ── Phase 2: File generation ──────────────────────────────────────────────────

FILE_SYSTEM = """You are writing code for a real open-source project.
You write like a senior engineer — not verbose, not terse, just right.
Real logic. Real error handling. No filler. No placeholder functions.
You never mention AI, generation, or automation.
Output raw file content only — no markdown fences, no explanation."""


def generate_file(
    path: str,
    purpose: str,
    plan: dict,
    analysis: dict,
    design: dict,
    already_written: dict,
) -> str:
    profile = get_profile(analysis["category"])
    source_block = _source_context_block(analysis)
    gh_user = design.get("github_user") or os.environ.get("GH_USERNAME", "")
    repo_name = plan["repo_name"]

    design_context = f"""TECHNICAL DESIGN:
Project: {design['project_name']} — {design['tagline']}
Language: {design['language']}
Data flow: {design['data_flow']}
Error strategy: {design['error_handling_strategy']}
Core abstractions: {json.dumps(design.get('core_abstractions', []), indent=2)}
Data models: {json.dumps(design.get('data_models', []), indent=2)}
Key decisions: {chr(10).join('- ' + d for d in design.get('key_design_decisions', []))}"""

    written_context = ""
    if already_written:
        written_context = "\nALREADY WRITTEN (match naming and imports exactly):\n"
        for p, content in list(already_written.items())[-4:]:
            preview = content[:800].replace("\n", "\\n")
            written_context += f"\n// {p}\n{preview}...\n"

    prompt = f"""Write this file for project '{repo_name}'.

{design_context}

{source_block}

{written_context}

FILE: {path}
Purpose: {purpose}

QUALITY BAR: {profile['quality_bar']}
STYLE: {profile['style']}
CODE PATTERNS: {profile['code_patterns']}

STRICT RULES — violating any of these makes the output unusable:

1. README.md rules:
   - All URLs must use github.com/{gh_user}/{repo_name} — never "your-org" or "your-username"
   - Install section: pip install {repo_name} (or npm/go equivalent)
   - Quickstart must be copy-paste runnable with real output shown
   - At least 120 lines
   - No generic phrases like "easy to use", "lightweight", "powerful"

2. Test file rules:
   - Import from the ACTUAL module paths in THIS project (not generic examples)
   - Every test must assert real behaviour — no `assert True`, no `or True`, no placeholder assertions
   - At least 5 meaningful test functions per file
   - No comments like "# Import the package under test" or "# placeholder"
   - Test functions must have descriptive names: test_pipeline_retries_on_timeout not test_basic

3. CI yaml rules:
   - Use real action versions (actions/checkout@v4, actions/setup-python@v5)
   - Install deps, lint with ruff/flake8, run pytest — all three steps

4. Source code rules:
   - Implement the FULL logic from the design doc — no pass, no NotImplementedError, no stubs
   - At least 80 lines of real code per source file
   - Type annotations on every function signature
   - Comments only where the logic is genuinely non-obvious — never describe what the next line does

Write the complete file now:"""

    return _call(
        [{"role": "system", "content": FILE_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0.3,
    )


# ── Phase 3: Validation pass ──────────────────────────────────────────────────

VALIDATE_SYSTEM = """You are doing a final review before a repo goes public.
You fix real problems only. Be specific and surgical.
Respond ONLY with valid JSON."""


def validate_and_fix(
    files: dict, readme: str, plan: dict, design: dict, analysis: dict
) -> tuple[dict, str]:
    gh_user = design.get("github_user") or os.environ.get("GH_USERNAME", "")
    repo_name = plan["repo_name"]

    design_summary = f"""Spec:
- Abstractions: {[a['name'] for a in design.get('core_abstractions', [])]}
- Models: {[m['name'] for m in design.get('data_models', [])]}
- Modules: {[m['file'] + ' exports ' + str(m['exports']) for m in design.get('module_interfaces', [])]}
- GitHub: github.com/{gh_user}/{repo_name}"""

    snapshot = f"=== README.md ===\n{readme[:600]}\n"
    for path, content in files.items():
        snapshot += f"\n=== {path} ===\n{content[:600]}\n"

    prompt = f"""Review this project before it goes public. Fix everything you find.

{design_summary}

FILES:
{snapshot}

Find and fix ALL of the following:
1. Any import that references a path or name not in the file tree
2. Function/class called in one file but defined differently in another
3. Stub implementations: pass, raise NotImplementedError, empty body, `or True` assertions
4. Test comments like "# Import the package under test", "# placeholder", "# TODO"
5. Test assertions that always pass (assert True, assert x or True, etc.)
6. README URLs containing "your-org", "your-username" — replace with github.com/{gh_user}/{repo_name}
7. Source files under 40 lines (too short to be real — expand them)
8. Any mention of "generated", "auto-generated", "AI" in comments or docstrings

Return ONLY this JSON:
{{
  "fixes": [
    {{
      "path": "src/core/engine.py",
      "issue": "one-line description of the actual problem",
      "fixed_content": "complete corrected file — the whole thing, not a diff"
    }}
  ]
}}"""

    try:
        raw = _call(
            [{"role": "system", "content": VALIDATE_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.2,
        )
        result = _parse_json(raw)
        fixes = result.get("fixes", [])

        if not fixes:
            print("[validator] ✓ No issues found")
            return files, readme

        print(f"[validator] {len(fixes)} fix(es) applied:")
        for fix in fixes:
            path = fix.get("path", "")
            fixed = fix.get("fixed_content", "")
            issue = fix.get("issue", "")
            if not path or not fixed:
                continue
            print(f"  ✓ {path}: {issue}")
            if path == "README.md":
                readme = fixed
            elif path in files:
                files[path] = fixed

        return files, readme

    except Exception as e:
        print(f"[validator] Skipped: {e}")
        return files, readme


# ── Entry point ───────────────────────────────────────────────────────────────

def generate_project(analysis: dict) -> dict:
    profile = get_profile(analysis["category"])
    print(f"[generator] Category: {analysis['category']} — {profile['quality_bar'][:70]}...")

    design = design_project(analysis)
    print(f"[generator] Design: '{design['project_name']}' — {design['tagline']}")
    print(f"[generator] Abstractions: {[a['name'] for a in design.get('core_abstractions', [])]}")
    time.sleep(4)

    print("[generator] Phase 1: Planning file tree...")
    plan = plan_project(analysis, design)
    file_tree = plan.get("file_tree", [])
    print(f"[generator] Planned {len(file_tree)} files:")
    for f in file_tree:
        print(f"  • {f['path']}")
    time.sleep(4)

    print("\n[generator] Phase 2: Writing files...")
    files: dict[str, str] = {}

    for i, spec in enumerate(file_tree):
        path = spec["path"]
        purpose = spec["purpose"]
        print(f"  [{i+1}/{len(file_tree)}] {path}")
        try:
            files[path] = generate_file(path, purpose, plan, analysis, design, files)
            print(f"         → {files[path].count(chr(10))} lines")
        except Exception as e:
            print(f"  ⚠ Failed {path}: {e}")
            files[path] = f"# {path}\n# {purpose}\n"
        time.sleep(8)

    readme = files.pop("README.md", f"# {plan['repo_name']}\n\n{plan['description']}\n")

    print("\n[generator] Phase 3: Validation pass...")
    files, readme = validate_and_fix(files, readme, plan, design, analysis)

    print(f"\n[generator] ✓ {len(files)} files ready (+README)")

    return {
        "repo_name": plan["repo_name"],
        "description": plan["description"],
        "topics": plan.get("topics", []),
        "readme": readme,
        "files": files,
        "inspired_by": analysis["source_repo"],
        "inspired_by_url": analysis["source_url"],
        "category": analysis["category"],
    }

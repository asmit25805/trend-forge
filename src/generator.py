"""
Four-phase project generator using Cerebras.

Phase 0: Design doc — API contracts, data models, module interfaces
Phase 1: Plan       — file tree informed by design doc + source code
Phase 2: Generate   — each file written against the spec (longer, coherent)
Phase 3: Validate   — static consistency check

Goal: output that looks on par with the trending repo that inspired it.
No AI watermarks. Comments read like a specific human developer wrote them.
"""

import os
import json
import time
from cerebras.cloud.sdk import Cerebras

client = Cerebras(api_key=os.environ["CEREBRAS_API_KEY"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _call(messages: list, max_tokens: int = 2048, temperature: float = 0.5) -> str:
    """Cerebras call with retry on 429/timeout."""
    for attempt in range(5):
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
            if retryable and attempt < 4:
                wait = 15 * (attempt + 1)
                print(f"  [rate limit] waiting {wait}s (attempt {attempt+1}/5)...")
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
    block = "REFERENCE — ACTUAL SOURCE CODE FROM TRENDING REPO (study patterns, never copy):\n"
    block += f"\nDirectory structure:\n{ctx.get('tree_summary', '')[:600]}\n"
    for path, content in ctx.get("source_files", {}).items():
        block += f"\n--- {path} ---\n{content[:1000]}\n"
    return block


# ── Category style profiles ───────────────────────────────────────────────────

CATEGORY_PROFILES = {
    "cli": {
        "language_hint": "Python or Go",
        "structure_hint": "cmd/ for subcommands, internal/ for logic, pkg/ for reusable parts",
        "quality_bar": "Works offline. Sub-100ms startup. Shell completions. --help is comprehensive. Error messages tell you exactly what to fix.",
        "style": "Unix philosophy. Composable. Every flag has a short form. stdout is machine-readable by default.",
        "code_patterns": "argparse/cobra subcommands, os.path.expanduser for config, rich/lipgloss for output, subprocess for shell ops",
    },
    "ai": {
        "language_hint": "Python",
        "structure_hint": "src/models/, src/pipelines/, src/utils/, examples/",
        "quality_bar": "Type-annotated throughout. Async-first where latency matters. Config via pydantic. Structured logging. Works without GPU.",
        "style": "Research-grade but ships. Not a Jupyter notebook. Real retry logic, real timeouts, real error messages.",
        "code_patterns": "pydantic BaseSettings, loguru/structlog, httpx for async HTTP, dataclasses for internal models, ABC for base classes",
    },
    "llm": {
        "language_hint": "Python",
        "structure_hint": "src/agents/, src/prompts/, src/memory/, src/tools/",
        "quality_bar": "Agent loop handles failures gracefully. Token counting is real. Memory persists across sessions. Tools have input validation.",
        "style": "Production agent patterns. State is explicit. Every tool call is logged. Retry on transient failures.",
        "code_patterns": "ABC for agent base, @dataclass for tool schemas, sqlite3 for memory, threading.Lock for shared state, json for persistence",
    },
    "agent": {
        "language_hint": "Python or TypeScript",
        "structure_hint": "src/agents/, src/tools/, src/memory/, src/runtime/",
        "quality_bar": "Plan/act/observe loop is inspectable. Tools declare their schema. Memory has a TTL. Human checkpoints are real.",
        "style": "Debuggable by design. Every agent decision is logged with reasoning. Tools fail loudly with useful messages.",
        "code_patterns": "event emitter for agent lifecycle, json schema for tool validation, sqlite for persistence, asyncio for parallel tool calls",
    },
    "web": {
        "language_hint": "TypeScript with Next.js or Python with FastAPI",
        "structure_hint": "app/ with routing, components/, lib/, api/",
        "quality_bar": "No hydration errors. Loading states everywhere. Error boundaries that recover. Auth that actually works.",
        "style": "Production patterns. No setTimeout hacks. Real optimistic updates. Accessible by default.",
        "code_patterns": "zod for validation, React Query/SWR for data, Tailwind for styling, next-auth or lucia for auth, Prisma for DB",
    },
    "api": {
        "language_hint": "Python (FastAPI) or TypeScript (Hono/Express)",
        "structure_hint": "src/routes/, src/middleware/, src/models/, src/services/",
        "quality_bar": "Every endpoint has a schema. 4xx errors have actionable messages. Rate limiting is real. Health check endpoint exists.",
        "style": "REST done right. Idempotent where possible. Pagination on all list endpoints. No N+1 queries.",
        "code_patterns": "pydantic/zod for validation, dependency injection, middleware chain, background tasks, proper HTTP status codes",
    },
    "developer-tools": {
        "language_hint": "Python, TypeScript, or Go",
        "structure_hint": "src/core/, src/plugins/, src/reporters/, bin/",
        "quality_bar": "Zero config to get a useful result. Plugin API is stable. Config file is optional. Watch mode works.",
        "style": "Opinionated defaults, escapable via config. Errors point to the exact line and suggest the fix.",
        "code_patterns": "plugin registry with hooks, config file discovery walking up dirs, file watcher with debounce, multiple output formats",
    },
    "automation": {
        "language_hint": "Python",
        "structure_hint": "src/tasks/, src/triggers/, src/integrations/, src/scheduler/",
        "quality_bar": "Tasks are idempotent. Every run is logged with duration and result. Dry-run mode is always available. Failed tasks don't block others.",
        "style": "Reliability over cleverness. Explicit over implicit. Every side effect is logged before it happens.",
        "code_patterns": "dataclass for task definition, threading for concurrent tasks, sqlite for run history, contextmanager for cleanup",
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
# This is the key quality upgrade. Before planning files or writing code,
# we make Cerebras think through the full technical design:
# - What are the core abstractions?
# - What do the module interfaces look like?
# - What are the exact function signatures?
# - What data flows between components?
# All subsequent phases reference this doc so everything is coherent.

DESIGN_SYSTEM = """You are a principal engineer designing a new open-source project.
You think deeply before writing a single line of code.
Your design documents are specific, concrete, and implementable — not hand-wavy.
Respond ONLY with valid JSON."""


def design_project(analysis: dict) -> dict:
    """
    Phase 0: Generate a concrete technical design document.
    This ensures all files share a coherent architecture before any code is written.
    """
    profile = get_profile(analysis["category"])
    source_block = _source_context_block(analysis)

    prompt = f"""Design a new open-source project inspired by this trending repo.

INSPIRATION: {analysis['source_repo']} (⭐{analysis['source_stars']})
What it does: {analysis['concept']}
Why it's trending: {analysis['why_trending']}
Architecture pattern used: {analysis.get('architecture_pattern', 'N/A')}
Key implementation insights from its code: {', '.join(analysis.get('key_implementation_insights', []))}
Your original angle: {analysis['inspiration_angle']}
Category: {analysis['category']}

{source_block}

QUALITY BAR TO MATCH OR EXCEED:
{profile['quality_bar']}

CODE PATTERNS TO USE:
{profile['code_patterns']}

Design an ORIGINAL project (not a clone) that could realistically appear on GitHub trending.
Think: what would a senior engineer build over a weekend that solves the same problem better?

Return ONLY this JSON:
{{
  "project_name": "kebab-case",
  "tagline": "one sharp sentence — what it does and why it's better",
  "language": "primary language",
  "core_abstractions": [
    {{
      "name": "ClassName or module_name",
      "role": "what this abstraction does in the system",
      "key_methods": ["method_name(args) -> return_type: what it does"]
    }}
  ],
  "data_models": [
    {{
      "name": "ModelName",
      "fields": ["field_name: type — what it represents"],
      "used_by": ["which modules use this"]
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
  "data_flow": "step-by-step: how data moves through the system from input to output",
  "error_handling_strategy": "how errors propagate and surface to the user",
  "key_design_decisions": ["decision and why — be specific"]
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
Every file must be justified by the design doc — no speculative files.
Respond ONLY with valid JSON."""


def plan_project(analysis: dict, design: dict) -> dict:
    profile = get_profile(analysis["category"])
    gh_user = os.environ.get("GH_USERNAME", "your-username")

    # Build compact design summary for the plan prompt
    abstractions = "\n".join(
        f"  - {a['name']}: {a['role']}" for a in design.get("core_abstractions", [])
    )
    modules = "\n".join(
        f"  - {m['file']}: exports {m['exports']}" for m in design.get("module_interfaces", [])
    )

    prompt = f"""Turn this design document into a concrete file tree.

PROJECT: {design['project_name']}
GitHub user: {gh_user}
Tagline: {design['tagline']}
Language: {design['language']}

CORE ABSTRACTIONS:
{abstractions}

MODULE INTERFACES (these files MUST exist):
{modules}

Data flow: {design['data_flow']}

CATEGORY: {analysis['category']}
Structure convention: {profile['structure_hint']}
Style: {profile['style']}

RULES:
- Every file in module_interfaces above must appear in the file tree
- Also include: README.md, .github/workflows/ci.yml, dependency file, 2+ tests, 1 example
- 10-14 files total — quality over quantity
- "purpose" must reference the design (mention specific class/function names from the design doc)

Return ONLY this JSON:
{{
  "repo_name": "{design['project_name']}",
  "description": "{design['tagline']}",
  "language": "{design['language']}",
  "topics": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "file_tree": [
    {{"path": "README.md", "purpose": "specific to this project"}},
    {{"path": "src/core/engine.py", "purpose": "implements ClassName — specific detail"}}
  ]
}}"""

    raw = _call(
        [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.4,
    )
    return _parse_json(raw)


# ── Phase 2: File generation ──────────────────────────────────────────────────

FILE_SYSTEM = """You are writing code for a real open-source project you care about.
You write like a senior engineer on a good day — not verbose, not terse, just right.
Real logic. Real error handling. Comments only where the code isn't obvious.
No filler. No "TODO: implement this". No placeholder functions.
You never mention AI, generation, or automation. You just write code.
Output raw file content only — no markdown fences, no explanation."""


def generate_file(
    path: str,
    purpose: str,
    plan: dict,
    analysis: dict,
    design: dict,
    already_written: dict,
    github_username: str = "",
) -> str:
    profile = get_profile(analysis["category"])
    source_block = _source_context_block(analysis)
    gh_user = github_username or os.environ.get("GH_USERNAME", "your-username")

    # Full design context for this file
    design_context = f"""TECHNICAL DESIGN:
Project: {design['project_name']} — {design['tagline']}
Language: {design['language']}
Data flow: {design['data_flow']}
Error strategy: {design['error_handling_strategy']}

Core abstractions:
{json.dumps(design.get('core_abstractions', []), indent=2)}

Data models:
{json.dumps(design.get('data_models', []), indent=2)}

Key design decisions:
{chr(10).join('- ' + d for d in design.get('key_design_decisions', []))}"""

    # Show last 4 already-written files for import consistency
    written_context = ""
    if already_written:
        written_context = "\nALREADY WRITTEN (match naming and imports exactly):\n"
        for p, content in list(already_written.items())[-4:]:
            # Show more of each file so imports are clear
            preview = content[:800].replace("\n", "\\n")
            written_context += f"\n// {p}\n{preview}...\n"

    prompt = f"""Write this file for project '{plan['repo_name']}'.

{design_context}

{source_block}

{written_context}

FILE TO WRITE: {path}
What it does: {purpose}

QUALITY BAR: {profile['quality_bar']}
STYLE: {profile['style']}
CODE PATTERNS: {profile['code_patterns']}

SPECIFIC RULES FOR THIS FILE:
- If README.md: Use github.com/{gh_user}/{plan['repo_name']} in all URLs. Include: badges (build status, version, license), one-liner pitch, install (pip/npm/go install), quickstart with real working example (copy-paste runnable), full API reference, config options table, contributing guide. At least 150 lines.
- If test file: Import from the actual module paths in this project. Write at least 5 real test cases per file testing actual behaviour, not just that functions exist. Use pytest/jest/go test as appropriate.
- If CI yaml: Working pipeline — install deps, lint, test. Use correct actions for the language. Pin action versions.
- If source code: Implement the FULL logic described in the design doc for this module. At least 80 lines. Real implementations, not stubs.
- All files: Type annotations on every function. Docstrings only on public APIs (one line, no fluff). Private functions: comment only if non-obvious.

Write the complete file now:"""

    return _call(
        [{"role": "system", "content": FILE_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=4096,  # raised from 3000 — real files need space
        temperature=0.3,  # lower = more consistent, less hallucination
    )


# ── Phase 3: Validation pass ──────────────────────────────────────────────────

VALIDATE_SYSTEM = """You are doing a final review before a repo goes public.
You fix real problems: broken imports, wrong function names, incomplete implementations, stubs.
Be specific. If a fix needs full file content, provide it.
Respond ONLY with valid JSON."""


def validate_and_fix(
    files: dict, readme: str, plan: dict, design: dict, analysis: dict
) -> tuple[dict, str]:
    # Give validator the design doc so it can check against spec
    design_summary = f"""Design spec:
- Core abstractions: {[a['name'] for a in design.get('core_abstractions', [])]}
- Data models: {[m['name'] for m in design.get('data_models', [])]}
- Module interfaces: {[m['file'] + ' exports ' + str(m['exports']) for m in design.get('module_interfaces', [])]}"""

    snapshot = f"=== README.md (first 400 chars) ===\n{readme[:400]}\n"
    for path, content in files.items():
        snapshot += f"\n=== {path} (first 600 chars) ===\n{content[:600]}\n"

    prompt = f"""Review this project before it goes public.

{design_summary}

PROJECT FILES:
{snapshot}

Find and fix:
1. Import that references a path or name not in the file tree
2. Function/class called in one file but defined differently in another  
3. Stub implementations — pass, raise NotImplementedError, empty function body
4. Test that imports a wrong path or tests nothing real
5. README URLs using "your-org", "your-username", or other placeholders
6. File that's clearly too short to be real (under 20 lines for a source file)

Return ONLY this JSON:
{{
  "fixes": [
    {{
      "path": "src/core/engine.py",
      "issue": "one-line description of the actual problem",
      "fixed_content": "complete corrected file — not a diff, the whole thing"
    }}
  ]
}}"""

    try:
        raw = _call(
            [{"role": "system", "content": VALIDATE_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=2048,
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
    gh_user = os.environ.get("GH_USERNAME", "")
    print(f"[generator] Category: {analysis['category']} — quality bar: {profile['quality_bar'][:60]}...")

    # Phase 0: Design document — the key quality upgrade
    design = design_project(analysis)
    print(f"[generator] Design: '{design['project_name']}' — {design['tagline']}")
    print(f"[generator] Core abstractions: {[a['name'] for a in design.get('core_abstractions', [])]}")
    time.sleep(4)

    # Phase 1: File tree informed by design
    print("[generator] Phase 1: Planning file tree...")
    plan = plan_project(analysis, design)
    file_tree = plan.get("file_tree", [])
    print(f"[generator] Planned {len(file_tree)} files:")
    for f in file_tree:
        print(f"  • {f['path']}")
    time.sleep(4)

    # Phase 2: Write each file against the spec
    print("\n[generator] Phase 2: Writing files...")
    files: dict[str, str] = {}

    for i, spec in enumerate(file_tree):
        path = spec["path"]
        purpose = spec["purpose"]
        print(f"  [{i+1}/{len(file_tree)}] {path}")
        try:
            files[path] = generate_file(
                path, purpose, plan, analysis, design, files, github_username=gh_user
            )
            line_count = files[path].count("\n")
            print(f"         → {line_count} lines")
        except Exception as e:
            print(f"  ⚠ Failed {path}: {e}")
            files[path] = f"# {path}\n# {purpose}\n"
        time.sleep(8)

    readme = files.pop("README.md", f"# {plan['repo_name']}\n\n{plan['description']}\n")

    # Phase 3: Validation pass
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

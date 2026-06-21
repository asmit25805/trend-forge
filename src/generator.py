"""
Four-phase project generator using Cerebras.

Phase 0: Design doc — API contracts, data models, module interfaces
Phase 1: Plan       — file tree informed by design doc
Phase 2: Generate   — each file written against the spec
Phase 2b: Stub check — regenerate any file that came back as a stub
Phase 3: Validate   — static consistency check

No AI watermarks. Comments read like a specific human developer wrote them.
"""

import os
import json
import time
from cerebras.cloud.sdk import Cerebras

client = Cerebras(
    api_key=os.environ["CEREBRAS_API_KEY"],
    timeout=120,
    max_retries=0,
)

GH_USER = os.environ.get("GH_USERNAME", "")

STUB_MARKERS = [
    "will be added in a later",
    "will be implemented in a later",
    "minimal scaffolding",
    "placeholder",
    "full implementation",
    "subsequent iteration",
    "to be implemented",
    "not yet implemented",
    "coming soon",
    "left as an exercise",
    "stub implementation",
    "raise NotImplementedError",
    "pass\n",
    "# TODO",
]

# Shared type files that must always exist — the model frequently imports from
# these without planning them, causing CI failures.
REQUIRED_TYPE_FILES = {
    "python": "src/core/models.py",
    "typescript": "src/types.ts",
    "go": "internal/types.go",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _call(messages: list, max_tokens: int = 2048, temperature: float = 0.5) -> str:
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
                wait = 20 * (attempt + 1)
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


def _is_stub(content: str) -> bool:
    lower = content.lower()
    return any(marker.lower() in lower for marker in STUB_MARKERS)


def _source_context_block(analysis: dict) -> str:
    ctx = analysis.get("_source_context", {})
    if not ctx:
        return ""
    block = "REFERENCE SOURCE CODE (study the patterns and architecture, never copy verbatim):\n"
    block += f"\nDirectory structure:\n{ctx.get('tree_summary', '')[:600]}\n"
    for path, content in ctx.get("source_files", {}).items():
        block += f"\n--- {path} ---\n{content[:1000]}\n"
    return block


def _install_cmd(language: str, repo_name: str) -> str:
    lang = language.lower()
    if "typescript" in lang or "javascript" in lang:
        return f"npm install {repo_name}"
    if "go" in lang:
        return f"go get github.com/{GH_USER}/{repo_name}"
    if "rust" in lang:
        return f"cargo add {repo_name}"
    return f"pip install {repo_name}"


def _ci_setup(language: str) -> str:
    lang = language.lower()
    if "typescript" in lang or "javascript" in lang:
        return "actions/setup-node@v4 with node-version 20, npm ci, npm run lint, npm test"
    if "go" in lang:
        return "actions/setup-go@v5, go vet ./..., go test ./..."
    if "rust" in lang:
        return "actions-rs/toolchain, cargo clippy, cargo test"
    return "actions/setup-python@v5, pip install -e .[dev], ruff check ., pytest -q"


def _required_types_file(language: str) -> str | None:
    lang = language.lower()
    for key, path in REQUIRED_TYPE_FILES.items():
        if key in lang:
            return path
    return None


def _ascii_architecture(design: dict) -> str:
    """Build a simple ASCII box diagram from the design's core abstractions."""
    abstractions = design.get("core_abstractions", [])
    if not abstractions:
        return ""

    boxes = [a["name"] for a in abstractions]
    width = max(len(b) for b in boxes) + 4

    lines = ["```"]
    for i, name in enumerate(boxes):
        padding = width - len(name) - 2
        left = padding // 2
        right = padding - left
        box = f"┌{'─' * (width - 2)}┐"
        mid = f"│ {' ' * left}{name}{' ' * right} │"
        bot = f"└{'─' * (width - 2)}┘"
        lines.append(box)
        lines.append(mid)
        lines.append(bot)
        if i < len(boxes) - 1:
            lines.append(f"{'│':^{width}}")
            lines.append(f"{'▼':^{width}}")
    lines.append("```")
    return "\n".join(lines)


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
        "language_hint": "TypeScript",
        "structure_hint": "src/core/, src/cli/, src/plugins/, tests/",
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
Think deeply — what are the right abstractions? What are the failure modes?
Respond ONLY with valid JSON. Never truncate."""


def design_project(analysis: dict) -> dict:
    profile = get_profile(analysis["category"])
    source_block = _source_context_block(analysis)

    prompt = f"""Design an original open-source project inspired by this trending repo.

INSPIRATION: {analysis['source_repo']} (⭐{analysis['source_stars']})
What it does: {analysis['concept']}
Why it's trending: {analysis['why_trending']}
Architecture: {analysis.get('architecture_pattern', 'N/A')}
Key insights from its code: {', '.join(analysis.get('key_implementation_insights', []))}
Your original angle: {analysis['inspiration_angle']}
Category: {analysis['category']}
GitHub owner: {GH_USER}

{source_block}

QUALITY BAR: {profile['quality_bar']}
CODE PATTERNS: {profile['code_patterns']}

Design an ORIGINAL project — not a clone, not a fork. A senior engineer's weekend project
that solves a related problem in a meaningfully different or better way.

Return ONLY this JSON:
{{
  "project_name": "kebab-case — short, memorable, describes what it does",
  "tagline": "one sharp sentence — what it does and the specific benefit",
  "language": "primary language",
  "github_user": "{GH_USER}",
  "core_abstractions": [
    {{
      "name": "ClassName or module_name",
      "role": "what this abstraction does in the system",
      "key_methods": ["method_name(args) -> return_type: what it does and why"]
    }}
  ],
  "data_models": [
    {{
      "name": "ModelName",
      "fields": ["field_name: type — what it represents and valid values"],
      "used_by": ["which modules use this and how"]
    }}
  ],
  "module_interfaces": [
    {{
      "file": "src/core/engine.py",
      "exports": ["ClassName", "function_name"],
      "imports_from": ["other/module.py"],
      "key_logic": "2-3 sentences on the non-obvious logic — what makes this module interesting"
    }}
  ],
  "data_flow": "numbered steps: 1. input arrives as X → 2. processed by Y → 3. output is Z",
  "error_handling_strategy": "specific: which errors are fatal, which are retried, what the user sees",
  "key_design_decisions": [
    "decision: why this approach over the obvious alternative — be concrete"
  ]
}}"""

    print("[generator] Phase 0: Designing architecture...")
    raw = _call(
        [{"role": "system", "content": DESIGN_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=4000,
        temperature=0.5,
    )
    return _parse_json(raw)


# ── Phase 1: File tree plan ───────────────────────────────────────────────────

PLAN_SYSTEM = """You are a senior engineer turning a design document into a concrete file tree.
Every file must be justified by the design. No speculative files.
Respond ONLY with valid JSON. Never truncate."""


def plan_project(analysis: dict, design: dict) -> dict:
    profile = get_profile(analysis["category"])
    language = design.get("language", "")
    types_file = _required_types_file(language)

    abstractions = "\n".join(
        f"  - {a['name']}: {a['role']}" for a in design.get("core_abstractions", [])
    )
    modules = "\n".join(
        f"  - {m['file']}: exports {m['exports']}" for m in design.get("module_interfaces", [])
    )

    # FIX 1: Tell the model exactly which shared types file must exist
    types_requirement = ""
    if types_file:
        types_requirement = f"""- REQUIRED: include '{types_file}' — this is the shared types/models file.
  ALL other modules must import their data models from this single file.
  This prevents import errors where modules reference types that don't exist."""

    # FIX 4: Tighten topics requirement
    prompt = f"""Turn this design document into a concrete file tree.

PROJECT: {design['project_name']}
URL: github.com/{GH_USER}/{design['project_name']}
Tagline: {design['tagline']}
Language: {language}

CORE ABSTRACTIONS:
{abstractions}

MODULE INTERFACES (every one of these files MUST appear in the file tree):
{modules}

Data flow: {design['data_flow']}
Category: {analysis['category']}
Structure convention: {profile['structure_hint']}

RULES:
- Every file listed in module_interfaces above must be in the file tree
- Also include: README.md, .github/workflows/ci.yml, dependency file, 2 test files, 1 example
{types_requirement}
- 10-14 files total — quality over quantity
- "purpose" must reference specific class or function names from the design doc above
- Do NOT include a LICENSE file

TOPICS RULE — topics must be specific, not generic:
- Include at least 2 technology-specific tags (e.g. "fastapi", "sqlite", "pydantic", "click", "jest")
- Include at least 2 problem-specific tags (e.g. "llm-routing", "dependency-analysis", "test-generation")
- Avoid generic tags like "python", "typescript", "tool", "library", "cli" alone

Return ONLY this JSON (no extra text, no truncation):
{{
  "repo_name": "{design['project_name']}",
  "description": "{design['tagline']}",
  "language": "{language}",
  "topics": ["tech-specific-1", "tech-specific-2", "problem-specific-1", "problem-specific-2", "category-tag"],
  "file_tree": [
    {{"path": "README.md", "purpose": "project overview, install, quickstart, full API reference"}},
    {{"path": "src/core/engine.py", "purpose": "implements Engine — orchestrates X and Y"}}
  ]
}}"""

    raw = _call(
        [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=2500,
        temperature=0.4,
    )
    result = _parse_json(raw)

    # FIX 1: Enforce the types file exists in the tree regardless of what the model planned
    if types_file:
        existing_paths = {f["path"] for f in result.get("file_tree", [])}
        if types_file not in existing_paths:
            result["file_tree"].insert(1, {
                "path": types_file,
                "purpose": f"shared data models and type definitions — all other modules import from here",
            })
            print(f"[generator] Auto-added required types file: {types_file}")

    return result


# ── Phase 2: File generation ──────────────────────────────────────────────────

FILE_SYSTEM = """You are writing code for a real open-source project you care about.
You write like a senior engineer on a good day — focused, precise, no fluff.
Real logic. Real error handling. No stubs. No filler.
You never mention AI, generation, or automation anywhere in the output.
Output raw file content only — no markdown fences, no explanation before or after."""


def generate_file(
    path: str,
    purpose: str,
    plan: dict,
    analysis: dict,
    design: dict,
    already_written: dict,
    all_file_paths: list[str],
) -> str:
    profile = get_profile(analysis["category"])
    language = design.get("language", "")
    repo_name = plan["repo_name"]
    types_file = _required_types_file(language)

    design_context = f"""TECHNICAL DESIGN:
Project: {design['project_name']} — {design['tagline']}
Language: {language}
Data flow: {design['data_flow']}
Error handling: {design['error_handling_strategy']}

Core abstractions:
{json.dumps(design.get('core_abstractions', []), indent=2)}

Data models:
{json.dumps(design.get('data_models', []), indent=2)}

Key design decisions:
{chr(10).join('- ' + d for d in design.get('key_design_decisions', []))}"""

    # FIX 1: Pass the FULL list of planned files so the model knows exactly what exists
    files_list = "\n".join(f"  - {p}" for p in all_file_paths)
    files_context = f"""
ALL FILES IN THIS PROJECT (only import from paths listed here):
{files_list}

{"IMPORTANT: All shared data models and types must be imported from: " + types_file if types_file else ""}
"""

    written_context = ""
    if already_written:
        written_context = "\nALREADY WRITTEN FILES (match naming, imports, and style exactly):\n"
        for p, content in list(already_written.items())[-4:]:
            # FIX 2: Increased from 1500 to 3000 chars so full class signatures are visible
            preview = content[:3000].replace("\n", "\\n")
            written_context += f"\n// {p}\n{preview}...\n"

    install = _install_cmd(language, repo_name)
    ci_setup = _ci_setup(language)

    # FIX 3: Build ASCII architecture diagram to inject into README
    arch_diagram = ""
    if path == "README.md":
        arch_diagram = f"""
ARCHITECTURE DIAGRAM TO INCLUDE (put this in the Architecture section):
{_ascii_architecture(design)}
"""

    prompt = f"""Write this file for the project '{repo_name}' (github.com/{GH_USER}/{repo_name}).

{design_context}

{files_context}

{written_context}

{arch_diagram}

FILE TO WRITE: {path}
Purpose: {purpose}

QUALITY BAR: {profile['quality_bar']}
STYLE: {profile['style']}
CODE PATTERNS: {profile['code_patterns']}

════════════════════════════════════════════════════════
ABSOLUTE RULES — every violation makes the output unusable:
════════════════════════════════════════════════════════

FORBIDDEN in ALL files:
  ✗ Any mention of "Alice", "Bob", "example.com", "your-org", "your-username", "maintainer@"
  ✗ Any fake email address or placeholder contact information
  ✗ Phrases: "generated by", "auto-generated", "created by AI", "maintained by"
  ✗ "End of README", "End of file", or any such markers
  ✗ Do NOT generate a LICENSE file or LICENSE section in README
  ✗ NEVER write stub code — no "pass", no "raise NotImplementedError", no empty bodies
  ✗ NEVER write comments like "full implementation will be added later", "placeholder", "TODO"
  ✗ NEVER import from a path that is NOT in the "ALL FILES IN THIS PROJECT" list above

README.md rules (only when writing README.md):
  ✓ All GitHub URLs: github.com/{GH_USER}/{repo_name} — no exceptions
  ✓ Install: {install}
  ✓ Quickstart: copy-paste runnable, shows real expected output
  ✓ Sections: Overview, Features, Installation, Quickstart, Architecture, API Reference, Contributing
  ✓ Architecture section: include the ASCII diagram provided above
  ✓ API Reference documents every public class and function with signature and description
  ✓ Contributing: fork → branch → test → PR only — no fake maintainer contact
  ✓ At least 150 lines
  ✗ No "License" section — do not mention MIT, Apache, or any license
  ✗ No fake "Created by" or "Maintained by" or "Contact:" lines

Test file rules (only when writing test files):
  ✓ Import from actual module paths in THIS project (check the file list above)
  ✓ At least 6 test functions per file, each testing real behaviour
  ✓ Descriptive names: test_engine_retries_on_transient_error not test_basic
  ✓ Real assertions on real return values
  ✗ No "# placeholder", "# TODO", "# Import the package under test" comments
  ✗ No assert True, assert x or True patterns

CI yaml rules (only when writing .github/workflows/ci.yml):
  ✓ Trigger on push and pull_request to main
  ✓ Steps: checkout → {ci_setup}
  ✓ Pin all action versions (checkout@v4, setup-python@v5 or setup-node@v4)

Source code rules (all .py / .ts / .go / .rs files):
  ✓ Implement the FULL logic from the design doc — every method must have a real body
  ✓ At least 100 lines of real code
  ✓ Type annotations on every function signature
  ✓ Docstrings on public API only — one line, describes behaviour
  ✗ No pass, no raise NotImplementedError, no empty function bodies
  ✗ Only import from files that exist in the project file list above

Write the complete file now. Do not add any preamble or explanation:"""

    return _call(
        [{"role": "system", "content": FILE_SYSTEM}, {"role": "user", "content": prompt}],
        max_tokens=6000,
        temperature=0.25,
    )


# ── Phase 2b: Stub detection and regeneration ─────────────────────────────────

def _check_and_regenerate_stubs(
    files: dict,
    file_tree: list,
    plan: dict,
    analysis: dict,
    design: dict,
    all_file_paths: list[str],
) -> dict:
    purpose_map = {spec["path"]: spec["purpose"] for spec in file_tree}

    for path in list(files.keys()):
        content = files[path]
        if _is_stub(content):
            purpose = purpose_map.get(path, "implement the module described in the design doc")
            print(f"  ⚠ Stub detected in {path} — regenerating...")
            time.sleep(5)
            try:
                new_content = generate_file(
                    path, purpose, plan, analysis, design, files, all_file_paths
                )
                if _is_stub(new_content):
                    print(f"  ⚠ Still a stub after regeneration: {path} — keeping original")
                else:
                    files[path] = new_content
                    print(f"         → regenerated: {new_content.count(chr(10))} lines")
            except Exception as e:
                print(f"  ⚠ Regeneration failed for {path}: {e}")
            time.sleep(8)

    return files


# ── Phase 3: Validation pass ──────────────────────────────────────────────────

VALIDATE_SYSTEM = """You are a senior engineer doing a final review before a repo goes public.
You find and fix every real problem. No false positives, no missed issues.
Respond ONLY with valid JSON. Never truncate the response."""


def validate_and_fix(
    files: dict, readme: str, plan: dict, design: dict, analysis: dict
) -> tuple[dict, str]:
    language = design.get("language", "")
    repo_name = plan["repo_name"]
    install = _install_cmd(language, repo_name)

    design_summary = f"""Spec:
- Language: {language}
- Abstractions: {[a['name'] for a in design.get('core_abstractions', [])]}
- Models: {[m['name'] for m in design.get('data_models', [])]}
- Modules: {[m['file'] + ' → exports ' + str(m['exports']) for m in design.get('module_interfaces', [])]}
- Install command: {install}
- GitHub: github.com/{GH_USER}/{repo_name}
- All files in project: {list(files.keys())}"""

    snapshot = f"=== README.md (first 800 chars) ===\n{readme[:800]}\n"
    for path, content in files.items():
        snapshot += f"\n=== {path} (first 400 chars) ===\n{content[:400]}\n"

    prompt = f"""Review this project and fix every problem you find before it goes public.

{design_summary}

FILE SNAPSHOTS:
{snapshot}

CHECK FOR AND FIX ALL OF THE FOLLOWING:

Code issues:
1. Import referencing a path or name NOT in the project files list above
2. Function/class called in one file but defined with a different name in another
3. Stub implementations: pass, raise NotImplementedError, empty body
4. assert True or assert x or True — meaningless test assertions
5. Truncated import blocks (import line cut off mid-statement)

README issues:
6. URLs containing "your-org" or "your-username" → replace with github.com/{GH_USER}/{repo_name}
7. Wrong install command → should be: {install}
8. Fake email addresses → remove entirely
9. "Created by", "Maintained by", "Contact:" with fake names → remove entirely
10. "*End of README*" or similar markers → remove
11. "License" or "MIT License" section → remove entirely
12. Missing API Reference section → add it
13. Missing Architecture section with diagram → add it

General:
14. Any mention of "generated", "auto-generated", "AI" in comments or docstrings → remove

Return ONLY this JSON — include a fix for EVERY issue found, empty list if none:
{{
  "fixes": [
    {{
      "path": "README.md",
      "issue": "concise description of the specific problem",
      "fixed_content": "the complete corrected file — not a diff, the entire content"
    }}
  ]
}}"""

    try:
        raw = _call(
            [{"role": "system", "content": VALIDATE_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=8000,
            temperature=0.15,
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
            else:
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
    time.sleep(6)

    print("[generator] Phase 1: Planning file tree...")
    plan = plan_project(analysis, design)
    file_tree = plan.get("file_tree", [])
    # FIX 1: Build the full file path list once and pass it to every generation call
    all_file_paths = [f["path"] for f in file_tree]
    print(f"[generator] Planned {len(file_tree)} files:")
    for f in file_tree:
        print(f"  • {f['path']}")
    time.sleep(6)

    print("\n[generator] Phase 2: Writing files...")
    files: dict[str, str] = {}

    for i, spec in enumerate(file_tree):
        path = spec["path"]
        purpose = spec["purpose"]
        print(f"  [{i+1}/{len(file_tree)}] {path}")
        try:
            files[path] = generate_file(
                path, purpose, plan, analysis, design, files, all_file_paths
            )
            print(f"         → {files[path].count(chr(10))} lines")
        except Exception as e:
            print(f"  ⚠ Failed {path}: {e}")
            files[path] = f"# {path}\n# {purpose}\n"
        time.sleep(10)

    readme = files.pop("README.md", f"# {plan['repo_name']}\n\n{plan['description']}\n")

    print("\n[generator] Phase 2b: Checking for stubs...")
    files = _check_and_regenerate_stubs(
        files, file_tree, plan, analysis, design, all_file_paths
    )

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

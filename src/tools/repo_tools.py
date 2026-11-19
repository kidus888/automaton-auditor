"""
RepoInvestigator forensic tools (the "AST Detective").

Every analysis here is static: the audited repository is cloned into a sandbox and read as
text / parsed with Python's ``ast`` module. The audited code is NEVER imported or executed,
because it is untrusted input.
"""

import ast
import difflib
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Set, Tuple

from src.state import Evidence

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages",
    ".tox", ".mypy_cache", ".pytest_cache", "build", "dist",
}
MAX_FILE_BYTES = 1_000_000
START_NODE, END_NODE = "__start__", "__end__"


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------

def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _read(path: str) -> str:
    """Reads a file from the sandbox. Symlinks are refused so a hostile repo cannot make the
    auditor read (and then quote in its report) files outside the clone."""
    if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _parse(path: str) -> Tuple[Optional[ast.Module], str]:
    source = _read(path)
    try:
        return ast.parse(source), source
    except (SyntaxError, ValueError):
        return None, source


def _iter_py_files(root: str, subdir: str = "", include_tests: bool = False) -> Iterable[str]:
    base = os.path.join(root, subdir) if subdir else root
    if not os.path.isdir(base):
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith(".")
            and (include_tests or d not in ("tests", "test"))
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _first_existing(repo_path: str, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        p = os.path.join(repo_path, c)
        if os.path.isfile(p):
            return p
    return None


def _segment(source: str, node: ast.AST, limit: int = 2500) -> str:
    seg = ast.get_source_segment(source, node) or ast.unparse(node)
    return seg if len(seg) <= limit else seg[:limit] + "\n# ... (truncated)"


def _qualname(func: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """Resolves a call target like ``sp.run`` or ``system`` to ``subprocess.run`` / ``os.system``
    using the module's import aliases."""
    parts: List[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        root = aliases.get(func.id, func.id)
        return ".".join([root] + parts[::-1])
    return None


def _import_aliases(tree: ast.Module) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _enclosing_functions(tree: ast.Module) -> Dict[ast.AST, ast.AST]:
    """Maps every node to its innermost enclosing FunctionDef (if any)."""
    parent_fn: Dict[ast.AST, ast.AST] = {}

    def visit(node: ast.AST, current: Optional[ast.AST]):
        for child in ast.iter_child_nodes(node):
            nxt = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            if current is not None:
                parent_fn[child] = current
            visit(child, nxt)

    visit(tree, None)
    return parent_fn


def build_repo_manifest(repo_path: str) -> List[str]:
    """Lists repository files (repo-relative, POSIX separators). Uses git when available so
    ignored files (venvs, caches) are excluded; falls back to a filtered directory walk."""
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return sorted({line.strip() for line in res.stdout.splitlines() if line.strip()})
    except (OSError, subprocess.TimeoutExpired):
        pass

    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        files.extend(_rel(os.path.join(dirpath, f), repo_path) for f in filenames)
    return sorted(files)


# ---------------------------------------------------------------------------
# Sandboxed cloning
# ---------------------------------------------------------------------------

_ALLOWED_URL = re.compile(r"^(https?://|ssh://|git://|git@[\w.\-]+:)[\w.\-~/:@%+]+$")
_AUTH_ERROR_MARKERS = (
    "authentication failed", "could not read username", "repository not found",
    "permission denied", "terminal prompts disabled", "403", "401",
)


def validate_repo_url(url: str) -> Optional[str]:
    """Returns an error message if ``url`` is not a safe git remote, else None.

    Blocks git option injection (``--upload-pack=...``), ``ext::`` / ``file://`` transports and
    shell metacharacters. Arguments are passed as a list, so there is no shell to inject into,
    but git itself interprets some URL forms as commands."""
    if not url or not url.strip():
        return "Empty repository URL."
    if url.startswith("-"):
        return "Rejected repository URL starting with '-' (git option injection)."
    if any(c.isspace() or ord(c) < 32 for c in url):
        return "Rejected repository URL containing whitespace or control characters."
    if not _ALLOWED_URL.match(url):
        return (
            f"Rejected repository URL '{url}': only https://, ssh://, git:// or git@host:path "
            "remotes (or an existing local directory) are accepted."
        )
    return None


def _classify_git_error(stderr: str, returncode: int) -> str:
    low = stderr.lower()
    if any(m in low for m in _AUTH_ERROR_MARKERS):
        return (
            f"Git authentication/visibility error (exit {returncode}): the repository is private, "
            f"renamed or does not exist. git said: {stderr[:300]}"
        )
    return f"Git clone failed (exit {returncode}): {stderr[:300]}"


def clone_repo_sandboxed(
    repo_url_or_path: str, timeout: int = 120
) -> Tuple[Optional[str], Optional[tempfile.TemporaryDirectory], Optional[str]]:
    """
    Clones a repository into a ``tempfile.TemporaryDirectory()`` sandbox (never the live working
    directory). An existing local directory is analysed in place without cloning.

    Hardening: URL validation, ``--`` separator before the URL, no credential prompts
    (GIT_TERMINAL_PROMPT=0 / ssh BatchMode) so auth failures return instead of hanging,
    symlinks checked out as plain files, full history (no ``--depth``) so the git narrative is
    complete, and a timeout. Returns (repo_path, temp_dir_obj, error_msg); the caller must
    ``cleanup()`` the temp dir.
    """
    if os.path.isdir(repo_url_or_path):
        return os.path.abspath(repo_url_or_path), None, None

    error = validate_repo_url(repo_url_or_path)
    if error:
        return None, None, error

    temp_dir = tempfile.TemporaryDirectory(prefix="auditor_sandbox_")
    target = os.path.join(temp_dir.name, "repo")
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
    }
    cmd = [
        "git", "-c", "core.symlinks=false", "-c", "protocol.ext.allow=never",
        "clone", "--quiet", "--", repo_url_or_path, target,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        temp_dir.cleanup()
        return None, None, f"git clone timed out after {timeout}s."
    except OSError as e:
        temp_dir.cleanup()
        return None, None, f"git executable unavailable: {e}"

    if result.returncode != 0:
        temp_dir.cleanup()
        return None, None, _classify_git_error(result.stderr.strip(), result.returncode)
    return target, temp_dir, None


# ---------------------------------------------------------------------------
# Protocol C: The Git Narrative
# ---------------------------------------------------------------------------

_PHASES = {
    "setup": re.compile(r"\b(init|initial|scaffold|setup|set up|environment|env|depend|pyproject|requirements|uv|chore|config|bootstrap)\b", re.I),
    "tools": re.compile(r"\b(tool|tools|ast|pars\w*|clone|sandbox\w*|pdf|ingest\w*|detective\w*|forensic\w*|git history)\b", re.I),
    "graph": re.compile(r"\b(graph|stategraph|orchestrat\w*|wire|wiring|fan-?out|fan-?in|judge\w*|justice|bench|node\w*)\b", re.I),
}
_ITERATION = re.compile(r"\b(fix\w*|refactor\w*|test\w*|improv\w*|harden\w*)\b", re.I)


def extract_git_history(path: str) -> Evidence:
    """
    Forensic Protocol C. Runs ``git log --reverse`` with author timestamps and judges whether
    history is atomic (step-by-step) or monolithic (single init / bulk upload).
    """
    fmt = "%h%x1f%aI%x1f%s"
    try:
        res = subprocess.run(
            ["git", "-C", path, "log", "--reverse", f"--format={fmt}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return Evidence(goal="git_forensic_analysis", found=False, location=".git",
                        rationale=f"Could not run git log: {e}", confidence=1.0,
                        gaps=["git log could not be executed"])

    if res.returncode != 0:
        return Evidence(goal="git_forensic_analysis", found=False, location=".git",
                        rationale=f"Not a readable git repository: {res.stderr.strip()[:200]}",
                        confidence=1.0, gaps=["Repository has no readable git history"])

    commits: List[Tuple[str, datetime, str]] = []
    for line in res.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            try:
                commits.append((parts[0], datetime.fromisoformat(parts[1]), parts[2]))
            except ValueError:
                continue

    n = len(commits)
    gaps: List[str] = []
    notes: List[str] = []

    shallow = subprocess.run(["git", "-C", path, "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True, check=False).stdout.strip() == "true"
    if shallow:
        gaps.append("Clone is shallow: commit history is truncated, progression cannot be fully verified")

    if n <= 3:
        gaps.append(f"Only {n} commit(s): matches the 'single init / bulk upload' failure pattern")

    span_minutes = 0.0
    if n >= 2:
        span_minutes = (commits[-1][1] - commits[0][1]).total_seconds() / 60.0
        if n > 3 and span_minutes <= 30:
            gaps.append(f"All {n} commits fall within {span_minutes:.0f} minutes: timestamps are clustered (bulk upload)")

    # Monolithic first commit: did the very first commit already contain most of the code?
    if commits:
        first_files = subprocess.run(
            ["git", "-C", path, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commits[0][0]],
            capture_output=True, text=True, check=False,
        ).stdout.split()
        first_py = {f for f in first_files if f.endswith(".py")}
        all_py = {f for f in build_repo_manifest(path) if f.endswith(".py")}
        if len(all_py) >= 5 and len(first_py & all_py) / len(all_py) >= 0.8:
            gaps.append(f"First commit already contains {len(first_py & all_py)}/{len(all_py)} Python files: monolithic upload")

    first_idx = {}
    for phase, pattern in _PHASES.items():
        idx = next((i for i, (_, _, msg) in enumerate(commits) if pattern.search(msg)), None)
        first_idx[phase] = idx
    missing_phases = [p for p, i in first_idx.items() if i is None]
    if missing_phases:
        gaps.append(f"No commits evidencing phase(s): {', '.join(missing_phases)} (expected Setup -> Tools -> Graph)")
    elif not (first_idx["setup"] <= first_idx["tools"] <= first_idx["graph"]):
        gaps.append("Commit order does not follow Environment Setup -> Tool Engineering -> Graph Orchestration")
    else:
        notes.append("Progression Setup -> Tool Engineering -> Graph Orchestration is visible in commit order")

    iteration_commits = sum(1 for _, _, msg in commits if _ITERATION.search(msg))
    if iteration_commits:
        notes.append(f"{iteration_commits} fix/refactor/test commit(s) show iteration after initial implementation")

    found = n > 3 and not any("bulk upload" in g or "monolithic" in g for g in gaps)
    span_text = f" over {span_minutes / 60:.1f} hours" if n >= 2 else ""
    rationale = f"Found {n} commits{span_text}. " + "; ".join(notes)
    if gaps:
        rationale += " Gaps: " + "; ".join(gaps) + "."

    lines = [f"{h} | {ts.isoformat()} | {msg}" for h, ts, msg in commits]
    content = f"Total Commits: {n}\n" + "\n".join(lines[:60]) + ("\n..." if n > 60 else "")
    return Evidence(goal="git_forensic_analysis", found=found, content=content, location=".git (git log --reverse)",
                    rationale=rationale.strip(), confidence=0.9 if shallow else 1.0, gaps=gaps)


# Backwards-compatible name
get_git_history = extract_git_history


# ---------------------------------------------------------------------------
# State Management Rigor (Phase 0)
# ---------------------------------------------------------------------------

def _tail_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _collect_classes(repo_path: str) -> Dict[str, Tuple[List[str], ast.ClassDef, str, str]]:
    """name -> (base names, ClassDef, source, file) for every class under the repo (tests excluded)."""
    classes: Dict[str, Tuple[List[str], ast.ClassDef, str, str]] = {}
    for f in _iter_py_files(repo_path):
        tree, source = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name, ([_tail_name(b) for b in node.bases], node, source, f))
    return classes


def _inherits(name: str, target: str, classes: Dict, seen: Optional[Set[str]] = None) -> bool:
    seen = seen or set()
    if name in seen or name not in classes:
        return False
    seen.add(name)
    bases = classes[name][0]
    return target in bases or any(_inherits(b, target, classes, seen) for b in bases)


def _annotated_fields(cls: ast.ClassDef) -> Dict[str, Tuple[Set[str], Optional[str]]]:
    """field -> (names used in the type, reducer source or None)."""
    fields: Dict[str, Tuple[Set[str], Optional[str]]] = {}
    for stmt in cls.body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        ann = stmt.annotation
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                continue
        reducer = None
        type_expr = ann
        if isinstance(ann, ast.Subscript) and _tail_name(ann.value) == "Annotated":
            sl = ann.slice
            if isinstance(sl, ast.Tuple) and len(sl.elts) >= 2:
                type_expr, reducer = sl.elts[0], ast.unparse(sl.elts[1])
        names = {n.id for n in ast.walk(type_expr) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(type_expr) if isinstance(n, ast.Attribute)}
        fields[stmt.target.id] = (names, reducer)
    return fields


def analyze_state_management(repo_path: str) -> Evidence:
    """
    AST-verifies the typed state: AgentState is a TypedDict/BaseModel, Evidence and
    JudicialOpinion are Pydantic models (transitively), and the keys holding them carry
    ``Annotated[..., reducer]`` metadata so parallel writers merge instead of overwrite.
    """
    classes = _collect_classes(repo_path)
    preferred = [os.path.join(repo_path, p) for p in ("src/state.py", "src/graph.py", "state.py")]

    def is_typed(name: str) -> bool:
        return _inherits(name, "TypedDict", classes) or _inherits(name, "BaseModel", classes)

    candidates = [n for n in classes if n.endswith("State") and is_typed(n)]
    candidates.sort(key=lambda n: (n != "AgentState", classes[n][3] not in preferred, -len(_annotated_fields(classes[n][1]))))

    if not candidates:
        return Evidence(
            goal="state_management_rigor", found=False, location="src/state.py",
            rationale="No TypedDict/BaseModel state class (e.g. AgentState) found anywhere in the repository via AST.",
            confidence=1.0,
            gaps=["No typed AgentState (TypedDict or Pydantic BaseModel) is defined"],
        )

    state_name = candidates[0]
    _, cls, source, file = classes[state_name]
    fields = _annotated_fields(cls)

    def field_for(fragment: str) -> Optional[Tuple[str, Set[str], Optional[str]]]:
        for fname, (names, reducer) in fields.items():
            if any(fragment in n for n in names):
                return fname, names, reducer
        return None

    gaps: List[str] = []
    notes = [f"{state_name} inherits {'TypedDict' if _inherits(state_name, 'TypedDict', classes) else 'BaseModel'}"]

    for label, fragment in (("Evidence", "Evidence"), ("JudicialOpinion", "Opinion")):
        hit = field_for(fragment)
        if not hit:
            gaps.append(f"{state_name} has no field holding {label} objects")
            continue
        fname, names, reducer = hit
        model = next((n for n in names if fragment in n), fragment)
        if not _inherits(model, "BaseModel", classes):
            gaps.append(f"{model} is not a Pydantic BaseModel (dict soup risk)")
        else:
            notes.append(f"{model} is a Pydantic BaseModel")
        if reducer:
            notes.append(f"'{fname}' uses reducer {reducer}")
        else:
            gaps.append(f"'{fname}' has no Annotated reducer: parallel writers will overwrite each other")

    found = not gaps
    return Evidence(
        goal="state_management_rigor", found=found,
        content=_segment(source, cls), location=_rel(file, repo_path),
        rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Forensic Protocol B: Graph Wiring
# ---------------------------------------------------------------------------

_ROLE = {
    "detective": re.compile(r"repo|doc|vision|detective|investigat|analy[sz]|inspect|forensic", re.I),
    "judge": re.compile(r"prosecut|defen[cs]e|tech_?lead|judge|critic|advocate", re.I),
    "aggregator": re.compile(r"aggregat|sync|gather|merge|fan_?in|join|collect", re.I),
    "chief": re.compile(r"chief|justice|synth|verdict|supreme", re.I),
    "error": re.compile(r"error|missing|fail|fallback|abort|retry|handle|recover", re.I),
}


class _GraphExtractor:
    """Builds the node/edge graph of every StateGraph in a module from its AST."""

    def __init__(self, tree: ast.Module, source: str):
        self.tree, self.source = tree, source
        self.consts: Dict[str, str] = {}
        self.funcs: Dict[str, ast.AST] = {}
        self.stategraph_calls: List[ast.Call] = []
        self.nodes: Set[str] = set()
        self.static_edges: List[Tuple[str, str]] = []
        self.join_edges: List[Tuple[List[str], str]] = []
        self.conditionals: List[Dict] = []

        self.list_consts: Dict[str, List[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.consts[t.id] = node.value.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name] = node
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
                values = [self._name(e) for e in node.value.elts]
                if values and all(values):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            self.list_consts[t.id] = values

        # Wiring done in loops (for d in DETECTIVES: builder.add_edge(SRC, d)) is expanded per value
        handled: Set[int] = set()
        for loop in (n for n in ast.walk(tree) if isinstance(n, ast.For) and isinstance(n.target, ast.Name)):
            values = self._str_list(loop.iter)
            if not values:
                continue
            calls = [n for n in ast.walk(loop) if isinstance(n, ast.Call) and id(n) not in handled]
            previous = self.consts.get(loop.target.id)
            for v in values:
                self.consts[loop.target.id] = v
                for c in calls:
                    self._visit_call(c)
            if previous is None:
                self.consts.pop(loop.target.id, None)
            else:
                self.consts[loop.target.id] = previous
            handled.update(id(c) for c in calls)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and id(node) not in handled:
                self._visit_call(node)

    def _str_list(self, node: ast.AST) -> Optional[List[str]]:
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self._name(e) for e in node.elts]
            return values if values and all(values) else None
        if isinstance(node, ast.Name):
            return self.list_consts.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):  # JUDGES + ["ErrorHandler"]
            left, right = self._str_list(node.left), self._str_list(node.right)
            return left + right if left is not None and right is not None else None
        return None

    def _name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, (ast.Name, ast.Attribute)):
            ident = _tail_name(node)
            if ident == "START":
                return START_NODE
            if ident == "END":
                return END_NODE
            return self.consts.get(ident, ident)
        return None

    def _visit_call(self, call: ast.Call):
        method = _tail_name(call.func)
        if method == "StateGraph":
            self.stategraph_calls.append(call)
            return
        if not isinstance(call.func, ast.Attribute):
            return
        args = call.args
        if method == "add_node" and args:
            if len(args) == 1 and isinstance(args[0], ast.Name) and args[0].id in self.funcs:
                self.nodes.add(args[0].id)
            elif self._name(args[0]):
                self.nodes.add(self._name(args[0]))
        elif method == "add_edge" and len(args) >= 2:
            dst = self._name(args[1])
            srcs = self._str_list(args[0]) if not isinstance(args[0], ast.Constant) else None
            if srcs and dst:
                self.join_edges.append((srcs, dst))
                self.static_edges.extend((s, dst) for s in srcs)
            elif self._name(args[0]) and dst:
                self.static_edges.append((self._name(args[0]), dst))
        elif method == "add_sequence" and args and isinstance(args[0], (ast.List, ast.Tuple)):
            seq = [self._name(e) for e in args[0].elts]
            self.nodes.update(s for s in seq if s)
            self.static_edges.extend((a, b) for a, b in zip(seq, seq[1:]) if a and b)
        elif method == "set_entry_point" and args and self._name(args[0]):
            self.static_edges.append((START_NODE, self._name(args[0])))
        elif method == "set_finish_point" and args and self._name(args[0]):
            self.static_edges.append((self._name(args[0]), END_NODE))
        elif method in ("add_conditional_edges", "set_conditional_entry_point") and args:
            if method == "set_conditional_entry_point":
                src, router, path_map = START_NODE, args[0], (args[1] if len(args) > 1 else None)
            else:
                src = self._name(args[0])
                router = args[1] if len(args) > 1 else next((k.value for k in call.keywords if k.arg == "path"), None)
                path_map = args[2] if len(args) > 2 else next((k.value for k in call.keywords if k.arg == "path_map"), None)
            self.conditionals.append(self._conditional(src, router, path_map))

    def _conditional(self, src: Optional[str], router: Optional[ast.AST], path_map: Optional[ast.AST]) -> Dict:
        mapping: Dict[str, str] = {}
        targets: Set[str] = set()
        if isinstance(path_map, ast.Dict):
            for k, v in zip(path_map.keys, path_map.values):
                kn, vn = (self._name(k) if k else None), self._name(v)
                if vn:
                    targets.add(vn)
                    if kn:
                        mapping[kn] = vn
        elif path_map is not None and self._str_list(path_map):
            targets |= set(self._str_list(path_map))
        else:
            path_map = None  # unresolvable map: infer targets from the router's return values

        # Inspect what the router can return: a single route, or a list of routes (parallel fan-out)
        fanout_lists: List[List[str]] = []
        uses_send = False
        body = None
        if isinstance(router, ast.Name) and router.id in self.funcs:
            body = self.funcs[router.id]
        elif isinstance(router, ast.Lambda):
            body = router
        if body is not None:
            returns = [r.value for r in ast.walk(body) if isinstance(r, ast.Return) and r.value is not None]
            if isinstance(body, ast.Lambda):
                returns.append(body.body)
            for ret in returns:
                if isinstance(ret, (ast.List, ast.Tuple)):
                    names = [mapping.get(n, n) for n in (self._name(e) for e in ret.elts) if n]
                    if len(names) >= 2:
                        fanout_lists.append(names)
                    if path_map is None:
                        targets |= set(names)
                elif isinstance(ret, ast.ListComp) and any(_tail_name(c.func) == "Send" for c in ast.walk(ret) if isinstance(c, ast.Call)):
                    uses_send = True
                elif path_map is None and self._name(ret):
                    targets.add(self._name(ret))
            # a module-level list constant returned by name, e.g. return JUDGES
            for ret in returns:
                names = self._str_list(ret) if isinstance(ret, ast.Name) else None
                if names and len(names) >= 2:
                    fanout_lists.append([mapping.get(n, n) for n in names])
                    targets |= {mapping.get(n, n) for n in names}
        return {
            "source": src,
            "router": ast.unparse(router) if router is not None else "?",
            "targets": sorted(targets),
            "fanout_lists": fanout_lists,
            "uses_send": uses_send,
        }


def _find_graph_file(repo_path: str) -> Optional[str]:
    preferred = _first_existing(repo_path, ["src/graph.py", "graph.py"])
    if preferred:
        return preferred
    best, best_edges = None, 0
    for f in _iter_py_files(repo_path):
        source = _read(f)
        if "StateGraph" in source:
            edges = source.count("add_edge") + source.count("add_conditional_edges")
            if edges >= best_edges:
                best, best_edges = f, edges
    return best


def analyze_graph_structure(path: str) -> Evidence:
    """
    Forensic Protocol B. Reconstructs the StateGraph topology from ``add_node`` /
    ``add_edge`` / ``add_conditional_edges`` calls (including list-returning routers and
    ``Send`` map-reduce) and verifies, structurally, that Detectives and Judges each fan out
    in parallel and fan in to a synchronization node. Keyword presence alone earns nothing.
    """
    graph_file = _find_graph_file(path)
    if not graph_file:
        return Evidence(goal="graph_orchestration", found=False, location="src/graph.py",
                        rationale="No module instantiating StateGraph was found.", confidence=1.0,
                        gaps=["No StateGraph definition exists in the repository"])

    tree, source = _parse(graph_file)
    location = _rel(graph_file, path)
    if tree is None:
        return Evidence(goal="graph_orchestration", found=False, location=location,
                        rationale=f"{location} has a syntax error; the graph cannot compile.", confidence=1.0,
                        gaps=[f"{location} does not parse (SyntaxError)"])

    g = _GraphExtractor(tree, source)
    gaps: List[str] = []
    if not g.stategraph_calls:
        gaps.append("No StateGraph(...) instantiation found by AST")

    out: Dict[str, Set[str]] = defaultdict(set)
    inc: Dict[str, Set[str]] = defaultdict(set)
    for s, d in g.static_edges:
        out[s].add(d)
        inc[d].add(s)
    for c in g.conditionals:
        for t in c["targets"]:
            out[c["source"]].add(t)
            inc[t].add(c["source"])

    # Parallel fan-out points: multiple unconditional edges fire together; a router returning
    # a list (or Send objects) dispatches its branches concurrently.
    fanouts: Dict[str, Set[str]] = defaultdict(set)
    static_out: Dict[str, Set[str]] = defaultdict(set)
    for s, d in g.static_edges:
        if d != END_NODE:
            static_out[s].add(d)
    for s, ds in static_out.items():
        if len(ds) >= 2:
            fanouts[s] |= ds
    for c in g.conditionals:
        for lst in c["fanout_lists"]:
            fanouts[c["source"]] |= set(lst)
        if c["uses_send"]:
            fanouts[c["source"]] |= set(c["targets"])
    # static edges + a conditional edge from the same node also run in parallel
    for c in g.conditionals:
        if static_out.get(c["source"]) and c["targets"]:
            fanouts[c["source"]] |= static_out[c["source"]] | set(c["targets"])

    def role(n: str, r: str) -> bool:
        return bool(n) and n not in (START_NODE, END_NODE) and bool(_ROLE[r].search(n))

    def fanout_of(r: str) -> Optional[Tuple[str, List[str]]]:
        for src, ts in fanouts.items():
            members = sorted(t for t in ts if role(t, r) and not (r == "detective" and role(t, "judge")))
            if len(members) >= 2:
                return src, members
        return None

    def fanin_of(r: str) -> Optional[Tuple[str, List[str]]]:
        for dst, srcs in inc.items():
            members = sorted(s for s in srcs if role(s, r) and not (r == "detective" and role(s, "judge")))
            if len(members) >= 2 and dst != END_NODE:
                return dst, members
        return None

    det_fanout, judge_fanout = fanout_of("detective"), fanout_of("judge")
    det_fanin, judge_fanin = fanin_of("detective"), fanin_of("judge")
    error_routes = sorted({t for c in g.conditionals for t in c["targets"] if role(t, "error")})

    notes: List[str] = []
    if g.stategraph_calls:
        notes.append(f"StateGraph instantiated in {location} with {len(g.nodes)} nodes and {len(g.static_edges)} static edges")
    if not fanouts:
        gaps.append("ORCHESTRATION FRAUD: no node fans out to parallel branches; the graph is purely linear")
    if det_fanout:
        notes.append(f"Detectives fan out in parallel from '{det_fanout[0]}' -> {det_fanout[1]}")
    else:
        gaps.append("Detectives do not fan out in parallel from a single node")
    if det_fanin:
        notes.append(f"'{det_fanin[0]}' synchronizes (fan-in) evidence from {det_fanin[1]}")
    else:
        gaps.append("No fan-in synchronization node collects Detective evidence before the Judges")
    if judge_fanout:
        notes.append(f"Judges fan out in parallel from '{judge_fanout[0]}' -> {judge_fanout[1]}")
    else:
        gaps.append("Judges (Prosecutor/Defense/TechLead) do not fan out in parallel")
    if judge_fanin:
        notes.append(f"Judges fan in to '{judge_fanin[0]}'")
    else:
        gaps.append("Judge opinions are not joined into a single synthesis node (e.g. ChiefJustice)")
    if det_fanin and judge_fanout and det_fanin[0] != judge_fanout[0]:
        # the judges must be dispatched downstream of the evidence aggregator
        frontier, seen = [det_fanin[0]], set()
        while frontier:
            n = frontier.pop()
            if n in seen:
                continue
            seen.add(n)
            frontier.extend(out.get(n, ()))
        if judge_fanout[0] not in seen:
            gaps.append("Judges are dispatched before evidence aggregation completes")
    if error_routes:
        notes.append(f"Conditional edges route failures to {error_routes}")
    elif g.conditionals:
        gaps.append("Conditional edges exist but none routes 'Evidence Missing' / 'Node Failure' to a handler node")
    else:
        gaps.append("No conditional edges: 'Evidence Missing' / 'Node Failure' scenarios are not handled")

    # Capture the graph-building block
    parents = _enclosing_functions(tree)
    block = g.stategraph_calls and parents.get(g.stategraph_calls[0])
    edges_text = "\n".join(
        [f"{s} -> {d}" for s, d in g.static_edges]
        + [f"{c['source']} -?{c['router']}-> {c['targets']}" + (f" (parallel: {c['fanout_lists']})" if c["fanout_lists"] else "")
           for c in g.conditionals]
    )
    content = f"# Edges reconstructed from AST\n{edges_text}\n\n# Graph definition\n{_segment(source, block) if block else source[:1500]}"

    found = bool(g.stategraph_calls and det_fanout and det_fanin and judge_fanout and judge_fanin)
    return Evidence(goal="graph_orchestration", found=found, content=content, location=location,
                    rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps)


# Backwards-compatible name
analyze_graph_orchestration = analyze_graph_structure


# ---------------------------------------------------------------------------
# Safe Tool Engineering (Phase 2)
# ---------------------------------------------------------------------------

def _is_dynamic(node: Optional[ast.AST]) -> bool:
    """True when a command argument is built from runtime values (f-string, concat, variable)."""
    if node is None or isinstance(node, ast.Constant):
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(not isinstance(e, ast.Constant) for e in node.elts)
    return True


def _q_endswith(call: ast.Call, aliases: Dict[str, str], suffix: str) -> bool:
    return (_qualname(call.func, aliases) or "").endswith(suffix)


def _scan_security(tree: ast.Module) -> List[str]:
    """Returns security violations: os.system/os.popen anywhere, or subprocess with shell=True."""
    aliases = _import_aliases(tree)
    violations = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        q = _qualname(call.func, aliases) or ""
        arg0 = call.args[0] if call.args else None
        if q in ("os.system", "os.popen") or q.startswith("commands."):
            kind = "with unsanitized dynamic input" if _is_dynamic(arg0) else "with a constant command"
            violations.append(f"raw {q}() {kind} (line {call.lineno})")
        elif q.startswith("subprocess.") and any(
            k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords
        ):
            if _is_dynamic(arg0):
                violations.append(f"{q}(..., shell=True) with dynamic input (line {call.lineno})")
    return violations


_EXEC_SUFFIXES = ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
                  "subprocess.check_output", "os.system", "os.popen")


def _is_git_clone_call(call: ast.Call, aliases: Dict[str, str], fn: Optional[ast.AST]) -> bool:
    """True for a call that actually executes ``git clone`` (subprocess/os/GitPython), as opposed
    to a string that merely mentions it. A command held in a local variable is resolved."""
    q = _qualname(call.func, aliases) or ""
    if q.endswith("clone_from"):
        return True
    if not q.endswith(_EXEC_SUFFIXES) or not call.args:
        return False
    arg = call.args[0]
    if isinstance(arg, ast.Name) and fn is not None:
        assigned = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == arg.id for t in n.targets)]
        arg = assigned[0] if assigned else arg
    consts = [n.value for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return "clone" in consts or any("git clone" in c for c in consts)


def analyze_safe_tool_engineering(repo_path: str) -> Evidence:
    """
    Scans ``src/tools/`` for the repository-clone function and verifies sandboxing
    (tempfile), list-form subprocess calls with error handling, URL sanitization, and the
    absence of ``os.system`` / ``shell=True``. Security violations anywhere under ``src/`` are
    reported because a shell-injection hole outside tools is just as exploitable.
    """
    tools_rel = next((d for d in ("src/tools", "tools") if os.path.isdir(os.path.join(repo_path, d))), None)
    gaps: List[str] = []
    notes: List[str] = []
    if not tools_rel:
        gaps.append("src/tools/ directory does not exist")

    violations: List[str] = []
    for f in _iter_py_files(repo_path):
        tree, _ = _parse(f)
        if tree is not None:
            violations.extend(f"{_rel(f, repo_path)}: {v}" for v in _scan_security(tree))

    clone_fn_src, clone_loc = None, None
    uses_tempfile = False
    clone_checks = {"error_handling": False, "captures_output": False, "timeout": False, "sanitized": False}
    scan_root = tools_rel or ("src" if os.path.isdir(os.path.join(repo_path, "src")) else "")
    for f in _iter_py_files(repo_path, scan_root):
        tree, source = _parse(f)
        if tree is None:
            continue
        aliases = _import_aliases(tree)
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            q = _qualname(call.func, aliases) or ""
            if q in ("tempfile.TemporaryDirectory", "tempfile.mkdtemp"):
                uses_tempfile = True
        parents = _enclosing_functions(tree)
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            if clone_fn_src is None and call in parents and _is_git_clone_call(call, aliases, parents[call]):
                fn = parents[call]
                clone_fn_src, clone_loc = _segment(source, fn), f"{_rel(f, repo_path)}::{fn.name}"
                fn_nodes = list(ast.walk(fn))
                clone_checks["error_handling"] = (
                    any(isinstance(n, ast.Try) for n in fn_nodes)
                    or any(isinstance(n, ast.Attribute) and n.attr == "returncode" for n in fn_nodes)
                    or any(k.arg == "check" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)
                )
                clone_checks["captures_output"] = any(
                    k.arg in ("capture_output", "stdout", "stderr") for k in call.keywords
                ) or _q_endswith(call, aliases, "clone_from")
                clone_checks["timeout"] = any(k.arg == "timeout" for k in call.keywords)
                fn_text = clone_fn_src.lower()
                clone_checks["sanitized"] = (
                    "validat" in fn_text or "sanitiz" in fn_text
                    or any(isinstance(n, ast.Constant) and n.value == "--" for n in ast.walk(call))
                )

    if uses_tempfile:
        notes.append("Clones are sandboxed with tempfile.TemporaryDirectory()/mkdtemp()")
    else:
        gaps.append("No tempfile.TemporaryDirectory() sandbox: clones may land in the live working directory")
    if clone_fn_src:
        notes.append(f"Clone function located at {clone_loc}")
        if clone_checks["error_handling"]:
            notes.append("clone checks returncode / handles exceptions")
        else:
            gaps.append("Clone call has no error handling (no try/except, returncode check or check=True)")
        if clone_checks["captures_output"]:
            notes.append("stdout/stderr captured")
        else:
            gaps.append("Clone call does not capture stdout/stderr")
        if not clone_checks["timeout"]:
            gaps.append("Clone call has no timeout: a hanging remote stalls the whole swarm")
        if not clone_checks["sanitized"]:
            gaps.append("Repo URL reaches git without validation or a '--' separator (option-injection risk)")
    else:
        gaps.append("No git clone function found in the tools package")

    if violations:
        gaps.insert(0, "SECURITY VIOLATION: " + "; ".join(violations))
    else:
        notes.append("No os.system / shell=True calls (AST-verified)")

    found = uses_tempfile and clone_fn_src is not None and clone_checks["error_handling"] and not violations
    return Evidence(goal="safe_tool_engineering", found=found,
                    content=clone_fn_src or "(no clone function found)",
                    location=clone_loc or (tools_rel or "src/tools/"),
                    rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps)


# ---------------------------------------------------------------------------
# Structured Output (Phase 3) and Judicial Nuance
# ---------------------------------------------------------------------------

_PERSONA_KEYS = {
    "Prosecutor": re.compile(r"prosecut", re.I),
    "Defense": re.compile(r"defen[cs]e", re.I),
    "TechLead": re.compile(r"tech_?lead", re.I),
}
_PERSONA_LEXICON = {
    "Prosecutor": ("trust no one", "harsh", "scrutin", "flaw", "gap", "penal", "charge", "violation", "lazi"),
    "Defense": ("effort", "intent", "spirit", "generous", "forgiv", "reward", "credit", "strength"),
    "TechLead": ("maintainab", "pragmatic", "technical debt", "architect", "tie-breaker", "viab", "remediation"),
}


def _find_judges_file(repo_path: str) -> Optional[str]:
    preferred = _first_existing(repo_path, ["src/nodes/judges.py", "src/judges.py", "judges.py"])
    if preferred:
        return preferred
    for f in _iter_py_files(repo_path):
        text = _read(f)
        if "Prosecutor" in text and ("invoke" in text or "with_structured_output" in text):
            return f
    return None


def _extract_persona_prompts(tree: ast.Module, source: str) -> Dict[str, str]:
    prompts: Dict[str, str] = {}

    def text_of(v: ast.AST) -> Optional[str]:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        if isinstance(v, ast.JoinedStr):
            return "".join(p.value for p in v.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and "PROMPT" in t.id.upper():
                    txt = text_of(node.value)
                    if txt:
                        for persona, pat in _PERSONA_KEYS.items():
                            if pat.search(t.id):
                                prompts.setdefault(persona, txt)
                if isinstance(node.value, ast.Dict):
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(k.value, str) and text_of(v) and len(text_of(v)) > 80:
                            for persona, pat in _PERSONA_KEYS.items():
                                if pat.search(k.value):
                                    prompts.setdefault(persona, text_of(v))
    return prompts


def _similarity(a: str, b: str) -> float:
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio() if wa and wb else 0.0


def analyze_structured_output(repo_path: str) -> Evidence:
    """Verifies Judges call ``.with_structured_output(JudicialOpinion)`` / ``.bind_tools`` and
    retry instead of accepting free text. Captures the code block that queries the Judge LLM."""
    judges_file = _find_judges_file(repo_path)
    if not judges_file:
        return Evidence(goal="structured_output_enforcement", found=False, location="src/nodes/judges.py",
                        rationale="No judges module found.", confidence=1.0,
                        gaps=["src/nodes/judges.py does not exist"])
    tree, source = _parse(judges_file)
    location = _rel(judges_file, repo_path)
    if tree is None:
        return Evidence(goal="structured_output_enforcement", found=False, location=location,
                        rationale=f"{location} does not parse.", confidence=1.0, gaps=[f"{location} has a SyntaxError"])

    parents = _enclosing_functions(tree)
    bound_schemas: List[str] = []
    query_fn = None
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        method = _tail_name(call.func)
        if method == "with_structured_output" and call.args:
            bound_schemas.append(ast.unparse(call.args[0]))
            query_fn = query_fn or parents.get(call)
        elif method == "bind_tools" and call.args:
            bound_schemas.append(ast.unparse(call.args[0]))
            query_fn = query_fn or parents.get(call)

    has_retry = any(_tail_name(c.func) == "with_retry" for c in ast.walk(tree) if isinstance(c, ast.Call))
    has_retry |= any(
        isinstance(d, (ast.Name, ast.Attribute, ast.Call)) and "retry" in ast.unparse(d).lower()
        for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in fn.decorator_list
    )
    for loop in (n for n in ast.walk(tree) if isinstance(n, (ast.For, ast.While))):
        for t in (n for n in ast.walk(loop) if isinstance(n, ast.Try)):
            if any(isinstance(c, ast.Call) and _tail_name(c.func) in ("invoke", "ainvoke") for c in ast.walk(t)):
                has_retry = True
    parses_free_text = any(
        isinstance(c, ast.Call) and _qualname(c.func, _import_aliases(tree)) in ("json.loads", "re.search", "re.findall")
        and "content" in ast.unparse(c)
        for c in ast.walk(tree)
    )

    gaps: List[str] = []
    notes: List[str] = []
    schema_ok = any("JudicialOpinion" in s or "Opinion" in s for s in bound_schemas)
    if schema_ok:
        notes.append(f"Judge LLM bound to Pydantic schema via structured output ({', '.join(sorted(set(bound_schemas)))})")
    elif bound_schemas:
        gaps.append(f"Structured output bound to {bound_schemas} rather than the JudicialOpinion schema")
    else:
        gaps.append("HALLUCINATION LIABILITY: judges never call .with_structured_output() / .bind_tools()")
    if has_retry:
        notes.append("Retry loop re-queries the judge on malformed / free-text output")
    else:
        gaps.append("No retry when a judge returns free text or fails validation")
    if parses_free_text:
        gaps.append("Judge responses are parsed from free text (json.loads / regex on .content)")

    return Evidence(goal="structured_output_enforcement", found=schema_ok,
                    content=_segment(source, query_fn) if query_fn else source[:800],
                    location=f"{location}::{query_fn.name}" if query_fn else location,
                    rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps)


def analyze_judicial_nuance(repo_path: str) -> Evidence:
    """Extracts the three persona system prompts and measures how distinct they are (word-level
    similarity), whether each carries its persona's philosophy, and whether per-criterion
    judicial_logic from the rubric is injected."""
    judges_file = _find_judges_file(repo_path)
    if not judges_file:
        return Evidence(goal="judicial_nuance", found=False, location="src/nodes/judges.py",
                        rationale="No judges module found.", confidence=1.0,
                        gaps=["No Prosecutor/Defense/TechLead personas exist (monolithic grader)"])
    tree, source = _parse(judges_file)
    location = _rel(judges_file, repo_path)
    if tree is None:
        return Evidence(goal="judicial_nuance", found=False, location=location,
                        rationale=f"{location} does not parse.", confidence=1.0, gaps=[f"{location} has a SyntaxError"])

    prompts = _extract_persona_prompts(tree, source)
    # Prompts may also live in separate template files
    for d in ("src/prompts", "prompts", "src/nodes/prompts"):
        full = os.path.join(repo_path, d)
        if os.path.isdir(full):
            for name in sorted(os.listdir(full)):
                for persona, pat in _PERSONA_KEYS.items():
                    if pat.search(name):
                        prompts.setdefault(persona, _read(os.path.join(full, name)))

    gaps: List[str] = []
    notes: List[str] = []
    missing = [p for p in _PERSONA_KEYS if p not in prompts]
    if missing:
        gaps.append(f"No distinct system prompt found for: {', '.join(missing)}")

    sims: Dict[str, float] = {}
    personas = [p for p in _PERSONA_KEYS if p in prompts]
    for i, a in enumerate(personas):
        for b in personas[i + 1:]:
            sims[f"{a}/{b}"] = _similarity(prompts[a], prompts[b])
    if sims:
        worst_pair, worst = max(sims.items(), key=lambda kv: kv[1])
        sim_text = ", ".join(f"{k}={v:.0%}" for k, v in sims.items())
        if worst > 0.9:
            gaps.append(f"PERSONA COLLUSION: {worst_pair} prompts are {worst:.0%} identical")
        elif worst > 0.5:
            gaps.append(f"High prompt overlap ({worst_pair} {worst:.0%}): personas may not genuinely disagree")
        else:
            notes.append(f"Persona prompts are distinct (pairwise word overlap: {sim_text})")

    for persona in personas:
        text = prompts[persona].lower()
        hits = [w for w in _PERSONA_LEXICON[persona] if w in text]
        if len(hits) >= 2:
            notes.append(f"{persona} prompt carries its philosophy ({', '.join(hits[:3])})")
        else:
            gaps.append(f"{persona} prompt lacks its persona-specific instructions")

    if "judicial_logic" in source:
        notes.append("Per-criterion judicial_logic from the rubric is injected into persona prompts")
    else:
        gaps.append("Persona prompts are static: the rubric's per-criterion judicial_logic is not injected")
    if "criterion_id" not in source:
        gaps.append("Opinions are not mapped back to rubric criterion IDs")

    found = not missing and sims and max(sims.values()) <= 0.5 and not any("lacks" in g for g in gaps)
    content = "\n\n".join(f"[{p}] {prompts[p][:500]}" for p in personas) or source[:800]
    return Evidence(goal="judicial_nuance", found=bool(found), content=content, location=location,
                    rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps)


def analyze_structured_output_and_judges(repo_path: str) -> List[Evidence]:
    """Backwards-compatible wrapper returning both judge-related evidences."""
    return [analyze_structured_output(repo_path), analyze_judicial_nuance(repo_path)]


# ---------------------------------------------------------------------------
# Chief Justice Synthesis
# ---------------------------------------------------------------------------

_LLM_CLASSES = re.compile(r"Chat(OpenAI|GoogleGenerativeAI|Anthropic|Ollama|VertexAI|Groq|Mistral)")


def analyze_chief_justice_node(repo_path: str) -> Evidence:
    """
    AST-verifies that conflict resolution is deterministic Python (named rules, numeric caps,
    variance threshold) rather than an LLM prompt or a plain average, and that the node emits
    a structured Markdown report.
    """
    justice_file = _first_existing(repo_path, ["src/nodes/justice.py", "src/justice.py", "justice.py"])
    if not justice_file:
        for f in _iter_py_files(repo_path):
            if re.search(r"def\s+\w*(chief|justice)\w*\s*\(", _read(f), re.I):
                justice_file = f
                break
    if not justice_file:
        return Evidence(goal="chief_justice_synthesis", found=False, location="src/nodes/justice.py",
                        rationale="No Chief Justice synthesis node found.", confidence=1.0,
                        gaps=["src/nodes/justice.py (ChiefJusticeNode) does not exist"])

    tree, source = _parse(justice_file)
    location = _rel(justice_file, repo_path)
    if tree is None:
        return Evidence(goal="chief_justice_synthesis", found=False, location=location,
                        rationale=f"{location} does not parse.", confidence=1.0, gaps=[f"{location} has a SyntaxError"])

    nodes = list(ast.walk(tree))
    low = source.lower()
    # Resolve module-level numeric constants such as SECURITY_CAP = 3 / VARIANCE_THRESHOLD = 2
    int_consts = {
        t.id: n.value.value
        for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, (int, float))
        for t in n.targets if isinstance(t, ast.Name)
    }

    def num(node: ast.AST) -> Optional[float]:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            return int_consts.get(node.id)
        return None

    if_count = sum(isinstance(n, ast.If) for n in nodes)
    llm_used = bool(_LLM_CLASSES.search(source)) or any(
        isinstance(n, ast.Call) and _tail_name(n.func) in ("invoke", "ainvoke") for n in nodes
    )
    # A cap at 3 is either min(x, 3) or `if x > 3: x = 3`
    caps_at_3 = any(
        isinstance(n, ast.Call) and _tail_name(n.func) == "min" and any(num(a) == 3 for a in n.args)
        for n in nodes
    ) or any(
        isinstance(n, ast.If) and isinstance(n.test, (ast.Compare, ast.BoolOp))
        and any(isinstance(c, ast.Compare) and any(num(x) == 3 for x in c.comparators) for c in ast.walk(n.test))
        and any(isinstance(s, ast.Assign) and num(s.value) == 3 for s in n.body)
        for n in nodes
    )
    variance_threshold = any(
        isinstance(n, ast.Compare) and any(isinstance(op, (ast.Gt, ast.GtE)) for op in n.ops)
        and any(num(c) in (2, 3) for c in n.comparators)
        and re.search(r"varian|spread|max\(", ast.unparse(n), re.I)
        for n in nodes
    )
    reads_evidence = any(isinstance(n, ast.Attribute) and n.attr == "found" for n in nodes)
    md_sections = all(s in source for s in ("Executive Summary", "Remediation Plan"))

    rules = {
        "Rule of Security (cap at 3)": caps_at_3 and "security" in low,
        "Rule of Evidence / Fact Supremacy": reads_evidence and ("fact" in low or "evidence" in low),
        "Rule of Functionality (Tech Lead weight)": "functionality" in low or ("techlead" in low and "architect" in low),
        "Variance > 2 re-evaluation": variance_threshold,
        "Dissent summary": "dissent" in low,
    }
    notes = [f"{if_count} deterministic if-branches in {location}"]
    gaps: List[str] = []
    for name, ok in rules.items():
        (notes if ok else gaps).append(f"{name} {'implemented' if ok else 'missing'}")
    if llm_used and sum(rules.values()) < 3:
        gaps.insert(0, "Synthesis delegated to an LLM prompt instead of hardcoded rules")
    elif llm_used:
        notes.append("LLM used only alongside deterministic rules")
    if if_count < 3 and not llm_used:
        gaps.insert(0, "Synthesis looks like a plain average (fewer than 3 decision branches)")
    if md_sections:
        notes.append("Emits Markdown with Executive Summary and Remediation Plan sections")
    else:
        gaps.append("Final report lacks Executive Summary -> Criterion Breakdown -> Remediation Plan structure")

    target = next((n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and re.search(r"chief|justice", n.name, re.I)), None)
    found = if_count >= 3 and sum(rules.values()) >= 4 and md_sections
    return Evidence(goal="chief_justice_synthesis", found=found,
                    content=_segment(source, target) if target else source[:1500],
                    location=f"{location}::{target.name}" if target else location,
                    rationale="; ".join(notes + gaps), confidence=1.0, gaps=gaps)

"""Read-only tool box for the delegated model.

Security contract (see PLAN.md 5.4/5.5):
  * Every path is resolved (symlinks followed) and must stay inside the workdir AND inside the
    per-call allow-list (`files`). Secret globs are refused everywhere.
  * The model never writes. `propose_edit(path, old, new)` only records a proposal; the host
    validates (allowed path, file read in this session and unchanged since, `old` occurs exactly
    once) and renders the unified diff itself. The main Claude session reviews and applies.
"""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_GREP_RESULTS = 200
MAX_GLOB_RESULTS = 500
BINARY_SNIFF = 4096


class ToolError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF]


@dataclass
class Proposal:
    path: str
    old: str
    new: str
    reason: str = ""


@dataclass
class ToolBox:
    root: Path
    allow: list[str]
    secret_globs: list[str]
    read_page_chars: int = 12000
    seen: dict[str, str] = field(default_factory=dict)       # rel path -> sha at read time
    proposals: list[Proposal] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        # allow-list entries resolved once; a missing entry is kept as a glob pattern
        self._allow_paths: list[Path] = []
        self._allow_globs: list[str] = []
        for a in self.allow:
            p = (self.root / a).resolve()
            if p.exists():
                self._allow_paths.append(p)
            else:
                self._allow_globs.append(a.replace("\\", "/"))

    # ---------- path policy ----------
    def _rel(self, p: Path) -> str:
        return PurePosixPath(p.relative_to(self.root).as_posix()).as_posix()

    def _is_secret(self, rel: str) -> bool:
        name = PurePosixPath(rel).name
        for g in self.secret_globs:
            if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g):
                return True
            # ".git/**" style: any segment named .git
            if g.endswith("/**") and g[:-3] in PurePosixPath(rel).parts:
                return True
        return False

    def _allowed(self, p: Path, rel: str) -> bool:
        if not self.allow:
            return False
        for ap in self._allow_paths:
            if p == ap or ap in p.parents:
                return True
        return any(fnmatch.fnmatch(rel, g) for g in self._allow_globs)

    def resolve(self, path: str, must_exist: bool = True) -> tuple[Path, str]:
        raw = Path(path)
        p = (raw if raw.is_absolute() else self.root / raw).resolve()  # follows symlinks/junctions
        try:
            rel = self._rel(p)
        except ValueError:
            raise ToolError(f"path outside workdir: {path}")
        if self._is_secret(rel):
            raise ToolError(f"refused (secret pattern): {rel}")
        if not self._allowed(p, rel):
            raise ToolError(f"not in allow-list: {rel}")
        if must_exist and not p.exists():
            raise ToolError(f"not found: {rel}")
        return p, rel

    def _iter_allowed_files(self):
        seen = set()
        for ap in self._allow_paths:
            files = [ap] if ap.is_file() else [f for f in ap.rglob("*") if f.is_file()]
            for f in files:
                fr = f.resolve()
                if fr in seen:
                    continue
                seen.add(fr)
                try:
                    rel = self._rel(fr)
                except ValueError:
                    continue
                if self._is_secret(rel):
                    continue
                yield fr, rel
        if self._allow_globs:
            for f in self.root.rglob("*"):
                if not f.is_file():
                    continue
                fr = f.resolve()
                if fr in seen:
                    continue
                try:
                    rel = self._rel(fr)
                except ValueError:
                    continue
                if self._is_secret(rel) or not any(fnmatch.fnmatch(rel, g) for g in self._allow_globs):
                    continue
                seen.add(fr)
                yield fr, rel

    # ---------- tools ----------
    def read_file(self, path: str, offset: int = 0) -> dict:
        p, rel = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"not a file: {rel}")
        data = p.read_bytes()
        if _is_binary(data):
            raise ToolError(f"binary file refused: {rel}")
        self.seen[rel] = _sha(data)
        text = data.decode("utf-8", "replace")
        offset = max(0, int(offset or 0))
        chunk = text[offset: offset + self.read_page_chars]
        nxt = offset + len(chunk)
        out = {"path": rel, "offset": offset, "content": chunk, "total_chars": len(text)}
        if nxt < len(text):
            out["next_offset"] = nxt
            out["note"] = f"truncated; call read_file again with offset={nxt}"
        return out

    def grep(self, pattern: str, path_glob: str = "*", max_results: int = MAX_GREP_RESULTS) -> dict:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regex: {e}")
        results = []
        for f, rel in self._iter_allowed_files():
            if path_glob and path_glob != "*" and not fnmatch.fnmatch(rel, path_glob):
                continue
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if _is_binary(data):
                continue
            for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    results.append({"path": rel, "line": i, "text": line[:300]})
                    if len(results) >= max_results:
                        return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def glob(self, pattern: str) -> dict:
        out = []
        for _, rel in self._iter_allowed_files():
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(PurePosixPath(rel).name, pattern):
                out.append(rel)
                if len(out) >= MAX_GLOB_RESULTS:
                    return {"paths": out, "truncated": True}
        return {"paths": sorted(out), "truncated": False}

    def propose_edit(self, path: str, old: str, new: str, reason: str = "") -> dict:
        p, rel = self.resolve(path)
        if rel not in self.seen:
            raise ToolError(f"read_file first: {rel}")
        data = p.read_bytes()
        if _sha(data) != self.seen[rel]:
            raise ToolError(f"file changed since read: {rel} (read it again)")
        if not old:
            raise ToolError("old must be non-empty")
        if old == new:
            raise ToolError("old and new are identical")
        text = data.decode("utf-8", "replace")
        # apply earlier proposals for this file so later ones see the updated text
        current = self._apply(text, [q for q in self.proposals if q.path == rel])
        n = current.count(old)
        if n == 0:
            raise ToolError("old text not found (must match exactly, including whitespace)")
        if n > 1:
            raise ToolError(f"old text matches {n} times; include more context to make it unique")
        self.proposals.append(Proposal(rel, old, new, reason))
        return {"ok": True, "path": rel, "proposals_for_file": len([q for q in self.proposals if q.path == rel])}

    # ---------- host-side rendering ----------
    @staticmethod
    def _apply(text: str, props: list[Proposal]) -> str:
        for q in props:
            text = text.replace(q.old, q.new, 1)
        return text

    @staticmethod
    def _unified(a: str, b: str, rel: str) -> str:
        """difflib output made git-valid: handles files without a trailing newline."""
        out = []
        for line in difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                         fromfile=f"a/{rel}", tofile=f"b/{rel}"):
            if not line.endswith("\n"):
                line += "\n\\ No newline at end of file\n"
            out.append(line)
        return "".join(out)

    def render_diffs(self) -> list[dict]:
        """Re-validate every proposal against the CURRENT file (path policy + hash), then render.
        A file that changed since it was read yields an error entry, never a silent empty diff."""
        out = []
        for rel in sorted({q.path for q in self.proposals}):
            props = [q for q in self.proposals if q.path == rel]
            entry = {"path": rel, "edits": len(props), "diff": "", "reasons": [q.reason for q in props if q.reason]}
            try:
                p, rel2 = self.resolve(rel)          # allow-list / secret / symlink re-check
                data = p.read_bytes()
                if rel2 != rel or _sha(data) != self.seen.get(rel):
                    raise ToolError("file changed since it was read; proposals discarded")
                text = data.decode("utf-8", "replace")
                new = self._apply(text, props)
                if new == text:
                    raise ToolError("proposals no longer match the file content")
                entry["diff"] = self._unified(text, new, rel)
            except (ToolError, OSError) as e:
                entry["error"] = str(e)
            out.append(entry)
        return out

    def git_apply_check(self, diffs: list[dict]) -> None:
        """Best-effort `git apply --check` inside the workdir; annotates each entry in place."""
        import subprocess
        import tempfile
        for d in diffs:
            if not d.get("diff"):
                continue
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8", newline="\n") as f:
                    f.write(d["diff"])
                    tmp = f.name
                r = subprocess.run(["git", "-C", str(self.root), "apply", "--check", tmp],
                                   capture_output=True, text=True, timeout=30)
                d["apply_check"] = {"rc": r.returncode, "stderr": r.stderr.strip()[-300:]}
            except (OSError, subprocess.SubprocessError) as e:
                d["apply_check"] = {"rc": None, "stderr": f"git not available: {e}"}
            finally:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except Exception:
                    pass

    # ---------- dispatch ----------
    def call(self, name: str, args: dict) -> dict:
        fn = {"read_file": self.read_file, "grep": self.grep, "glob": self.glob, "propose_edit": self.propose_edit}.get(name)
        if fn is None:
            raise ToolError(f"unknown tool: {name}")
        try:
            res = fn(**args)
            self.calls.append({"tool": name, "ok": True})
            return res
        except TypeError as e:
            self.calls.append({"tool": name, "ok": False})
            raise ToolError(f"bad arguments for {name}: {e}")
        except ToolError:
            self.calls.append({"tool": name, "ok": False})
            raise


OPENAI_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file from the allowed set (paged). Always read before propose_edit.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "character offset for paging", "default": 0}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Regex search over the allowed files. Returns path, line number and text.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path_glob": {"type": "string", "default": "*"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "glob",
        "description": "List allowed files matching a glob pattern (e.g. '**/*.py').",
        "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "propose_edit",
        "description": ("Propose replacing `old` with `new` in a file you have read. `old` must match exactly once. "
                        "The file is NOT modified; the host renders a diff for human review. "
                        "Make several small proposals rather than one huge replacement."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
            "reason": {"type": "string", "default": ""}},
            "required": ["path", "old", "new"]}}},
]

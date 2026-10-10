"""Faz 3: bir gorevin diff'ini GECICI git worktree'de uygula, derle, gorev-ozel kontrolleri kos.
Gercek repo DEGISMEZ. Kullanim: verify_task.py <task_id> <repo_dir> <check: docstrings|future|hints>
"""
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

task_id, repo, check = sys.argv[1:4]
RES = Path(__file__).resolve().parent / "results"
diff = RES / f"{task_id}.diff"
out = {"task_id": task_id, "check": check}
wt = Path(tempfile.mkdtemp(prefix=f"cd_wt_{task_id}_"))
subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", "-q", str(wt), "HEAD"], check=True)
try:
    ap = subprocess.run(["git", "-C", str(wt), "apply", "--whitespace=nowarn", str(diff)], capture_output=True, text=True)
    out["apply"] = {"rc": ap.returncode, "stderr": ap.stderr[-300:]}
    if ap.returncode == 0:
        changed = subprocess.run(["git", "-C", str(wt), "diff", "--name-only"], capture_output=True, text=True).stdout.split()
        out["changed_files"] = changed
        comp = []
        for f in changed:
            if f.endswith(".py"):
                r = subprocess.run([sys.executable, "-m", "py_compile", str(wt / f)], capture_output=True, text=True)
                comp.append({"file": f, "rc": r.returncode, "err": r.stderr[-200:]})
        out["py_compile"] = comp
        details = {}
        for f in changed:
            if not f.endswith(".py"):
                continue
            src = (wt / f).read_text(encoding="utf-8")
            tree = ast.parse(src)
            if check == "docstrings":
                fs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
                details[f] = {"public": len(fs), "missing_docstring": [n.name for n in fs if not ast.get_docstring(n)]}
            elif check == "future":
                # ast: first non-docstring statement must be the __future__ import
                body = tree.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                    body = body[1:]
                first = body[0] if body else None
                ok = isinstance(first, ast.ImportFrom) and first.module == "__future__" and any(a.name == "annotations" for a in first.names)
                details[f] = {"future_first": ok}
            elif check == "typing":
                details[f] = {"typing_generics_left": [w for w in ("List[", "Dict[", "Tuple[", "Optional[") if w in src],
                              "typing_import_left": any(isinstance(n, ast.ImportFrom) and n.module == "typing" for n in tree.body),
                              "functions": len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)])}
        out["details"] = details
        # repo-wide import smoke of touched modules (no DB needed for these pure modules)
        imp = []
        for f in changed:
            if f.endswith(".py") and "/" in f:
                mod = f[:-3].replace("/", ".")
                r = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=str(wt), capture_output=True, text=True)
                imp.append({"module": mod, "rc": r.returncode, "err": r.stderr.strip().splitlines()[-1][:200] if r.returncode else ""})
        out["import_check"] = imp
finally:
    subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", str(wt)], capture_output=True)


(RES / f"{task_id}.verify.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(out, indent=2, ensure_ascii=False))

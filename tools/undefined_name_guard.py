"""Dependency-free detector for the NameError-on-a-rare-path defect class.

Bujji has shipped this bug to production twice:
  * 2026-08-21 09:54  emergency close raised NameError: 'PositionHealthThresholds'
  * (earlier)         the exit funnel raised NameError on an unbound 'execution'

Both were on safety paths that only execute when something has already gone
wrong -- so the regression suite never touched them and the bug waited for the
worst possible moment. This finds them statically.
"""
import ast, builtins, symtable, sys, os

BUILTINS = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__class__",
}


class _Scope:
    def __init__(self, node, table):
        self.node, self.table = node, table


def _own_load_names(fn_node):
    """Load-context Names lexically owned by fn_node (not by a nested scope)."""
    out = []

    def walk(node, top=False):
        for child in ast.iter_child_nodes(node):
            if not top and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                              ast.Lambda, ast.ClassDef)):
                continue  # nested scope owns it
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append(child)
            walk(child)

    for child in ast.iter_child_nodes(fn_node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            out.append(child)
        walk(child)
    return out


def _annotation_spots(tree):
    spots = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign) and node.annotation:
            targets.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in (list(a.args) + list(a.kwonlyargs)
                        + list(getattr(a, "posonlyargs", []))
                        + [x for x in (a.vararg, a.kwarg) if x]):
                if arg.annotation:
                    targets.append(arg.annotation)
            if node.returns:
                targets.append(node.returns)
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Name):
                    spots.add((sub.lineno, sub.col_offset))
    return spots


def check(path):
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src, path)
        top = symtable.symtable(src, path, "exec")
    except SyntaxError as exc:
        return [(getattr(exc, "lineno", 0) or 0, 0, f"SYNTAX ERROR: {exc}")]

    # A star-import makes the module namespace unknowable: refuse to judge.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []

    future_ann = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names) for n in tree.body)
    ann_spots = _annotation_spots(tree) if future_ann else set()

    module_names = set(top.get_identifiers())
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            module_names.update(node.names)

    # Pair every symtable function scope with its ast node, by (name, lineno).
    tables = {}

    def collect(t):
        tables[(t.get_name(), t.get_lineno())] = t
        for c in t.get_children():
            collect(c)

    collect(top)

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        table = tables.get((node.name, node.lineno))
        if table is None:
            continue
        try:
            syms = {s.get_name(): s for s in table.get_symbols()}
        except Exception:
            continue
        for name_node in _own_load_names(node):
            name = name_node.id
            if (name_node.lineno, name_node.col_offset) in ann_spots:
                continue
            if name in BUILTINS or name in module_names:
                continue
            sym = syms.get(name)
            if sym is None:
                continue
            # Bound in this scope, a parameter, a closure cell, or imported here.
            if sym.is_parameter() or sym.is_free() or sym.is_imported():
                continue
            if sym.is_assigned():
                continue
            # Everything left is an IMPLICIT GLOBAL that the module does not
            # define -- at runtime this is exactly NameError.
            findings.append((name_node.lineno, name_node.col_offset,
                             f"undefined name '{name}'"))
    return sorted(set(findings))


if __name__ == "__main__":
    total = 0
    for root in sys.argv[1:]:
        files = ([root] if os.path.isfile(root) else
                 [os.path.join(d, f) for d, _, fs in os.walk(root)
                  for f in fs if f.endswith(".py")])
        for f in sorted(files):
            for ln, col, msg in check(f):
                print(f"{f}:{ln}:{col}: {msg}")
                total += 1
    print(f"--- {total} finding(s) ---", file=sys.stderr)

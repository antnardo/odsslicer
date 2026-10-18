"""The package's modules import one another in one direction: no cycle at
run time, whatever the order they are first imported in.

Read from the source, not from `sys.modules`: an import cycle can work, or
fail, depending on which module is imported first, and a test importing the
package proves only the order it happened to take. An import under
`if TYPE_CHECKING:` runs for mypy alone, and is left out: the style views
name `Cell` and `Sheet` there, which import them.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "odsslicer"
MODULES = sorted(path.stem for path in PACKAGE.glob("*.py"))


def _runtime_imports(path):
    """The modules of the package `path` imports at run time."""
    imported = set()

    def visit(statements):
        for node in statements:
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
                visit(node.orelse)
                continue
            if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
                parts = node.module.split(".")
                if parts[0] == "odsslicer":
                    imported.update(parts[1:2] or [alias.name for alias in node.names])
            elif isinstance(node, ast.Import):
                imported.update(
                    alias.name.split(".")[1] for alias in node.names if alias.name.startswith("odsslicer.")
                )
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    visit([child])

    visit(ast.parse(path.read_text()).body)
    return imported - {path.stem}


def _graph():
    return {name: _runtime_imports(PACKAGE / f"{name}.py") & set(MODULES) for name in MODULES}


def test_the_modules_import_one_another_without_a_cycle():
    graph = _graph()
    done, path = set(), []

    def visit(name):
        if name in path:
            pytest.fail("import cycle: " + " -> ".join([*path[path.index(name) :], name]))
        if name in done:
            return
        path.append(name)
        for dependency in sorted(graph[name]):
            visit(dependency)
        path.pop()
        done.add(name)

    for name in MODULES:
        visit(name)


def test_the_package_init_holds_no_logic():
    statements = ast.parse((PACKAGE / "__init__.py").read_text()).body
    for node in statements:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        is_all = isinstance(node, ast.Assign) and [ast.unparse(t) for t in node.targets] == ["__all__"]
        assert is_docstring or is_all or isinstance(node, ast.ImportFrom), ast.unparse(node)[:60]


def _declared_all(name):
    """The names `name`'s `__all__` lists, read from the source; `None` if
    it declares none."""
    for node in ast.parse((PACKAGE / f"{name}.py").read_text()).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if [ast.unparse(t) for t in targets] == ["__all__"]:
            return ast.literal_eval(node.value)
    return None


@pytest.mark.parametrize("name", [name for name in MODULES if name != "__init__"])
def test_every_module_declares_its_public_names(name):
    declared = _declared_all(name)
    assert declared is not None, f"{name}.py declares no __all__"
    assert not [public for public in declared if public.startswith("_")]


# but xmltree's: its `qn` is imported from it, to search the escape hatches
@pytest.mark.parametrize("name", [name for name in MODULES if name not in ("__init__", "xmltree")])
def test_the_package_exports_the_modules_public_names(name):
    assert set(_declared_all(name)) <= set(_declared_all("__init__"))

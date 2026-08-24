import ast
from pathlib import Path

RUNTIME_ROOT = Path("services/agent_runtime")
FORBIDDEN_IMPORTS = {
    "frappe",
    "erpnext",
    "mariadb",
    "mysql",
    "mysql.connector",
    "MySQLdb",
    "pymysql",
    "psycopg2",
}


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_runtime_has_no_erp_or_database_imports() -> None:
    imported = set()
    for source_file in RUNTIME_ROOT.rglob("*.py"):
        imported.update(_imported_modules(source_file.read_text(encoding="utf-8")))

    forbidden = {
        module
        for module in imported
        if module in FORBIDDEN_IMPORTS
        or any(module.startswith(f"{prefix}.") for prefix in FORBIDDEN_IMPORTS)
    }
    assert forbidden == set()


def test_runtime_direct_dependencies_are_boundary_safe() -> None:
    pyproject = (RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "frappe" not in pyproject.lower()
    assert "erpnext" not in pyproject.lower()
    assert "mariadb" not in pyproject.lower()
    assert "pymysql" not in pyproject.lower()

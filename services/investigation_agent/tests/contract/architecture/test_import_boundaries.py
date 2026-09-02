import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "investigation_agent"
PURE_ROOTS = (PACKAGE_ROOT / "domain", PACKAGE_ROOT / "application")
FORBIDDEN_EXTERNAL_ROOTS = {
    "boto3",
    "botocore",
    "fastapi",
    "langchain",
    "langchain_aws",
    "langgraph",
    "pgvector",
    "pglast",
    "psycopg",
    "sse_starlette",
    "starlette",
    "uvicorn",
}


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", maxsplit=1)[0])
    return roots


def test_domain_and_application_have_no_framework_database_or_provider_imports() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(_import_roots(path) & FORBIDDEN_EXTERNAL_ROOTS)
        for root in PURE_ROOTS
        if root.exists()
        for path in root.rglob("*.py")
        if _import_roots(path) & FORBIDDEN_EXTERNAL_ROOTS
    }

    assert violations == {}


def test_service_never_imports_a_sibling_service_package() -> None:
    violations = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if "ingestion" in _import_roots(path)
    ]

    assert violations == []

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "investigation_agent"
PURE_ROOTS = tuple(PACKAGE_ROOT / owner for owner in ("core", "domain", "ports", "application"))
INTERNAL_BOUNDARIES = {
    "domain": {
        "adapters",
        "api",
        "application",
        "bootstrap",
        "db",
        "genai",
        "observability",
        "ports",
    },
    "ports": {"adapters", "api", "application", "bootstrap", "db", "genai", "observability"},
    "application": {"adapters", "api", "bootstrap", "db", "genai"},
    "db": {"adapters", "api", "application", "bootstrap", "genai"},
    "genai": {"adapters", "api", "bootstrap", "db"},
}
FORBIDDEN_EXTERNAL_ROOTS = {
    "boto3",
    "botocore",
    "fastapi",
    "langchain",
    "langchain_aws",
    "langchain_core",
    "langgraph",
    "opentelemetry",
    "pgvector",
    "pglast",
    "psycopg",
    "sqlalchemy",
    "sse_starlette",
    "starlette",
    "uvicorn",
}
GENERIC_COLLECTIONS = {
    "constants.py",
    "core/constants.py",
    "core/errors.py",
    "errors.py",
    "common/constants.py",
    "common/errors.py",
}
FRAMEWORK_PORT_NAMES = {
    "ainvoke",
    "astream",
    "checkpoint_ns",
    "configurable",
    "durability",
    "recursion_limit",
    "stream_mode",
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


def _service_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            prefix = "investigation_agent."
            if module.startswith(prefix):
                roots.add(module.removeprefix(prefix).split(".", maxsplit=1)[0])
    return roots


def _relative_import_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]


def _framework_port_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        names.add(node.name)
        names.update(arg.arg for arg in node.args.args + node.args.kwonlyargs)
    return names & FRAMEWORK_PORT_NAMES


def test_domain_and_application_have_no_framework_database_or_provider_imports() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(_import_roots(path) & FORBIDDEN_EXTERNAL_ROOTS)
        for root in PURE_ROOTS
        if root.exists()
        for path in root.rglob("*.py")
        if _import_roots(path) & FORBIDDEN_EXTERNAL_ROOTS
    }

    assert violations == {}


def test_internal_dependencies_point_toward_contracts_and_domain() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(_service_import_roots(path) & forbidden_roots)
        for owner, forbidden_roots in INTERNAL_BOUNDARIES.items()
        for path in (PACKAGE_ROOT / owner).rglob("*.py")
        if _service_import_roots(path) & forbidden_roots
    }

    assert violations == {}


def test_service_never_imports_a_sibling_service_package() -> None:
    violations = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if "ingestion" in _import_roots(path)
    ]

    assert violations == []


def test_service_uses_only_absolute_imports() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): _relative_import_lines(path)
        for path in PACKAGE_ROOT.rglob("*.py")
        if _relative_import_lines(path)
    }

    assert violations == {}


def test_errors_and_constants_are_not_centralized_in_generic_roots() -> None:
    present = {
        str(path.relative_to(PACKAGE_ROOT))
        for path in PACKAGE_ROOT.rglob("*.py")
        if str(path.relative_to(PACKAGE_ROOT)) in GENERIC_COLLECTIONS
    }

    assert present == set()


def test_ports_do_not_expose_framework_control_vocabulary() -> None:
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): sorted(_framework_port_names(path))
        for path in (PACKAGE_ROOT / "ports").rglob("*.py")
        if _framework_port_names(path)
    }

    assert violations == {}

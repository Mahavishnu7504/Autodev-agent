"""
AutoDev Agent - Smart Test Validator

Validates generated Python tests before execution.

Goals:
- detect invalid imports
- resolve real project modules
- verify imported symbols exist
- reject tests that reference nonexistent project structure
- keep validation deterministic and dependency-free
"""

import ast
import re
from pathlib import Path
from typing import Any


def _module_name(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized.endswith(".py"):
        return normalized.replace("/", ".")
    normalized = normalized[:-3]
    if normalized.endswith("/__init__"):
        normalized = normalized[:-9]
    return normalized.replace("/", ".")


def _project_modules(project: dict[str, str]) -> dict[str, set[str]]:
    modules: dict[str, set[str]] = {}
    for path, content in project.items():
        if not path.endswith(".py"):
            continue
        if path.startswith("tests/"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        module = _module_name(path)
        symbols: set[str] = set()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        symbols.add(target.id)

        modules[module] = symbols
    return modules


def _import_errors(test_code: str, modules: dict[str, set[str]]) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(test_code)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                continue

            module = node.module or ""
            if not module or module.startswith(("unittest", "typing", "pathlib", "json", "os", "sys", "math", "re")):
                continue

            if module not in modules:
                top = module.split(".")[0]
                if top in {"pytest", "requests", "numpy", "pandas"}:
                    continue
                errors.append(f"Module not found: {module}")
                continue

            symbols = modules[module]
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in symbols:
                    errors.append(
                        f"Symbol not found: {alias.name} in {module}"
                    )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                top = module.split(".")[0]
                if top in {"unittest", "typing", "pathlib", "json", "os", "sys", "math", "re"}:
                    continue
                if module in modules:
                    continue
                if top in {"pytest", "requests", "numpy", "pandas"}:
                    continue

    return errors


def _rewrite_common_imports(
    test_code: str,
    modules: dict[str, set[str]],
) -> str:
    """
    Correct the common mistake:
        from app import ClassName
    when ClassName actually lives in:
        app.main
    """
    if not modules:
        return test_code

    def replace(match: re.Match) -> str:
        package = match.group(1)
        symbol = match.group(2)

        direct_module = package
        if direct_module in modules and symbol in modules[direct_module]:
            return match.group(0)

        candidates = [
            module
            for module, symbols in modules.items()
            if module.startswith(package + ".") and symbol in symbols
        ]

        if len(candidates) == 1:
            return f"from {candidates[0]} import {symbol}"

        return match.group(0)

    pattern = r"from\s+([A-Za-z_][\w.]*)\s+import\s+([A-Za-z_]\w*)"
    return re.sub(pattern, replace, test_code)


def validate_tests(
    project: dict[str, str],
    tests: list[dict[str, Any]],
) -> dict[str, Any]:
    modules = _project_modules(project)
    validated: list[dict[str, str]] = []
    errors: list[str] = []

    for item in tests:
        path = str(item.get("path", "")).strip()
        content = str(item.get("content", ""))

        if not path or not content.strip():
            continue

        fixed = _rewrite_common_imports(content, modules)
        test_errors = _import_errors(fixed, modules)

        if test_errors:
            errors.extend(f"{path}: {error}" for error in test_errors)
            continue

        validated.append({
            "path": path,
            "content": fixed,
        })

    return {
        "valid": bool(validated) and not errors,
        "tests": validated,
        "errors": errors,
        "modules": {
            module: sorted(symbols)
            for module, symbols in modules.items()
        },
    }

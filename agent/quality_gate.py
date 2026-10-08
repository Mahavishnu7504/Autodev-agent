from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Iterable


# ============================================================
# CONFIG
# ============================================================

IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "node_modules",
}

IGNORED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
}

IGNORED_FILES = {
    ".DS_Store",
    "Thumbs.db",
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "for",
    "with",
    "from",
    "must",
    "should",
    "support",
    "supports",
    "system",
    "application",
    "user",
    "users",
    "using",
    "when",
    "that",
    "this",
    "is",
    "be",
    "can",
    "able",
    "allow",
    "allows",
    "return",
    "returns",
    "check",
    "checking",
    "have",
    "has",
    "into",
    "their",
    "its",
    "will",
    "via",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def _normalize_path(path: str | Path) -> str:
    return (
        str(path)
        .replace("\\", "/")
        .strip("/")
    )


def _is_ignored(path: Path) -> bool:
    if path.name in IGNORED_FILES:
        return True

    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True

    return any(
        part in IGNORED_DIRECTORIES
        for part in path.parts
    )


def _iter_files(
    workspace: Path,
) -> Iterable[Path]:

    if not workspace.exists():
        return []

    return (
        path
        for path in workspace.rglob("*")
        if path.is_file()
        and not _is_ignored(
            path.relative_to(workspace)
        )
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return ""


# ============================================================
# BEHAVIOR SPECIFICATION
# ============================================================

def _flatten_spec(
    value: Any,
    prefix: str = "",
) -> list[str]:

    result: list[str] = []

    if value is None:
        return result

    if isinstance(value, str):

        text = value.strip()

        if text:
            if prefix:
                result.append(
                    f"{prefix}: {text}"
                )
            else:
                result.append(text)

        return result

    if isinstance(value, dict):

        for key, item in value.items():

            key_text = str(key).strip()

            next_prefix = (
                f"{prefix}.{key_text}"
                if prefix
                else key_text
            )

            if isinstance(
                item,
                (dict, list, tuple, set),
            ):

                result.extend(
                    _flatten_spec(
                        item,
                        next_prefix,
                    )
                )

            elif item is not None:

                result.extend(
                    _flatten_spec(
                        str(item),
                        next_prefix,
                    )
                )

        return result

    if isinstance(
        value,
        (list, tuple, set),
    ):

        for item in value:

            result.extend(
                _flatten_spec(
                    item,
                    prefix,
                )
            )

    return result


def _tokens(text: str) -> set[str]:

    words = re.findall(
        r"[A-Za-z0-9_]+",
        text.lower(),
    )

    result = set()

    for word in words:

        if len(word) < 3:
            continue

        if word in STOPWORDS:
            continue

        result.add(word)

    return result


# ============================================================
# PROJECT FILE CHECK
# ============================================================

def _validate_required_files(
    workspace: Path,
    plan: Dict[str, Any] | None,
) -> Dict[str, Any]:

    if not isinstance(plan, dict):

        return {
            "passed": True,
            "expected": [],
            "missing": [],
        }

    raw_files = plan.get(
        "files",
        [],
    )

    if not isinstance(
        raw_files,
        list,
    ):

        return {
            "passed": True,
            "expected": [],
            "missing": [],
        }

    expected: list[str] = []

    for item in raw_files:

        if isinstance(item, dict):

            path = (
                item.get("path")
                or item.get("filename")
                or ""
            )

        else:

            path = str(item)

        path = _normalize_path(path)

        if not path:
            continue

        # Test files are generated after planning.
        if path.startswith("tests/"):
            continue

        expected.append(path)

    expected = sorted(
        set(expected)
    )

    missing = [
        path
        for path in expected
        if not (
            workspace / path
        ).is_file()
    ]

    return {
        "passed": not missing,
        "expected": expected,
        "missing": missing,
    }


# ============================================================
# PYTHON SYNTAX CHECK
# ============================================================

def _validate_syntax(
    workspace: Path,
) -> Dict[str, Any]:

    python_files: list[str] = []
    errors: list[dict] = []

    for path in _iter_files(workspace):

        relative = path.relative_to(
            workspace
        )

        if path.suffix.lower() != ".py":
            continue

        relative_name = (
            relative.as_posix()
        )

        python_files.append(
            relative_name
        )

        source = _read_text(path)

        try:

            ast.parse(
                source,
                filename=relative_name,
            )

        except SyntaxError as exc:

            errors.append({
                "file": relative_name,
                "line": exc.lineno,
                "column": exc.offset,
                "message": exc.msg,
            })

    return {
        "passed": not errors,
        "files": sorted(python_files),
        "errors": errors,
    }


# ============================================================
# PACKAGE STRUCTURE
# ============================================================

def _validate_structure(
    workspace: Path,
) -> Dict[str, Any]:

    errors: list[str] = []
    warnings: list[str] = []

    # --------------------------------------------------------
    # Package/module collisions
    # --------------------------------------------------------

    for package in (
        "app",
        "src",
    ):

        package_dir = (
            workspace / package
        )

        module_file = (
            workspace / f"{package}.py"
        )

        if (
            package_dir.is_dir()
            and module_file.is_file()
        ):

            errors.append(
                f"Package/module collision: "
                f"{package}/ and {package}.py both exist."
            )

        if package_dir.is_dir():

            python_files = list(
                package_dir.rglob("*.py")
            )

            if python_files:

                init_file = (
                    package_dir
                    / "__init__.py"
                )

                if not init_file.exists():

                    errors.append(
                        f"Package {package}/ "
                        f"contains Python files "
                        f"but has no __init__.py."
                    )

    # --------------------------------------------------------
    # Tests
    # --------------------------------------------------------

    tests_dir = (
        workspace / "tests"
    )

    if tests_dir.exists():

        test_files = [
            path
            for path in tests_dir.rglob("*.py")
            if path.is_file()
        ]

        if not test_files:

            warnings.append(
                "tests/ exists but contains no Python test files."
            )

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
    }


# ============================================================
# ARTIFACT CHECK
# ============================================================

def _validate_artifacts(
    workspace: Path,
) -> Dict[str, Any]:

    artifacts: list[str] = []

    # Check directories as well as files.
    for path in workspace.rglob("*"):

        relative = path.relative_to(
            workspace
        )

        if path.is_dir():

            if any(
                part in IGNORED_DIRECTORIES
                for part in relative.parts
            ):

                artifacts.append(
                    relative.as_posix()
                )

        elif path.is_file():

            if (
                path.name in IGNORED_FILES
                or path.suffix.lower()
                in IGNORED_SUFFIXES
            ):

                artifacts.append(
                    relative.as_posix()
                )

    return {
        "passed": not artifacts,
        "artifacts": sorted(
            set(artifacts)
        ),
    }


# ============================================================
# TEST DISCOVERY
# ============================================================

def _discover_tests(
    workspace: Path,
) -> list[str]:

    result = []

    tests_dir = (
        workspace / "tests"
    )

    if not tests_dir.exists():
        return result

    for path in tests_dir.rglob("*.py"):

        if not path.is_file():
            continue

        if _is_ignored(
            path.relative_to(workspace)
        ):
            continue

        result.append(
            path.relative_to(
                workspace
            ).as_posix()
        )

    return sorted(result)


# ============================================================
# TEST RESULT VALIDATION
# ============================================================

def _validate_test_result(
    test_result: Dict[str, Any] | None,
    discovered_tests: list[str],
) -> Dict[str, Any]:

    if test_result is None:

        return {
            "passed": not bool(
                discovered_tests
            ),
            "expected": bool(
                discovered_tests
            ),
            "executed": False,
            "skipped": False,
            "reason": (
                "Tests exist but no test "
                "execution result was supplied."
                if discovered_tests
                else
                "No tests were discovered."
            ),
        }

    skipped = bool(
        test_result.get(
            "skipped",
            False,
        )
    )

    success = bool(
        test_result.get(
            "success",
            False,
        )
    )

    # A skipped test suite is not considered
    # a successful quality validation when tests
    # actually exist.
    if discovered_tests and skipped:

        return {
            "passed": False,
            "expected": True,
            "executed": False,
            "skipped": True,
            "reason": (
                "Tests exist but execution was skipped."
            ),
        }

    return {
        "passed": success,
        "expected": bool(
            discovered_tests
        ),
        "executed": not skipped,
        "skipped": skipped,
        "return_code": test_result.get(
            "return_code"
        ),
        "reason": (
            "Test suite passed."
            if success
            else "Test suite failed."
        ),
    }


# ============================================================
# APPLICATION RESULT VALIDATION
# ============================================================

def _validate_application_result(
    run_result: Dict[str, Any] | None,
) -> Dict[str, Any]:

    if run_result is None:

        return {
            "passed": True,
            "expected": False,
            "executed": False,
            "reason": (
                "No application command was required."
            ),
        }

    skipped = bool(
        run_result.get(
            "skipped",
            False,
        )
    )

    success = bool(
        run_result.get(
            "success",
            False,
        )
    )

    if skipped:

        return {
            "passed": False,
            "expected": True,
            "executed": False,
            "skipped": True,
            "reason": (
                "Application execution was skipped."
            ),
        }

    return {
        "passed": success,
        "expected": True,
        "executed": True,
        "skipped": False,
        "return_code": run_result.get(
            "return_code"
        ),
        "reason": (
            "Application executed successfully."
            if success
            else
            "Application execution failed."
        ),
    }


# ============================================================
# IMPORT / AST VALIDATION
# ============================================================

def _project_modules(
    workspace: Path,
) -> set[str]:

    modules: set[str] = set()

    for path in _iter_files(workspace):

        relative = path.relative_to(
            workspace
        )

        if path.suffix.lower() != ".py":
            continue

        parts = list(
            relative.with_suffix("").parts
        )

        if not parts:
            continue

        if parts[-1] == "__init__":
            parts.pop()

        if not parts:
            continue

        modules.add(
            ".".join(parts)
        )

    return modules


def _validate_test_imports(
    workspace: Path,
) -> Dict[str, Any]:

    test_files = _discover_tests(
        workspace
    )

    if not test_files:

        return {
            "passed": True,
            "errors": [],
            "checked_files": [],
        }

    modules = _project_modules(
        workspace
    )

    errors = []

    for relative_name in test_files:

        path = (
            workspace
            / relative_name
        )

        source = _read_text(path)

        try:

            tree = ast.parse(
                source,
                filename=relative_name,
            )

        except SyntaxError:
            # Syntax validation reports this separately.
            continue

        for node in ast.walk(tree):

            if isinstance(
                node,
                ast.ImportFrom,
            ):

                module = (
                    node.module or ""
                )

                if not module:
                    continue

                root = module.split(
                    "."
                )[0]

                # Only flag imports that clearly target
                # the generated project.
                if root in {
                    "app",
                    "src",
                    "main",
                }:

                    if module not in modules:

                        # Allow imports from a package
                        # when a child module exists.
                        prefix_exists = any(
                            candidate.startswith(
                                module + "."
                            )
                            for candidate
                            in modules
                        )

                        if not prefix_exists:

                            errors.append({
                                "file": relative_name,
                                "import": module,
                                "message": (
                                    "Generated project "
                                    "module does not exist."
                                ),
                            })

    return {
        "passed": not errors,
        "errors": errors,
        "checked_files": test_files,
    }


# ============================================================
# REQUIREMENT COVERAGE
# ============================================================

def _requirement_coverage(
    specification: Any,
    source_text: str,
    test_text: str,
) -> Dict[str, Any]:

    requirements = _flatten_spec(
        specification
    )

    if not requirements:

        return {
            "available": False,
            "passed": True,
            "total": 0,
            "covered": 0,
            "details": [],
        }

    source_lower = source_text.lower()
    tests_lower = test_text.lower()

    details = []
    covered_count = 0

    for index, requirement in enumerate(
        requirements,
        start=1,
    ):

        tokens = _tokens(
            requirement
        )

        if not tokens:

            covered_count += 1

            details.append({
                "id": index,
                "requirement": requirement,
                "passed": True,
                "reason": (
                    "No meaningful tokens."
                ),
            })

            continue

        source_hits = sorted(
            token
            for token in tokens
            if token in source_lower
        )

        test_hits = sorted(
            token
            for token in tokens
            if token in tests_lower
        )

        # Strong evidence:
        # implementation + tests.
        strong = bool(
            source_hits
            and test_hits
        )

        # Accept implementation evidence alone
        # for requirements where the generated
        # test wording differs substantially.
        implementation_present = bool(
            source_hits
        )

        passed = implementation_present

        if passed:
            covered_count += 1

        details.append({
            "id": index,
            "requirement": requirement,
            "passed": passed,
            "strong_evidence": strong,
            "implementation_evidence": (
                source_hits[:12]
            ),
            "test_evidence": (
                test_hits[:12]
            ),
        })

    return {
        "available": True,
        "passed": (
            covered_count
            == len(requirements)
        ),
        "total": len(requirements),
        "covered": covered_count,
        "details": details,
    }


def _collect_code(
    workspace: Path,
) -> tuple[str, str]:

    source_parts = []
    test_parts = []

    for path in _iter_files(
        workspace
    ):

        relative = path.relative_to(
            workspace
        )

        if path.suffix.lower() != ".py":
            continue

        content = _read_text(path)

        if (
            relative.parts
            and relative.parts[0]
            == "tests"
        ):

            test_parts.append(content)

        else:

            source_parts.append(content)

    return (
        "\n".join(source_parts),
        "\n".join(test_parts),
    )


# ============================================================
# QUALITY GATE
# ============================================================

def run_quality_gate(
    workspace: Path | str,
    plan: Dict[str, Any] | None = None,
    test_result: Dict[str, Any] | None = None,
    run_result: Dict[str, Any] | None = None,
    behavior_specification: Any = None,
) -> Dict[str, Any]:

    """
    Production-style deterministic quality gate.

    No LLM.
    No network.
    No dependency installation.
    No subprocess execution.

    The gate validates the generated artifact after
    autonomous generation/repair and before packaging.
    """

    workspace = Path(workspace)

    checks = []
    warnings = []

    # --------------------------------------------------------
    # 1. Workspace
    # --------------------------------------------------------

    workspace_exists = workspace.exists()

    checks.append({
        "name": "workspace_exists",
        "passed": workspace_exists,
    })

    if not workspace_exists:

        return {
            "quality_gate": "failed",
            "passed": False,
            "failed_checks": [
                "workspace_exists"
            ],
            "checks": checks,
            "warnings": [
                "Workspace does not exist."
            ],
        }

    # --------------------------------------------------------
    # 2. Required files
    # --------------------------------------------------------

    required = _validate_required_files(
        workspace,
        plan,
    )

    checks.append({
        "name": "required_files",
        "passed": required["passed"],
        "details": required,
    })

    # --------------------------------------------------------
    # 3. Python syntax
    # --------------------------------------------------------

    syntax = _validate_syntax(
        workspace
    )

    checks.append({
        "name": "python_syntax",
        "passed": syntax["passed"],
        "details": syntax,
    })

    # --------------------------------------------------------
    # 4. Structure
    # --------------------------------------------------------

    structure = _validate_structure(
        workspace
    )

    checks.append({
        "name": "package_structure",
        "passed": structure["passed"],
        "details": structure,
    })

    warnings.extend(
        structure["warnings"]
    )

    # --------------------------------------------------------
    # 5. Test discovery
    # --------------------------------------------------------

    test_files = _discover_tests(
        workspace
    )

    test_check = _validate_test_result(
        test_result,
        test_files,
    )

    checks.append({
        "name": "test_execution",
        "passed": test_check["passed"],
        "details": test_check,
    })

    # --------------------------------------------------------
    # 6. Application execution
    # --------------------------------------------------------

    application_check = (
        _validate_application_result(
            run_result
        )
    )

    checks.append({
        "name": "application_execution",
        "passed": application_check["passed"],
        "details": application_check,
    })

    # --------------------------------------------------------
    # 7. Test imports
    # --------------------------------------------------------

    imports = _validate_test_imports(
        workspace
    )

    checks.append({
        "name": "test_import_integrity",
        "passed": imports["passed"],
        "details": imports,
    })

    # --------------------------------------------------------
    # 8. Behavior specification
    # --------------------------------------------------------

    requirements = _flatten_spec(
        behavior_specification
    )

    specification_present = bool(
        requirements
    )

    checks.append({
        "name": "behavior_contract",
        "passed": specification_present,
        "details": {
            "requirement_count": len(
                requirements
            ),
        },
    })

    # --------------------------------------------------------
    # 9. Requirement coverage
    # --------------------------------------------------------

    source_text, test_text = (
        _collect_code(workspace)
    )

    coverage = _requirement_coverage(
        behavior_specification,
        source_text,
        test_text,
    )

    checks.append({
        "name": "requirement_coverage",
        "passed": coverage["passed"],
        "details": coverage,
    })

    # --------------------------------------------------------
    # 10. Artifact cleanliness
    # --------------------------------------------------------

    artifacts = _validate_artifacts(
        workspace
    )

    checks.append({
        "name": "artifact_cleanliness",
        "passed": artifacts["passed"],
        "details": artifacts,
    })

    # --------------------------------------------------------
    # FINAL DECISION
    # --------------------------------------------------------

    failed_checks = [
        check["name"]
        for check in checks
        if not check["passed"]
    ]

    passed = not failed_checks

    result = {
        "quality_gate": (
            "passed"
            if passed
            else "failed"
        ),

        "passed": passed,

        "tests_passed": bool(
            test_check["passed"]
        ),

        "application_passed": bool(
            application_check["passed"]
        ),

        "structure_valid": bool(
            structure["passed"]
        ),

        "syntax_valid": bool(
            syntax["passed"]
        ),

        "imports_valid": bool(
            imports["passed"]
        ),

        "artifacts_clean": bool(
            artifacts["passed"]
        ),

        "requirements_covered": bool(
            coverage["passed"]
        ),

        "required_files_present": bool(
            required["passed"]
        ),

        "behavior_contract_present": (
            specification_present
        ),

        "test_files": test_files,

        "failed_checks": failed_checks,

        "checks": checks,

        "warnings": warnings,

        "coverage": coverage,
    }

    return result
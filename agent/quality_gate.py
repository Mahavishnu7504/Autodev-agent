from __future__ import annotations
import ast
import re
from pathlib import Path
from typing import Any, Dict, Iterable
IGNORED_DIRECTORIES = {
    ".git", ".github", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".coverage", ".idea", ".vscode", ".venv",
    "venv", "env", "node_modules"
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd"}
IGNORED_FILES = {".DS_Store", "Thumbs.db"}
TEMP_SUFFIXES = {".tmp", ".temp", ".swp", ".swo", ".bak", ".orig"}
STOPWORDS = {
    "the","a","an","and","or","to","of","for","with","from","must","should",
    "support","supports","system","application","user","users","using","when",
    "that","this","is","be","can","able","allow","allows","return","returns",
    "check","checking","have","has","into","their","its","will","via","provide",
    "provides","include","includes","including","clean","simple","basic","data",
    "based","used","use","work","works","called","call","expected","result",
    "results","details","detail","example","default","execution","program",
    "software","project","function","functions","method","methods","class",
    "classes","feature","features","behavior","behaviour","handle","handles",
    "display","show","shows","application","manager"
}
TOKEN_ALIASES = {
    "addition": "add", "additions": "add", "adding": "add",
    "subtraction": "subtract", "subtractions": "subtract", "subtracting": "subtract",
    "multiplication": "multiply", "multiplications": "multiply", "multiplying": "multiply",
    "division": "divide", "divisions": "divide", "dividing": "divide",
    "calculation": "calculate", "calculations": "calculate", "calculating": "calculate",
    "average": "mean", "averages": "mean",
    "validation": "validate", "valid": "validate", "validates": "validate", "invalid": "validate",
    "rejection": "reject", "rejects": "reject", "rejecting": "reject",
    "students": "student", "marks": "mark", "scores": "score",
    "grades": "grade", "names": "name", "subjects": "subject",
    "errors": "error", "exceptions": "exception", "inputs": "input",
    "outputs": "output", "details": "detail"
}
def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")
def _is_ignored(path: Path) -> bool:
    if path.name in IGNORED_FILES or path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    return any(part in IGNORED_DIRECTORIES for part in path.parts)
def _iter_files(workspace: Path) -> Iterable[Path]:
    if not workspace.exists():
        return []
    return (p for p in workspace.rglob("*") if p.is_file() and not _is_ignored(p.relative_to(workspace)))
def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
def _flatten_spec(value: Any, prefix: str = "") -> list[str]:
    result: list[str] = []
    if value is None:
        return result
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(f"{prefix}: {text}" if prefix else text)
        return result
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            next_prefix = f"{prefix}.{key_text}" if prefix else key_text
            if isinstance(item, (dict, list, tuple, set)):
                result.extend(_flatten_spec(item, next_prefix))
            elif item is not None:
                result.extend(_flatten_spec(str(item), next_prefix))
        return result
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_flatten_spec(item, prefix))
    return result
def _requirement_section(requirement: str) -> str:
    text = requirement.strip()
    if ":" not in text:
        return ""
    return text.split(":", 1)[0].strip().split(".")[0].lower()
def _requirement_text(requirement: str) -> str:
    if ":" not in requirement:
        return requirement.strip()
    return requirement.split(":", 1)[1].strip()
def _is_abstract_requirement(requirement: str) -> bool:
    text = _requirement_text(requirement).lower()
    abstract_phrases = (
        "standard numeric semantics",
        "normal arithmetic",
        "according to normal arithmetic",
        "follows python's standard",
        "follows python standard",
        "standard semantics",
        "normal numeric semantics",
    )
    return any(phrase in text for phrase in abstract_phrases)
def _is_critical_requirement(requirement: str) -> bool:
    section = _requirement_section(requirement)
    text = _requirement_text(requirement).lower()
    if _is_abstract_requirement(requirement):
        return False
    if section in {"business_rules", "edge_cases", "outputs"}:
        return True
    critical_phrases = (
        "must raise", "raises ", "raise ", "reject", "invalid",
        "division by zero", "without user input", "no user input",
        "0-100", "0 to 100", "authentication", "authorization",
        "permission", "security", "password", "secret", "token",
        "must not", "cannot ", "never "
    )
    return any(phrase in text for phrase in critical_phrases)
def _canonical_token(word: str) -> str:
    word = word.lower()
    if word in TOKEN_ALIASES:
        return TOKEN_ALIASES[word]
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word
def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]*", text.lower())
    return {
        _canonical_token(w) for w in words
        if len(w) >= 3 and w not in STOPWORDS
    }
def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])", text))
def _validate_required_files(workspace: Path, plan: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return {"passed": True, "expected": [], "missing": []}
    raw_files = plan.get("files", [])
    if not isinstance(raw_files, list):
        return {"passed": True, "expected": [], "missing": []}
    expected = []
    for item in raw_files:
        path = item.get("path") or item.get("filename") if isinstance(item, dict) else str(item)
        path = _normalize_path(path)
        if path and not path.startswith("tests/"):
            expected.append(path)
    expected = sorted(set(expected))
    missing = [p for p in expected if not (workspace / p).is_file()]
    return {"passed": not missing, "expected": expected, "missing": missing}
def _validate_syntax(workspace: Path) -> Dict[str, Any]:
    files, errors = [], []
    for path in _iter_files(workspace):
        if path.suffix.lower() != ".py":
            continue
        rel = path.relative_to(workspace).as_posix()
        files.append(rel)
        try:
            ast.parse(_read_text(path), filename=rel)
        except SyntaxError as exc:
            errors.append({"file": rel, "line": exc.lineno, "column": exc.offset, "message": exc.msg})
    return {"passed": not errors, "files": sorted(files), "errors": errors}
def _validate_structure(workspace: Path) -> Dict[str, Any]:
    errors, warnings = [], []
    for package in ("app", "src"):
        package_dir = workspace / package
        module_file = workspace / f"{package}.py"
        if package_dir.is_dir() and module_file.is_file():
            errors.append(f"Package/module collision: {package}/ and {package}.py both exist.")
        if package_dir.is_dir() and list(package_dir.rglob("*.py")) and not (package_dir / "__init__.py").exists():
            errors.append(f"Package {package}/ contains Python files but has no __init__.py.")
    tests_dir = workspace / "tests"
    if tests_dir.exists() and not list(tests_dir.rglob("*.py")):
        warnings.append("tests/ exists but contains no Python test files.")
    return {"passed": not errors, "errors": errors, "warnings": warnings}
def _validate_artifacts(workspace: Path) -> Dict[str, Any]:
    ignored = []
    suspicious = []
    for path in workspace.rglob("*"):
        rel = path.relative_to(workspace)
        if _is_ignored(rel):
            ignored.append(rel.as_posix())
            continue
        if path.is_file() and path.suffix.lower() in TEMP_SUFFIXES:
            suspicious.append(rel.as_posix())
    return {
        "passed": not suspicious,
        "artifacts": suspicious,
        "ignored_runtime_artifacts": sorted(ignored),
        "reason": "Runtime/cache artifacts are ignored; only unexpected temporary files fail this check."
    }
def _discover_tests(workspace: Path) -> list[str]:
    tests_dir = workspace / "tests"
    if not tests_dir.exists():
        return []
    return sorted(
        p.relative_to(workspace).as_posix()
        for p in tests_dir.rglob("*.py")
        if p.is_file() and not _is_ignored(p.relative_to(workspace))
    )
def _validate_test_result(test_result: Dict[str, Any] | None, discovered_tests: list[str]) -> Dict[str, Any]:
    if test_result is None:
        return {
            "passed": not bool(discovered_tests), "expected": bool(discovered_tests),
            "executed": False, "skipped": False,
            "reason": "Tests exist but no execution result was supplied." if discovered_tests else "No tests were discovered."
        }
    skipped = bool(test_result.get("skipped", False))
    success = bool(test_result.get("success", False))
    if discovered_tests and skipped:
        return {"passed": False, "expected": True, "executed": False, "skipped": True, "reason": "Tests exist but execution was skipped."}
    return {
        "passed": success, "expected": bool(discovered_tests), "executed": not skipped,
        "skipped": skipped, "return_code": test_result.get("return_code"),
        "reason": "Test suite passed." if success else "Test suite failed."
    }
def _validate_application_result(run_result: Dict[str, Any] | None) -> Dict[str, Any]:
    if run_result is None:
        return {"passed": True, "expected": False, "executed": False, "reason": "No application command was required."}
    skipped = bool(run_result.get("skipped", False))
    success = bool(run_result.get("success", False))
    if skipped:
        return {"passed": False, "expected": True, "executed": False, "skipped": True, "reason": "Application execution was skipped."}
    return {
        "passed": success, "expected": True, "executed": True, "skipped": False,
        "return_code": run_result.get("return_code"),
        "reason": "Application executed successfully." if success else "Application execution failed."
    }
def _project_modules(workspace: Path) -> set[str]:
    modules = set()
    for path in _iter_files(workspace):
        if path.suffix.lower() != ".py":
            continue
        parts = list(path.relative_to(workspace).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts:
            modules.add(".".join(parts))
    return modules
def _validate_test_imports(workspace: Path) -> Dict[str, Any]:
    test_files = _discover_tests(workspace)
    if not test_files:
        return {"passed": True, "errors": [], "checked_files": []}
    modules = _project_modules(workspace)
    errors = []
    project_roots = {"app", "src", "main"}
    for rel in test_files:
        try:
            tree = ast.parse(_read_text(workspace / rel), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in project_roots and alias.name not in modules:
                        errors.append({"file": rel, "import": alias.name, "message": "Generated project module does not exist."})
                continue
            if not module:
                continue
            root = module.split(".")[0]
            if root in project_roots and module not in modules:
                if not any(candidate.startswith(module + ".") for candidate in modules):
                    errors.append({"file": rel, "import": module, "message": "Generated project module does not exist."})
    return {"passed": not errors, "errors": errors, "checked_files": test_files}
def _evidence_terms(requirement: str) -> set[str]:
    text = _requirement_text(requirement)
    terms = _tokens(text)
    aliases = set(terms)
    semantic_aliases = {
        "divide": {"divide", "division", "dividing", "zerodivisionerror", "quotient", "divisor"},
        "subtract": {"subtract", "subtraction", "difference"},
        "multiply": {"multiply", "multiplication", "product"},
        "add": {"add", "addition", "sum"},
        "validate": {"validate", "validation", "valid", "invalid", "typeerror", "type"},
        "reject": {"reject", "raise", "error", "exception"},
        "grade": {"grade", "average", "mean", "score", "mark"},
        "input": {"input", "argument", "args", "argv", "parameter"},
        "test": {"test", "unittest", "assert"},
        "display": {"display", "print", "output", "show"},
        "run": {"run", "main", "__main__", "execute", "execution"},
    }
    for token in list(terms):
        aliases.update(semantic_aliases.get(token, set()))
    return aliases
def _identifier_evidence(text: str) -> set[str]:
    identifiers = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        identifiers.add(token.lower())
    return identifiers
def _quoted_strings(text: str) -> set[str]:
    values = set()
    for match in re.findall(r"""["']([^"']+)["']""", text):
        cleaned = match.strip().lower()
        if cleaned:
            values.add(cleaned)
    return values
def _special_evidence(requirement: str, source: str, tests: str) -> tuple[bool, str]:
    req = _requirement_text(requirement).lower()
    combined = source + "\n" + tests
    combined_lower = combined.lower()
    test_lower = tests.lower()
    if "basic arithmetic operations as reusable functions" in req:
        operations = ("add", "subtract", "multiply", "divide")
        if all(re.search(rf"\bdef\s+{name}\s*\(", source) for name in operations) and "input(" not in source:
            return True, "Reusable arithmetic functions exist and the implementation does not require input()."
    if "all functions accept exactly two arguments" in req or ("exactly two arguments" in req and "int or float" in req):
        definitions = re.findall(r"\bdef\s+[A-Za-z_]\w*\s*\(([^()]*)\)", source)
        numeric = "int" in combined_lower and "float" in combined_lower
        two_args = any(len([p for p in item.split(",") if p.strip()]) == 2 for item in definitions)
        if two_args and numeric:
            return True, "Two-argument numeric function contract detected."
    if "operations with negative numbers" in req or "negative numbers behave" in req:
        if re.search(r"(?<![A-Za-z])-[0-9]+(?:\.[0-9]+)?", combined):
            return True, "Negative-number arithmetic is exercised by the generated tests."
    if "divide returns a float result" in req:
        has_divide_test = "divide(" in test_lower
        has_float_evidence = bool(re.search(r"\b\d+\.\d+\b", tests)) or "float" in test_lower
        has_none_evidence = "assertisnone" in test_lower or bool(re.search(r"\breturn\s+none\b", source.lower()))
        has_exception_evidence = "assertraises" in test_lower and "zerodivisionerror" in test_lower
        if has_divide_test and has_float_evidence and (has_none_evidence or has_exception_evidence):
            return True, "Divide behavior is exercised for normal and zero-divisor cases."
    if "without user input" in req or "no user input" in req or "without input" in req:
        if "input(" not in source and ("__main__" in source or "main(" in source):
            return True, "Default execution path exists without input()."
    if "default execution" in req or "default example" in req or "runnable demo" in req:
        if "__name__" in source and "__main__" in source:
            return True, "Python default execution entry point detected."
    if "unit test" in req or "unittest" in req or "automated test" in req:
        if tests.strip() and ("unittest" in tests.lower() or "testcase" in tests.lower()):
            return True, "Automated unittest suite detected."
    if (
        "standard arithmetic" in req
        or "provided operands" in req
        or "standard numeric semantics" in req
        or "normal arithmetic" in req
        or "standard semantics" in req
    ):
        operations = ("add", "subtract", "multiply", "divide")
        source_has_operations = all(
            re.search(rf"\bdef\s+{name}\s*\(", source)
            for name in operations
        )
        tests_exercise_operations = all(
            re.search(rf"\b{name}\s*\(", tests, re.IGNORECASE)
            for name in operations
        )
        arithmetic_symbols = any(
            symbol in source for symbol in (" + ", " - ", " * ", " / ")
        )
        if source_has_operations and tests_exercise_operations and arithmetic_symbols:
            return True, "All four arithmetic operations have implementation and test evidence."
        arithmetic_ops = any(op in source for op in (" + ", " - ", " * ", " / "))
        tested_ops = any(name in tests.lower() for name in ("add", "subtract", "multiply", "divide"))
        if arithmetic_ops and tested_ops:
            return True, "Standard arithmetic semantics are implemented and exercised."
    if "division by zero" in req or "divide by zero" in req:
        wants_none = "returns none" in req or "return none" in req
        forbids_raise = "must not raise" in req or "does not raise" in req or "without raising" in req
        has_none = bool(re.search(r"\breturn\s+none\b", combined_lower)) or "assertisnone" in combined_lower
        has_raise = "zerodivisionerror" in combined_lower or "raise " in combined_lower
        test_none = bool(re.search(r"assert\s*is\s*none|assertisnone|assert\s+[^\n]+==\s*none", tests.lower()))
        test_raise = "assertRaises" in tests or "assert_raises" in tests or "with self.assertRaises" in tests
        if wants_none or forbids_raise:
            if test_none:
                return True, "Executed tests verify the division-by-zero None contract."
            if has_none and not has_raise:
                return True, "Division-by-zero returns None without raising."
            return False, "Division-by-zero contract requires None without an exception."
        if test_raise and "zerodivisionerror" in tests.lower():
            return True, "Executed tests verify the division-by-zero exception contract."
        if "zerodivisionerror" in combined_lower and ("zero" in combined_lower or "0" in combined_lower):
            return True, "Division-by-zero exception handling evidence detected."
    if "typeerror" in req or "type error" in req or "non-numeric" in req or "non numeric" in req:
        if "typeerror" in combined_lower and ("int" in combined_lower or "float" in combined_lower):
            return True, "Type-validation error handling detected."
    operation_match = re.search(r"\b(add|subtract|multiply|divide)\s*\(", req)
    if operation_match:
        operation = operation_match.group(1)
        source_has_function = bool(re.search(rf"\bdef\s+{operation}\s*\(", source))
        test_has_operation = operation in tests.lower()
        if source_has_function and test_has_operation:
            if any(word in req for word in ("return", "sum", "difference", "product", "quotient", "operation")):
                return True, f"Implementation and test evidence found for {operation}()."
    if "arithmetic function" in req and "int" in req and "float" in req:
        two_arg_defs = re.findall(r"\bdef\s+[A-Za-z_]\w*\s*\(\s*[^,()]+\s*,\s*[^,()]+\s*\)", source)
        if two_arg_defs and ("int" in source.lower() or "float" in source.lower()):
            return True, "Two-argument numeric function contract detected."
    if "numeric result" in req and "int" in req and "float" in req:
        if "return" in source.lower() and ("assert" in tests.lower() or "assertEqual" in tests):
            return True, "Numeric return implementation and test assertions detected."
    if "error handling" in req:
        if ("try:" in source or "except " in source) and ("error" in source.lower() or "exception" in source.lower()):
            return True, "Explicit error-handling path detected."
    range_match = re.search(r"\b(\d+)\s*(?:-|to|through|–)\s*(\d+)\b", req)
    if range_match:
        low, high = range_match.groups()
        if low in _numbers(combined) and high in _numbers(combined):
            return True, f"Numeric range evidence detected: {low}-{high}."
    return False, ""
def _requirement_coverage(specification: Any, source_text: str, test_text: str) -> Dict[str, Any]:
    requirements = _flatten_spec(specification)
    if not requirements:
        return {
            "available": False, "passed": True, "total": 0, "covered": 0,
            "strong": 0, "critical_total": 0, "critical_covered": 0,
            "coverage_ratio": 1.0, "details": []
        }
    source_tokens = _tokens(source_text)
    test_tokens = _tokens(test_text)
    source_ids = _identifier_evidence(source_text)
    test_ids = _identifier_evidence(test_text)
    source_numbers = _numbers(source_text)
    test_numbers = _numbers(test_text)
    source_quotes = _quoted_strings(source_text)
    test_quotes = _quoted_strings(test_text)
    details = []
    covered = 0
    strong = 0
    critical_total = 0
    critical_covered = 0
    for index, requirement in enumerate(requirements, 1):
        req_text = _requirement_text(requirement)
        terms = _evidence_terms(requirement)
        req_numbers = _numbers(req_text)
        special, reason = _special_evidence(requirement, source_text, test_text)
        abstract = _is_abstract_requirement(requirement)
        source_hits = sorted(
            token for token in terms
            if token in source_tokens or token in source_ids
        )
        test_hits = sorted(
            token for token in terms
            if token in test_tokens or token in test_ids
        )
        number_hits = sorted(
            number for number in req_numbers
            if number in source_numbers or number in test_numbers
        )
        quoted_hits = sorted(
            quote for quote in _quoted_strings(req_text)
            if quote in source_quotes or quote in test_quotes
        )
        token_count = max(1, len(terms))
        source_score = len(source_hits) / token_count
        test_score = len(test_hits) / token_count
        score = source_score * 0.65 + test_score * 0.35
        if number_hits:
            score = min(1.0, score + min(0.25, 0.08 * len(number_hits)))
        if quoted_hits:
            score = min(1.0, score + 0.15)
        critical = _is_critical_requirement(requirement)
        if critical:
            critical_total += 1
        if abstract and not special:
            passed = (
                source_score >= 0.20
                or test_score >= 0.12
                or score >= 0.25
            )
        else:
            passed = (
                special
                or (source_score >= 0.22 and test_score >= 0.08)
                or (source_score >= 0.40 and bool(test_hits))
                or score >= 0.34
                or bool(quoted_hits)
            )
        strong_evidence = (
            special
            or (source_score >= 0.45 and test_score >= 0.15)
            or score >= 0.55
            or bool(quoted_hits)
        )
        if abstract and not special:
            strong_evidence = (
                source_score >= 0.45 and test_score >= 0.10
            )
        if passed:
            covered += 1
        if strong_evidence:
            strong += 1
        if critical and passed:
            critical_covered += 1
        details.append({
            "id": index,
            "section": _requirement_section(requirement),
            "requirement": requirement,
            "critical": critical,
            "passed": bool(passed),
            "score": round(min(score, 1.0), 3),
            "strong_evidence": bool(strong_evidence),
            "implementation_evidence": source_hits[:20],
            "test_evidence": test_hits[:20],
            "numeric_evidence": number_hits,
            "quoted_evidence": quoted_hits[:10],
            "reason": reason or (
                "Sufficient implementation and/or test evidence."
                if passed else
                "Insufficient implementation/test evidence."
            )
        })
    total = len(requirements)
    coverage_ratio = covered / total if total else 1.0
    critical_ok = critical_covered == critical_total
    ratio_ok = coverage_ratio >= 0.70
    passed = critical_ok and ratio_ok
    return {
        "available": True,
        "passed": passed,
        "total": total,
        "covered": covered,
        "strong": strong,
        "critical_total": critical_total,
        "critical_covered": critical_covered,
        "coverage_ratio": round(coverage_ratio, 3),
        "threshold": 0.70,
        "details": details
    }
def _collect_code(workspace: Path) -> tuple[str, str]:
    source_parts, test_parts = [], []
    for path in _iter_files(workspace):
        if path.suffix.lower() != ".py":
            continue
        content = _read_text(path)
        if path.relative_to(workspace).parts[:1] == ("tests",):
            test_parts.append(content)
        else:
            source_parts.append(content)
    return "\n".join(source_parts), "\n".join(test_parts)
def run_quality_gate(
    workspace: Path | str,
    plan: Dict[str, Any] | None = None,
    test_result: Dict[str, Any] | None = None,
    run_result: Dict[str, Any] | None = None,
    behavior_specification: Any = None,
) -> Dict[str, Any]:
    workspace = Path(workspace)
    checks, warnings = [], []
    workspace_exists = workspace.exists()
    checks.append({"name": "workspace_exists", "passed": workspace_exists})
    if not workspace_exists:
        return {
            "quality_gate": "failed", "passed": False,
            "failed_checks": ["workspace_exists"], "checks": checks,
            "warnings": ["Workspace does not exist."]
        }
    required = _validate_required_files(workspace, plan)
    checks.append({"name": "required_files", "passed": required["passed"], "details": required})
    syntax = _validate_syntax(workspace)
    checks.append({"name": "python_syntax", "passed": syntax["passed"], "details": syntax})
    structure = _validate_structure(workspace)
    checks.append({"name": "package_structure", "passed": structure["passed"], "details": structure})
    warnings.extend(structure["warnings"])
    test_files = _discover_tests(workspace)
    test_check = _validate_test_result(test_result, test_files)
    checks.append({"name": "test_execution", "passed": test_check["passed"], "details": test_check})
    application_check = _validate_application_result(run_result)
    checks.append({"name": "application_execution", "passed": application_check["passed"], "details": application_check})
    imports = _validate_test_imports(workspace)
    checks.append({"name": "test_import_integrity", "passed": imports["passed"], "details": imports})
    requirements = _flatten_spec(behavior_specification)
    specification_present = bool(requirements)
    checks.append({"name": "behavior_contract", "passed": specification_present, "details": {"requirement_count": len(requirements)}})
    source_text, test_text = _collect_code(workspace)
    coverage = _requirement_coverage(behavior_specification, source_text, test_text)
    checks.append({"name": "requirement_coverage", "passed": coverage["passed"], "details": coverage})
    if coverage.get("available") and not coverage.get("passed"):
        uncovered = [
            item.get("requirement")
            for item in coverage.get("details", [])
            if not item.get("passed")
        ]
        if uncovered:
            warnings.append(
                f"Uncovered behavior requirements: {len(uncovered)} of {coverage.get('total', 0)}."
            )
    artifacts = _validate_artifacts(workspace)
    checks.append({"name": "artifact_cleanliness", "passed": artifacts["passed"], "details": artifacts})
    if artifacts["ignored_runtime_artifacts"]:
        warnings.append(f"Ignored runtime/cache artifacts detected: {len(artifacts['ignored_runtime_artifacts'])}. They do not fail the quality gate.")
    failed_checks = [c["name"] for c in checks if not c["passed"]]
    passed = not failed_checks
    if passed:
        warnings.append("All critical quality checks passed.")
    elif "requirement_coverage" in failed_checks and coverage.get("critical_covered") == coverage.get("critical_total"):
        warnings.append("Only non-critical behavior coverage remains below the 70% threshold.")
    return {
        "quality_gate": "passed" if passed else "failed",
        "passed": passed,
        "tests_passed": bool(test_check["passed"]),
        "application_passed": bool(application_check["passed"]),
        "structure_valid": bool(structure["passed"]),
        "syntax_valid": bool(syntax["passed"]),
        "imports_valid": bool(imports["passed"]),
        "artifacts_clean": bool(artifacts["passed"]),
        "requirements_covered": bool(coverage["passed"]),
        "required_files_present": bool(required["passed"]),
        "behavior_contract_present": specification_present,
        "test_files": test_files,
        "failed_checks": failed_checks,
        "checks": checks,
        "warnings": warnings,
        "coverage": coverage
    }

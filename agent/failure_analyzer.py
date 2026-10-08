"""
AutoDev Agent - Deterministic Failure Analyzer

Classifies execution failures before an LLM repair is requested.
No network calls, no model calls, and no external dependencies.
"""

import re
from typing import Any, Dict


def _combined_output(
    test_result: Dict[str, Any] | None = None,
    run_result: Dict[str, Any] | None = None,
) -> str:
    parts = []
    for result in (test_result, run_result):
        if not isinstance(result, dict):
            continue
        for key in ("stderr", "stdout"):
            value = str(result.get(key) or "")
            if value:
                parts.append(value)
    return "\n".join(parts)


def analyze_failure(
    *,
    test_result: Dict[str, Any] | None = None,
    run_result: Dict[str, Any] | None = None,
    phase: str = "unknown",
    project: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Return a deterministic diagnosis.

    Categories:
    - timeout
    - structure_error
    - import_error
    - dependency_error
    - syntax_error
    - cli_usage_error
    - missing_file
    - permission_error
    - runtime_name_error
    - runtime_type_error
    - runtime_attribute_error
    - logic_error
    - runtime_error
    - unknown
    """
    output = _combined_output(test_result, run_result)
    lower = output.lower()
    project = project or {}

    if "timed out" in lower or "timeout" in lower:
        return _diagnosis(
            "timeout",
            "high",
            "The command exceeded the execution timeout.",
            _evidence(output, ("timed out", "timeout")),
            "Inspect the command and remove hangs, infinite loops, or unexpectedly slow work.",
        )

    if (
        "__path__ attribute not found" in lower
        or "cannot find 'app.main'" in lower
        or "cannot find module" in lower
        or "is not a package" in lower
        or "package/module conflict" in lower
    ):
        return _diagnosis(
            "structure_error",
            "high",
            "A Python package/module structure is inconsistent with the command or imports.",
            _evidence(output, ("__path__ attribute not found", "is not a package", "cannot find 'app.main'")),
            "Preserve existing package boundaries and repair imports or file placement without collapsing packages into modules.",
        )

    if "modulenotfounderror" in lower or "no module named" in lower or "importerror" in lower:
        missing = _extract_missing_module(output)
        top = missing.split(".")[0] if missing else ""
        project_tops = {
            path.replace("\\", "/").split("/")[0].replace(".py", "")
            for path in project
        }
        stdlib = {
            "unittest", "typing", "pathlib", "json", "os", "sys", "math",
            "re", "ast", "datetime", "time", "sqlite3", "collections",
            "statistics", "string", "random", "subprocess", "uuid",
        }
        category = "dependency_error"
        if top in project_tops or top in stdlib or top == "app":
            category = "import_error"
        return _diagnosis(
            category,
            "high",
            f"An import could not be resolved{f': {missing}' if missing else '.'}",
            _evidence(output, ("modulenotfounderror", "no module named", "importerror")),
            "Resolve the import against the actual project structure; if it is a third-party package, add only a genuinely required dependency.",
        )

    if "syntaxerror" in lower or "indentationerror" in lower or "taberror" in lower:
        return _diagnosis(
            "syntax_error",
            "high",
            "The generated source contains invalid Python syntax or indentation.",
            _evidence(output, ("syntaxerror", "indentationerror", "taberror")),
            "Fix the reported syntax/indentation issue while preserving the existing behavior contract.",
        )

    if re.search(r"\busage:\s", lower) and (
        "error:" in lower or "expected" in lower
    ):
        return _diagnosis(
            "cli_usage_error",
            "medium",
            "The application exited because its command-line interface arguments were not satisfied.",
            _evidence(output, ("usage:", "error:")),
            "Make the documented/default invocation valid, or align the run command with the application's intended CLI contract.",
        )

    if "filenotfounderror" in lower or "no such file or directory" in lower:
        return _diagnosis(
            "missing_file",
            "high",
            "The application attempted to access a file or path that does not exist.",
            _evidence(output, ("filenotfounderror", "no such file or directory")),
            "Create or reference the correct project-relative resource and avoid relying on unavailable external files.",
        )

    if "permissionerror" in lower or "permission denied" in lower:
        return _diagnosis(
            "permission_error",
            "high",
            "The process was denied access to a file or resource.",
            _evidence(output, ("permissionerror", "permission denied")),
            "Use a writable project-local location and remove unnecessary privileged operations.",
        )

    if "assertionerror" in lower or "failed (" in lower or "\nfail:" in lower or "\nfailed:" in lower:
        return _diagnosis(
            "logic_error",
            "high",
            "The generated implementation does not satisfy one or more executable behavior checks.",
            _evidence(output, ("assertionerror", "failed (", "fail:", "failed:")),
            "Fix the implementation to satisfy the existing tests and behavior specification; do not weaken or delete tests.",
        )

    if "nameerror" in lower:
        return _diagnosis(
            "runtime_name_error",
            "high",
            "The application referenced a name that is not defined in the current scope.",
            _evidence(output, ("nameerror",)),
            "Define or correctly import the referenced symbol and preserve the module's public API.",
        )

    if "typeerror" in lower:
        return _diagnosis(
            "runtime_type_error",
            "high",
            "The application used an incompatible value or function signature.",
            _evidence(output, ("typeerror",)),
            "Correct the value types or call signature at the root cause.",
        )

    if "attributeerror" in lower:
        return _diagnosis(
            "runtime_attribute_error",
            "high",
            "The application accessed an attribute that the target object does not provide.",
            _evidence(output, ("attributeerror",)),
            "Use the real API exposed by the target object and preserve existing behavior.",
        )

    if output.strip():
        return _diagnosis(
            "runtime_error",
            "medium",
            f"The {phase} phase failed with an unclassified runtime error.",
            output[-600:].strip(),
            "Inspect the traceback, identify the root cause, and make the smallest coherent repair.",
        )

    return _diagnosis(
        "unknown",
        "low",
        f"The {phase} phase failed without useful diagnostic output.",
        "",
        "Inspect the command, project structure, and behavior specification before making a minimal repair.",
    )


def _extract_missing_module(output: str) -> str:
    patterns = [
        r"No module named ['\"]([^'\"]+)['\"]",
        r"cannot import name ['\"]([^'\"]+)['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.I)
        if match:
            return match.group(1)
    return ""


def _evidence(output: str, needles: tuple[str, ...]) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(needle in line.lower() for needle in needles):
            return line[-600:]
    return output[-600:].strip()


def _diagnosis(
    category: str,
    confidence: str,
    summary: str,
    evidence: str,
    recommended_action: str,
) -> Dict[str, Any]:
    return {
        "category": category,
        "severity": "high" if category not in {"unknown", "cli_usage_error"} else "medium",
        "confidence": confidence,
        "summary": summary,
        "evidence": evidence,
        "recommended_action": recommended_action,
    }

"""
AutoDev Agent - Autonomous Repair Test

Purpose:
    Deliberately creates a broken project and verifies that
    AutoDev can detect the failure, repair the project, and
    pass the tests again.

This is a development validation script only.
"""

from agent.executor import execute_project


BROKEN_PLAN = {
    "project_name": "Autonomous Repair Demo",

    "summary": (
        "A simple calculator demonstrating autonomous "
        "error detection and repair."
    ),

    "language": "python",

    # Intentionally executable.
    "run_command": "python app/main.py",

    "test_command": (
        "python -m unittest discover "
        "-s tests -v"
    ),

    # Shared behavior contract required by the strict quality gate.
    "behavior_specification": {
        "goal": (
            "Demonstrate a calculator whose add function "
            "returns the sum of two numbers."
        ),
        "features": [
            "The add(a, b) function returns the sum of a and b."
        ],
        "inputs": [
            "The add function accepts two numeric arguments."
        ],
        "outputs": [
            "The demo application prints the addition result."
        ],
        "business_rules": [
            "Addition returns the mathematical sum of its arguments."
        ],
        "edge_cases": [],
    },

    "files": [
        {
            "path": "app/__init__.py",
            "content": "",
        },

        {
            "path": "app/calculator.py",
            "content": '''
def add(a, b):
    return a - b
''',
        },

        {
            "path": "app/main.py",
            "content": '''
from app.calculator import add


def main():
    result = add(10, 5)
    print(f"10 + 5 = {result}")


if __name__ == "__main__":
    main()
''',
        },

        {
            "path": "README.md",
            "content": """
# Autonomous Repair Demo

A deliberately broken calculator project used
to test AutoDev's autonomous repair capability.
""",
        },
    ],
}


def main():
    print()
    print("=" * 70)
    print("AUTODEV AUTONOMOUS REPAIR TEST")
    print("=" * 70)
    print()

    print("🐛 Deliberate bug:")
    print("   add(a, b) incorrectly returns a - b")
    print()

    result = execute_project(
        plan=BROKEN_PLAN,
        task=(
            "Create a simple Python calculator with "
            "an add function. The add function must "
            "correctly return the sum of two numbers. "
            "The application must print the result."
        ),
    )

    print()
    print("=" * 70)
    print("FINAL RESULT")
    print("=" * 70)

    print(f"Success          : {result.get('success')}")
    print(
        f"Repair attempts  : "
        f"{result.get('repair_attempts')}"
    )

    print()
    print("Repair history:")

    for repair in result.get(
        "repair_history",
        [],
    ):
        print()
        print(
            f"Attempt {repair.get('attempt')} "
            f"({repair.get('phase')})"
        )

        print(
            f"Summary: "
            f"{repair.get('summary')}"
        )

        print(
            "Files changed: "
            + ", ".join(
                repair.get(
                    "files_changed",
                    [],
                )
            )
        )

    print()

    test_result = result.get(
        "test_result"
    )

    if test_result:
        print(
            "Final tests: "
            + (
                "PASS"
                if test_result.get("success")
                else "FAIL"
            )
        )

    run_result = result.get(
        "run_result"
    )

    if run_result:
        print(
            "Final application: "
            + (
                "PASS"
                if run_result.get("success")
                else "FAIL"
            )
        )

    quality_gate = result.get("quality_gate")
    if isinstance(quality_gate, dict):
        print()
        print("Quality gate:")
        print(f"  Passed: {quality_gate.get('passed')}")
        for check in quality_gate.get("checks", []):
            if isinstance(check, dict):
                print(
                    f"  - {check.get('name')}: "
                    f"{'PASS' if check.get('passed') else 'FAIL'}"
                )

    print()
    print(
        f"ZIP: {result.get('zip_file')}"
    )

    print()
    print("=" * 70)

    if (
        result.get("success")
        and result.get("repair_attempts", 0) > 0
    ):
        print(
            "🎉 AUTONOMOUS REPAIR VERIFIED!"
        )
    else:
        print(
            "❌ AUTONOMOUS REPAIR TEST FAILED."
        )

    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
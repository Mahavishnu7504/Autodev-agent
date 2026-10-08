from pathlib import Path
import tempfile

from agent.sandbox_runner import run_in_sandbox


project = Path(tempfile.mkdtemp(prefix="sandbox-test-"))

(project / "main.py").write_text(
    'print("Hello from AutoDev Sandbox")',
    encoding="utf-8",
)

result = run_in_sandbox(
    project_path=project,
    run_command="python main.py",
)

print("\nSANDBOX RESULT")
print("=" * 60)
print(result)
from pathlib import Path
from uuid import uuid4

GENERATED_DIR = Path("generated")
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def create_generated_file(code: str) -> tuple[str, str]:
    file_id = uuid4().hex[:12]
    filename = f"autodev_{file_id}.py"

    file_path = GENERATED_DIR / filename

    file_path.write_text(
        code,
        encoding="utf-8"
    )

    return filename, str(file_path)


def update_generated_file(file_path: str, code: str) -> None:
    Path(file_path).write_text(
        code,
        encoding="utf-8"
    )


def get_generated_file(filename: str) -> Path | None:
    safe_name = Path(filename).name
    file_path = GENERATED_DIR / safe_name

    if not file_path.exists():
        return None

    return file_path
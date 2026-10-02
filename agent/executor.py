import re
import textwrap
from tools.file_tool import create_file
from tools.shell_tool import run_command
from agent.fixer import fix_code


def extract_python_code(plan):
    match = re.search(r"```python(.*?)```", plan, re.DOTALL)
    if match:
        return textwrap.dedent(match.group(1)).strip()
    return None


def clean_code(code):
    code = re.sub(r"```python", "", code)
    code = re.sub(r"```", "", code)
    return code.strip()


def execute_plan(plan):
    filename = "main.py"
    code = extract_python_code(plan)

    if not code:
        return ["❌ No code found"]

    code = clean_code(code)

    print("\n⚙️ Creating file...")
    create_file(filename, code)

    print("\n⚙️ Running code...")

    for attempt in range(3):
        result = run_command("python main.py")

        if result["success"]:
            print("✅ Code ran successfully")
            return result

        print(f"\n❌ Error (attempt {attempt+1}):")
        print(result["stderr"])

        print("\n🛠️ Fixing code...")
        fixed_code = fix_code(result["stderr"], code)

        fixed_code = clean_code(fixed_code)

        if not fixed_code.strip():
            return ["❌ Fixer returned empty code"]

        create_file(filename, fixed_code)
        code = fixed_code

    return ["❌ Failed after retries"]
import subprocess


def run_command(command):
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=20
        )

        success = result.returncode == 0

        return {
            "success": success,
            "stdout": result.stdout,
            "stderr": result.stderr
        }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e)
        }
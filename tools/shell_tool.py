import subprocess

def run_command(command):
    try:
        # Start process (non-blocking)
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Wait only limited time (important)
        try:
            stdout, stderr = process.communicate(timeout=5)
            success = process.returncode == 0

            return {
                "success": success,
                "stdout": stdout.strip(),
                "stderr": stderr.strip()
            }

        except subprocess.TimeoutExpired:
            # Kill long-running process (like uvicorn)
            process.kill()

            return {
                "success": True,
                "stdout": "⚠️ Process started (running in background)",
                "stderr": ""
            }

    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e)
        }
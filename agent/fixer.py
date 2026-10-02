import os
import time
from dotenv import load_dotenv
from groq import Groq
from config import MODEL_FALLBACKS, MAX_RETRIES

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")

if not API_KEY:
    raise ValueError("GROQ_API_KEY not found")

client = Groq(api_key=API_KEY)


def fix_code(code: str, error: str) -> str:

    prompt = f"""You are an expert Python debugger.

The following code failed:

{code}

ERROR:
{error}

Fix the code so it runs successfully.

Rules:
- Return ONLY valid python code
- Do not explain anything
- Do not use input()
- Use hardcoded values
"""

    last_error = None

    for model in MODEL_FALLBACKS:
        for attempt in range(MAX_RETRIES):
            try:
                print(f"Fixing with {model} (attempt {attempt+1})")

                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}]
                )

                return response.choices[0].message.content

            except Exception as e:
                last_error = str(e)
                print(f"Failed: {model} -> {last_error}")
                time.sleep(1)

    return f"# Failed to fix code\n# Error: {last_error}"
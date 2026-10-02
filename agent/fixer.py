import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")

if not API_KEY:
    raise ValueError("❌ GROQ_API_KEY not found in .env")

client = Groq(api_key=API_KEY)

MODEL = "llama3-70b-8192"   # ✅ FIXED


def fix_code(error, code):
    prompt = f"""
Fix this Python code.

Error:
{error}

Code:
{code}

Rules:
- Return ONLY valid python code
- No explanation
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")

if not API_KEY:
    raise ValueError("❌ GROQ_API_KEY not found in .env")

client = Groq(api_key=API_KEY)

def create_plan(user_task: str):
    model = "openai/gpt-oss-20b"  # ✅ define INSIDE function

    prompt = f"""
You are an AI software planner.

Task:
{user_task}

Rules:
- Return step-by-step plan
- Include FULL python code inside ```python block
- Follow user instruction EXACTLY
- Even if it breaks, DO NOT fix
"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

MODEL = "openai/gpt-oss-20b"


def create_plan(user_task):
    prompt = (
    "You are an AI software planner.\n\n"
    "Task:\n"
    + user_task
    + "\n\nRules:\n"
    "- Return steps\n"
    "- When creating code, FOLLOW USER INSTRUCTION EXACTLY\n"
    "- If user says 'do NOT import FastAPI', you MUST NOT import it\n"
    "- Even if code breaks, DO NOT fix it yourself\n"
    "- Let execution fail\n"
    "- Always include full code inside ```python block\n\n"
    "Example:\n\n"
    "```python\n"
    "app = FastAPI()  # This will break intentionally\n"
    "```\n"

    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
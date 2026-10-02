from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

MODEL = "openai/gpt-oss-20b"


def fix_code(error, code):
    prompt = (
        "Fix the following Python code.\n\n"
        "Error:\n" + error + "\n\n"
        "Code:\n" + code + "\n\n"
        "Return ONLY corrected full code."
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content
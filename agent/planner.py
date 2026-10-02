import time
from groq import Groq
from config import GROQ_API_KEY, MODEL_FALLBACKS, MAX_RETRIES

client = Groq(api_key=GROQ_API_KEY)


def create_plan(user_task: str):

    prompt = f"""You are an AI software planner.

Task:
{user_task}

Rules:
- Return step-by-step plan
- Include FULL python code inside ```python block
- Follow user instruction EXACTLY
- Even if it breaks, DO NOT fix
- DO NOT use input()
- Use hardcoded test values instead
"""

    last_error = None

    for model in MODEL_FALLBACKS:
        for attempt in range(MAX_RETRIES):
            try:
                print(f"⚡ Trying model: {model} (attempt {attempt+1})")

                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0
                )

                return response.choices[0].message.content

            except Exception as e:
                last_error = str(e)
                print(f"❌ Failed: {model} -> {last_error}")
                time.sleep(1)

    return f"❌ All models failed: {last_error}"
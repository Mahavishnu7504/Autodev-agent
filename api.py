from fastapi import FastAPI
from pydantic import BaseModel
from agent.planner import create_plan
from agent.executor import execute_plan
from agent.memory import save_memory, search_memory

app = FastAPI()


class TaskRequest(BaseModel):
    task: str


@app.post("/run-task")
def run_task(req: TaskRequest):

    print("🧠 Checking Memory...")
    memory = search_memory(req.task)

    if memory:
        return {
            "status": "success",
            "task": req.task,
            "plan": memory,
            "result": "⚡ Retrieved from memory"
        }

    print("🧠 Generating Plan...")
    plan = create_plan(req.task)

    print("🚀 Executing Plan...")
    result = execute_plan(plan)

    save_memory(req.task, plan)

    return {
        "status": "success" if result["success"] else "error",
        "task": req.task,
        "plan": plan,
        "result": result
    }
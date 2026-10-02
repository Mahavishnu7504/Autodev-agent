from fastapi import FastAPI
from pydantic import BaseModel
from agent.planner import create_plan
from agent.executor import execute_plan

app = FastAPI()

class TaskRequest(BaseModel):
    task: str

@app.get("/")
def home():
    return {"message": "AutoDev Agent API is running 🚀"}

@app.post("/run-task")
def run_task(request: TaskRequest):
    try:
        print("\n🧠 Generating Plan...")
        plan = create_plan(request.task)

        print("\n🚀 Executing Plan...")
        result = execute_plan(plan)

        return {
            "status": "success",
            "task": request.task,
            "plan": plan,
            "result": str(result)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
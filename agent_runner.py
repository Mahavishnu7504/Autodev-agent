from agent.planner import create_plan
from agent.executor import execute_plan
from agent.memory import save_memory, search_memory

task = "Create FastAPI app but do NOT import FastAPI"

print("\n🧠 Checking Memory...")
memory_plan = search_memory(task)

if memory_plan:
    print("\n⚡ Using saved plan")
    plan = memory_plan
else:
    print("\n🧠 Generating Plan...")
    plan = create_plan(task)
    save_memory(task, plan)

print("\n🧠 PLAN:\n")
print(plan)

print("\n🚀 Executing Plan...")
execute_plan(plan)
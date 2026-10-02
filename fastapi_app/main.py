from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def home():
    return {"message": "API is running 🚀"}

@app.get("/hello")
async def read_hello():
    return {"message": "Hello, World!"}
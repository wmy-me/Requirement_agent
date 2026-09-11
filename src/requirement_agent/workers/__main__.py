import uvicorn

if __name__ == "__main__":
    uvicorn.run("requirement_agent.workers.tasks:app", host="0.0.0.0", port=8200, reload=True)

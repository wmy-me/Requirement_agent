import uvicorn

if __name__ == "__main__":
    uvicorn.run("apps.worker.tasks:app", host="0.0.0.0", port=8200, reload=True)

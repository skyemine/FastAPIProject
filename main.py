from fastapi.responses import FileResponse

@app.get("/")
def root():
    return FileResponse("app/static/index.html")
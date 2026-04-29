from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from routers import bins, chat, drug


app = FastAPI()
frontend_file = backend_dir.parent / "yakpool_app_v15.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    if frontend_file.exists():
        return FileResponse(frontend_file)
    return JSONResponse({"message": "server is running", "frontend": "yakpool_app_v15.html not found"})


app.include_router(drug.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(bins.router, prefix="/api")

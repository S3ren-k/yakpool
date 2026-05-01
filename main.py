from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from routers import bins, chat, drug



app = FastAPI()
BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_FILE = BASE_DIR / "yakpool_app_v15.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    if FRONTEND_FILE.exists():
        return FileResponse(FRONTEND_FILE)
    return JSONResponse({"message": "server is running", "frontend": "yakpool_app_v15.html not found"})


app.include_router(drug.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(bins.router, prefix="/api")

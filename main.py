from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import load_environment

load_environment()

from api.routes import router as api_router  # noqa: E402

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"message": "jobis backend running"}


app.include_router(api_router)

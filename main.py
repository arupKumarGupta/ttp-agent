from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.websockets import router as websockets_router

app = FastAPI(title="Unified A2UI Agent Server")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(websockets_router)

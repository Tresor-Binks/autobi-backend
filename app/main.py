from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.routers import auth, analysis
from app.database.session import engine, Base

# Cette ligne crée les tables si elles n'existent pas
Base.metadata.create_all(bind=engine)
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend API pour l'analyse automatique de fichiers Excel avec IA",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Liste explicite pour éviter tout problème de chargement de config
PROD_ORIGINS = [
    "http://localhost:5173",                            # Dev Web local
    "https://autobi-frontend-production.up.railway.app", # Prod Web (Railway)
    "tauri://localhost",                                # Tauri (Windows)
    "http://tauri.localhost",                          # Tauri (macOS/Linux)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=PROD_ORIGINS, # On utilise la liste fixe
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION
    }

app.include_router(auth.router)
app.include_router(analysis.router)

@app.on_event("startup")
async def startup_event():
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} démarré")

@app.on_event("shutdown")
async def shutdown_event():
    print(f"🛑 {settings.APP_NAME} arrêté")
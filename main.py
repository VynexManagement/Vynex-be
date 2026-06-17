import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import admin, catalog, downloads, leads, payments

# ── Logging ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)

# ── App factory ──────────────────────────────
app = FastAPI(
    title="Shopify Lead Generator API",
    description="Premium Shopify store leads with real marketing-signal detection.",
    version="1.0.0",
)

# ── CORS ─────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        # Add production domain here when deploying
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────
app.include_router(leads.router)
app.include_router(catalog.router)
app.include_router(payments.router)
app.include_router(downloads.router)
app.include_router(admin.router)


# ── Health check ─────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {
        "service": "Shopify Lead Generator API",
        "status": "ok",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth import CurrentUser
from app.config import settings
from app.db import SessionDep
from app.routers import demo, internal, invoices, ledger, purchase_orders, vendors
from app.workflow import WorkflowError

app = FastAPI(title="PaymentLedger ledger-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(WorkflowError)
async def workflow_error(_request: Request, exc: WorkflowError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": {"code": exc.code, "message": exc.message, **exc.detail}},
    )


@app.get("/health")
async def health(session: SessionDep):
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/me")
def me(principal: CurrentUser):
    return principal


app.include_router(vendors.router)
app.include_router(purchase_orders.router)
app.include_router(invoices.router)
app.include_router(ledger.router)
app.include_router(internal.router)
app.include_router(demo.router)

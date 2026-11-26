"""FastAPI application for single-day wheelchair-access routing."""

from __future__ import annotations

import re

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import (
    CheckInCreate,
    CheckInRequest,
    ClosureCreate,
    GuideOrderCreate,
    PassageCreate,
    PointCreate,
)
from .seed import seed_demo
from .store import (
    ClosureBlockedError,
    ConflictError,
    DuplicateError,
    NotFoundError,
    OrderNotIssuedError,
    StoreError,
    store,
)

def unique_operation_id(route) -> str:
    """Preserve hyphen vs underscore aliases so operation IDs stay unique."""
    method = next(iter(route.methods or {"get"})).lower()
    base = f"{route.name}{route.path_format}"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", base)
    return f"{base}_{method}"


app = FastAPI(
    title="Museum Step-free Passage API",
    description=(
        "Single-day routing for wheelchair-accessible museum passages, "
        "wait-only-at-wait-point rules, check-ins, and closure impact checks."
    ),
    version="1.0.0",
    generate_unique_id_function=unique_operation_id,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


def blocked_closure_content(exc: ClosureBlockedError) -> dict[str, object]:
    blocked_ids = [order.id for order, _ in exc.blocked]
    blocked_orders = [
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "latest_report": jsonable_encoder(order.latest_report)
            or {
                "point_id": order.start_point_id,
                "minute": order.departure_minute,
            },
            "destination_point_id": order.destination_point_id,
            "deadline_minute": order.deadline_minute,
            "route": jsonable_encoder(route),
        }
        for order, route in exc.blocked
    ]
    return {
        "detail": (
            "closure was not applied because one or more active guide orders "
            "cannot reach their destination on time"
        ),
        "effective": False,
        "applied": False,
        "blocked_order_ids": blocked_ids,
        "blocked_guide_order_ids": blocked_ids,
        "blocked_order_nos": blocked_ids,
        "blocked_order_numbers": blocked_ids,
        "blocked_orders": blocked_orders,
        "proposed_closure": jsonable_encoder(exc.candidate),
        "closures": jsonable_encoder(store.list_closures()),
        "original_closures_preserved": True,
    }


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(DuplicateError)
async def duplicate_handler(_: Request, exc: DuplicateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OrderNotIssuedError)
async def order_not_issued_handler(
    _: Request, exc: OrderNotIssuedError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "guide order was not issued because no on-time route exists",
            "issued": False,
            "feasible": False,
            "blocked_order_ids": [],
            "route": jsonable_encoder(exc.route),
        },
    )


@app.exception_handler(ClosureBlockedError)
async def closure_blocked_handler(
    _: Request, exc: ClosureBlockedError
) -> JSONResponse:
    return JSONResponse(status_code=409, content=blocked_closure_content(exc))


@app.exception_handler(StoreError)
async def store_error_handler(_: Request, exc: StoreError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/")
def root() -> dict[str, object]:
    return {
        "name": "Museum Step-free Passage API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "canonical_prefix": "/api",
        "endpoints": {
            "points": ["/api/points", "/api/locations"],
            "passages": ["/api/passages", "/api/corridors"],
            "guide_orders": [
                "/api/guide-orders",
                "/api/guide-sheets",
                "/api/guide_orders",
                "/api/orders",
            ],
            "closures": ["/api/closures", "/api/closures/evaluate"],
            "demo_seed": "/api/demo/seed",
        },
    }


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "points": len(store.points),
        "passages": len(store.passages),
        "closures": len(store.closures),
        "guide_orders": len(store.orders),
    }


@app.post("/api/admin/reset")
@app.post("/admin/reset")
def reset_state() -> dict[str, str]:
    store.reset()
    return {"status": "reset"}


@app.post("/api/demo/seed")
@app.post("/demo/seed")
def seed_demo_data() -> dict[str, object]:
    ids = seed_demo(store)
    return {"status": "seeded", **ids, "health": health()}


# ---------- points ----------


@router.post("/points")
@router.post("/locations")
def create_point(payload: PointCreate):
    return store.create_point(payload)


@router.get("/points")
@router.get("/locations")
def list_points():
    return store.list_points()


@router.get("/points/{point_id}")
@router.get("/locations/{point_id}")
def get_point(point_id: str):
    return store.get_point(point_id)


# ---------- passages / corridors ----------


@router.post("/passages")
@router.post("/corridors")
def create_passage(payload: PassageCreate):
    return store.create_passage(payload)


@router.get("/passages")
@router.get("/corridors")
def list_passages():
    return store.list_passages()


@router.get("/passages/{passage_id}")
@router.get("/corridors/{passage_id}")
def get_passage(passage_id: str):
    return store.get_passage(passage_id)


# ---------- guide orders ----------


@router.post("/guide-orders")
@router.post("/guide-sheets")
@router.post("/guide_orders")
@router.post("/guide_sheets")
@router.post("/guides")
@router.post("/orders")
def create_guide_order(payload: GuideOrderCreate):
    return store.create_order(payload)


@router.get("/guide-orders")
@router.get("/guide-sheets")
@router.get("/guide_orders")
@router.get("/guide_sheets")
@router.get("/guides")
@router.get("/orders")
def list_guide_orders(
    status: str | None = Query(default=None),
    active: bool | None = Query(default=None),
):
    orders = store.list_orders(status=status)
    if active is True:
        orders = [order for order in orders if order.status != "completed"]
    if active is False:
        orders = [order for order in orders if order.status == "completed"]
    return orders


@router.get("/guide-orders/{order_id}")
@router.get("/guide-sheets/{order_id}")
@router.get("/guide_orders/{order_id}")
@router.get("/guide_sheets/{order_id}")
@router.get("/guides/{order_id}")
@router.get("/orders/{order_id}")
def get_guide_order(order_id: str):
    return store.get_order(order_id)


@router.get("/guide-orders/{order_id}/route")
@router.get("/guide-sheets/{order_id}/route")
@router.get("/guide_orders/{order_id}/route")
@router.get("/guides/{order_id}/route")
@router.get("/orders/{order_id}/route")
def get_guide_order_route(order_id: str):
    order = store.get_order(order_id)
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "feasible": order.feasible,
        "latest_report": order.latest_report
        or {"point_id": order.start_point_id, "minute": order.departure_minute},
        "route": order.current_route,
    }


@router.post("/guide-orders/{order_id}/check-ins")
@router.post("/guide-orders/{order_id}/check-in")
@router.post("/guide-orders/{order_id}/checkins")
@router.post("/guide-orders/{order_id}/check_ins")
@router.post("/guide-orders/{order_id}/reports")
@router.post("/guide-sheets/{order_id}/check-ins")
@router.post("/guide-sheets/{order_id}/check-in")
@router.post("/guide-sheets/{order_id}/checkins")
@router.post("/guide_orders/{order_id}/check-ins")
@router.post("/guide_orders/{order_id}/check_in")
@router.post("/guides/{order_id}/check-ins")
@router.post("/guides/{order_id}/check-in")
@router.post("/orders/{order_id}/check-ins")
@router.post("/orders/{order_id}/check-in")
def check_in_guide_order(order_id: str, payload: CheckInCreate):
    return store.check_in(order_id, payload)


@router.post("/check-ins")
@router.post("/checkin")
@router.post("/check_ins")
@router.post("/reports")
def check_in_by_body(payload: CheckInRequest):
    return store.check_in(payload.order_id, payload)


# ---------- closures ----------


@router.post("/closures/evaluate")
@router.post("/closure-proposals/evaluate")
def evaluate_closure(payload: ClosureCreate):
    candidate, blocked, _routes, existing = store.evaluate_closure(payload)
    blocked_ids = [order.id for order, _ in blocked]
    return {
        "effective": not blocked,
        "applied": False,
        "would_apply": not blocked,
        "blocked_order_ids": blocked_ids,
        "blocked_guide_order_ids": blocked_ids,
        "blocked_order_nos": blocked_ids,
        "blocked_orders": [
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "latest_report": order.latest_report
                or {
                    "point_id": order.start_point_id,
                    "minute": order.departure_minute,
                },
                "route": jsonable_encoder(route),
            }
            for order, route in blocked
        ],
        "proposed_closure": candidate,
        "current_closures": existing,
    }


@router.post("/closures")
@router.post("/closure-proposals")
def create_closure(payload: ClosureCreate):
    try:
        closure, rerouted = store.propose_closure(payload)
    except ClosureBlockedError as exc:
        return JSONResponse(
            status_code=409, content=blocked_closure_content(exc)
        )

    return {
        "closure": closure,
        "effective": True,
        "applied": True,
        "blocked_order_ids": [],
        "rerouted_order_ids": [order.id for order in rerouted],
        "rerouted_orders": rerouted,
        "closures": store.list_closures(),
    }


@router.get("/closures")
def list_closures():
    return store.list_closures()


@router.get("/closures/{closure_id}")
def get_closure(closure_id: str):
    return store.get_closure(closure_id)


# Canonical API surface. A lightweight middleware also accepts the same
# resource paths without the /api prefix for clients that guess root paths.
app.include_router(router, prefix="/api")


ROOT_API_ALIASES = (
    "/points",
    "/locations",
    "/passages",
    "/corridors",
    "/guide-orders",
    "/guide-sheets",
    "/guide_orders",
    "/guide_sheets",
    "/guides",
    "/orders",
    "/check-ins",
    "/checkin",
    "/check_ins",
    "/reports",
    "/closures",
    "/closure-proposals",
)


@app.middleware("http")
async def rewrite_root_aliases_to_api(request: Request, call_next):
    path = request.scope.get("path", "")
    if not path.startswith("/api") and any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in ROOT_API_ALIASES
    ):
        request.scope["path"] = "/api" + path
    return await call_next(request)

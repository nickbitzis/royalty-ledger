"""FastAPI service: ingest, matching review, statements, recoupment, analytics, health and metrics."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from . import __version__
from .exports import statement_csv, statement_html
from .logs import get_logger, log_event
from .reports import ReportError
from .service import RoyaltyService, build_data

STATIC = Path(__file__).parent / "static"
BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 10.0)


class Decision(BaseModel):
    action: str
    target: str | None = None


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.http: dict[tuple[str, str], int] = defaultdict(int)
        self.hist = [0] * len(BUCKETS)
        self.sum = 0.0
        self.count = 0
        self.ingested_lines = 0
        self.decisions = 0

    def observe(self, route: str, status: int, seconds: float) -> None:
        with self.lock:
            self.http[(route, str(status))] += 1
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    self.hist[i] += 1
            self.sum += seconds
            self.count += 1

    def render(self, svc: RoyaltyService) -> str:
        out = [
            "# HELP rl_http_requests_total HTTP requests by route and status.",
            "# TYPE rl_http_requests_total counter",
        ]
        with self.lock:
            out += [
                f'rl_http_requests_total{{route="{r}",status="{s}"}} {v}' for (r, s), v in sorted(self.http.items())
            ]
            out += [
                "# HELP rl_request_duration_seconds Request latency.",
                "# TYPE rl_request_duration_seconds histogram",
            ]
            for b, c in zip(BUCKETS, self.hist, strict=True):
                out.append(f'rl_request_duration_seconds_bucket{{le="{b}"}} {c}')
            out += [
                f'rl_request_duration_seconds_bucket{{le="+Inf"}} {self.count}',
                f"rl_request_duration_seconds_sum {self.sum:.6f}",
                f"rl_request_duration_seconds_count {self.count}",
                "# HELP rl_ingested_lines_total Report lines ingested through the API.",
                "# TYPE rl_ingested_lines_total counter",
                f"rl_ingested_lines_total {self.ingested_lines}",
                "# HELP rl_review_decisions_total Matching review decisions.",
                "# TYPE rl_review_decisions_total counter",
                f"rl_review_decisions_total {self.decisions}",
            ]
        ev = svc.match_eval()
        out += [
            "# HELP rl_report_lines Report lines loaded.",
            "# TYPE rl_report_lines gauge",
            f"rl_report_lines {len(svc.lines)}",
            "# HELP rl_match_rate Share of report lines matched to the catalog.",
            "# TYPE rl_match_rate gauge",
            f"rl_match_rate {ev['match_rate'] if ev['lines'] else 0}",
            "# HELP rl_review_queue_items Distinct metadata keys waiting for review.",
            "# TYPE rl_review_queue_items gauge",
            f"rl_review_queue_items {len(svc.matcher.queue)}",
            "# HELP rl_period_difference_cents Allocated minus received, per period. Must be 0.",
            "# TYPE rl_period_difference_cents gauge",
        ]
        for p in svc.rules.periods:
            out.append(f'rl_period_difference_cents{{period="{p}"}} {svc.ledger.tie_out(p)["difference_cents"]}')
        return "\n".join(out) + "\n"


def create_app(data_dir: Path | None = None, seed: int = 7, n_artists: int = 60) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("RL_DATA_DIR", "data"))
    log = get_logger()
    if not (data_dir / "catalog.json").exists():
        t0 = time.perf_counter()
        build_data(data_dir, seed=seed, n_artists=n_artists)
        log_event(log, "generated synthetic data", data_dir=str(data_dir), seconds=round(time.perf_counter() - t0, 2))
    svc = RoyaltyService(data_dir)
    log_event(log, "ledger ready", lines=len(svc.lines), **svc.timings)
    lock = threading.Lock()
    metrics = Metrics()
    app = FastAPI(title="royalty-ledger", version=__version__)
    app.state.svc = svc

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
        t0 = time.perf_counter()
        response = await call_next(request)
        dt = time.perf_counter() - t0
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        metrics.observe(path, response.status_code, dt)
        response.headers["x-request-id"] = rid
        if path not in ("/healthz", "/metrics"):
            log_event(
                log,
                "request",
                request_id=rid,
                method=request.method,
                route=path,
                status=response.status_code,
                ms=round(dt * 1000, 1),
            )
        return response

    def statement_or_404(payee: str, period: str) -> dict:
        try:
            return svc.statement(payee, period)
        except KeyError as e:
            raise HTTPException(404, f"no statement for {payee} in {period}") from e

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__, "lines": len(svc.lines), "periods": svc.rules.periods}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics_endpoint():
        return metrics.render(svc)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/summary")
    def summary():
        return svc.summary()

    @app.get("/api/periods")
    def periods():
        return {"periods": svc.rules.periods}

    @app.get("/api/payees")
    def payees(type: str | None = None):
        return {"payees": svc.payees(type)}

    @app.get("/api/statements/{payee}")
    def statement(payee: str, period: str = Query(...), format: str = "json"):
        st = statement_or_404(payee, period)
        if format == "csv":
            return PlainTextResponse(
                statement_csv(st),
                media_type="text/csv",
                headers={"content-disposition": f'attachment; filename="statement_{payee}_{period}.csv"'},
            )
        if format == "html":
            return HTMLResponse(statement_html(st, svc.rules.data["label"]))
        return st

    @app.get("/api/audit")
    def audit(line: str):
        try:
            return svc.audit(line)
        except KeyError as e:
            raise HTTPException(404, f"unknown statement line {line}") from e

    @app.get("/api/recoupment")
    def recoupment():
        return {"accounts": svc.recoupment()}

    @app.get("/api/recoupment/{account_id}")
    def recoupment_one(account_id: str):
        if account_id not in svc.ledger.accounts or svc.ledger.accounts[account_id]["type"] != "artist":
            raise HTTPException(404, "unknown artist account")
        return svc.recoupment_for(account_id)

    @app.get("/api/review")
    def review():
        return {"items": svc.review_queue(), "evaluation": svc.match_eval()}

    @app.post("/api/review/{key_id}")
    def decide(key_id: str, d: Decision):
        with lock:
            try:
                res = svc.decide(key_id, d.action, d.target)
            except KeyError as e:
                raise HTTPException(404, "not in the review queue") from e
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
        metrics.decisions += 1
        log_event(log, "review decision", **res)
        return res

    @app.post("/api/ingest")
    async def ingest(
        request: Request, name: str = Query(..., pattern=r"^[A-Za-z0-9_.-]+\.(csv|tsv)$"), format: str | None = None
    ):
        body = (await request.body()).decode("utf-8-sig")
        with lock:
            try:
                res = svc.ingest(name, body, format)
            except (ReportError, ValueError, KeyError) as e:
                raise HTTPException(422, str(e)) from e
        metrics.ingested_lines += res["lines"]
        log_event(log, "report ingested", **{k: v for k, v in res.items() if k != "status"}, **res["status"])
        return res

    @app.get("/api/analytics")
    def analytics(top: int = 15):
        return svc.analytics(top)

    @app.get("/api/tieout")
    def tieout():
        return {"periods": [svc.ledger.tie_out(p) for p in svc.rules.periods]}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log_event(log, "unhandled error", error=repr(exc), path=request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app

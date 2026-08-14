"""One-shot source patch: run without Celery, nginx or Qdrant."""

from pathlib import Path

# --- endpoints: skip Celery entirely when no broker is configured ------------
p = Path("app/api/v1/endpoints.py")
s = p.read_text(encoding="utf-8")

old = """    task_id: Optional[str] = None
    try:
        from app.workers.celery_app import execute_pipeline_task

        task = execute_pipeline_task.delay(
            run_id, payload.model_dump(mode="json"), [p.id for p in providers]
        )
        task_id = task.id
    except Exception as exc:"""
new = """    task_id: Optional[str] = None
    if not settings.use_celery:
        # Single-process deployment: run the pipeline on the API event loop.
        # Nothing is lost but horizontal scale, and it removes the broker and
        # the separate worker process from the required infrastructure.
        asyncio.create_task(_run_inline(run_id, payload, [p.id for p in providers]))
        logger.info("run_dispatched_inline", extra={"run_id": run_id})
        return RunAccepted(
            run_id=run.id,
            status=run.status,
            task_id=None,
            websocket_url=f"{settings.api_v1_prefix}/runs/{run_id}/stream",
        )

    try:
        from app.workers.celery_app import execute_pipeline_task

        task = execute_pipeline_task.delay(
            run_id, payload.model_dump(mode="json"), [p.id for p in providers]
        )
        task_id = task.id
    except Exception as exc:"""
assert old in s, "endpoints celery anchor"
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("endpoints: inline dispatch when no broker")

# --- vector service: explicit no-op when no Qdrant is configured -------------
p = Path("app/services/vector_service.py")
s = p.read_text(encoding="utf-8")
old = """    async def client(self) -> Optional[AsyncQdrantClient]:
        if self._client is None:"""
new = """    @property
    def enabled(self) -> bool:
        return bool(settings.qdrant_url.strip())

    async def client(self) -> Optional[AsyncQdrantClient]:
        if not self.enabled:
            return None
        if self._client is None:"""
assert old in s, "vector client anchor"
s = s.replace(old, new, 1)

old = """    async def health(self) -> str:
        client = await self.client()
        if client is None:
            return "unavailable\""""
new = """    async def health(self) -> str:
        if not self.enabled:
            return "disabled"
        client = await self.client()
        if client is None:
            return "unavailable\""""
assert old in s, "vector health anchor"
p.write_text(s.replace(old, new, 1), encoding="utf-8")
print("vector_service: disabled when no QDRANT_URL")

# --- main: serve the built SPA so no web server is required ------------------
p = Path("app/main.py")
s = p.read_text(encoding="utf-8")

old = """@app.get(settings.metrics_path, tags=["system"])
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)"""
new = """@app.get(settings.metrics_path, tags=["system"])
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _mount_frontend() -> None:
    \"\"\"Serve the built SPA from the API process.

    This is what removes nginx from the required stack: one process serves the
    API, the websocket and the dashboard. Mounted last so it can claim '/'
    without shadowing any API route. Absent build -> API-only, which is a
    legitimate way to run it.
    \"\"\"
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        logger.info("frontend_not_built_serving_api_only", extra={"path": str(dist)})
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def serve_spa(spa_path: str) -> Response:
        # Client-side routes (/runs/<id>, /models) must return the shell, but a
        # missing asset must 404 rather than silently returning HTML.
        candidate = (dist / spa_path).resolve()
        if spa_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        if "." in Path(spa_path).name:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(index)

    logger.info("frontend_mounted", extra={"path": str(dist)})


_mount_frontend()"""
assert old in s, "main metrics anchor"
s = s.replace(old, new, 1)

s = s.replace(
    "from fastapi.middleware.gzip import GZipMiddleware\nfrom fastapi.responses import JSONResponse",
    "from fastapi.middleware.gzip import GZipMiddleware\n"
    "from fastapi.responses import FileResponse, JSONResponse\n"
    "from fastapi.staticfiles import StaticFiles",
)
s = s.replace(
    "import time\nimport uuid\nfrom contextlib import asynccontextmanager",
    "import time\nimport uuid\nfrom contextlib import asynccontextmanager\nfrom pathlib import Path",
)
p.write_text(s, encoding="utf-8")
print("main: SPA mounted")

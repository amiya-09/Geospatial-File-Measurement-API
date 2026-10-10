from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.files import router as files_router
from app.db.session import get_engine, init_db
from app.errors import install_error_handlers
from app.middleware import BodyLimitMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db(get_engine())  # create tables at startup
    yield


def create_app() -> FastAPI:
    # redirect_slashes=False: the spec paths have exact trailing slashes, and a 307 redirect
    # would silently drop a POST body.
    app = FastAPI(
        title="Geospatial File Measurement API",
        version="0.1.0",
        redirect_slashes=False,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(files_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

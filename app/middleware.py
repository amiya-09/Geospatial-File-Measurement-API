"""Request body cap, enforced before the multipart parser or any geo parsing runs."""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import config

# Room for the multipart envelope (boundaries, headers, filename) around the file bytes.
# The exact file-size cap is still enforced by the route on the bytes it reads.
ENVELOPE_SLACK = 64 * 1024


def _limit() -> int:
    return config.MAX_UPLOAD_BYTES + ENVELOPE_SLACK  # read per request so tests can patch it


class BodyLimitMiddleware:
    """Pure ASGI middleware.

    A Content-Length over the cap is rejected with 413 without reading any of the body.
    Without Content-Length (chunked), received bytes are counted and the request is cut off
    with 413 as soon as the cap is passed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit()
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False  # malformed length: let the server layer deal with it
            if too_big:
                await self._reject(send, limit)
                return

        received = 0
        started = False
        rejected = False

        async def counting_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Do not raise: FastAPI would turn the error into a 400. Look like a
                    # disconnect to the app, drop whatever it replies, answer 413 ourselves.
                    rejected = True
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            if not rejected:
                raise
        if rejected and not started:
            await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "payload_too_large",
                    "message": f"request body is larger than {limit} bytes",
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

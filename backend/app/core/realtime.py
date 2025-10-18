import socketio

# Сервер Socket.IO (ASGI)
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

def with_socketio(app):
    """
    Оборачивает FastAPI-приложение в ASGI-приложение Socket.IO,
    не ломая обычные HTTP-маршруты.
    """
    from .settings import get_settings
    settings = get_settings()
    return socketio.ASGIApp(
        sio,
        other_asgi_app=app,                 # <- важно: FastAPI идёт как "другая" ASGI-апп
        socketio_path=settings.SOCKETIO_PATH  # например: /api/ws/dashboard
    )

ROOM_DASHBOARD = "dashboard"

@sio.event
async def connect(sid, environ, auth):
    await sio.enter_room(sid, ROOM_DASHBOARD)

@sio.event
async def disconnect(sid):
    try:
        await sio.leave_room(sid, ROOM_DASHBOARD)
    except Exception:
        pass

async def emit_dashboard(event: str, data):
    await sio.emit(event, data, room=ROOM_DASHBOARD)


def schedule_dashboard_event(event: str, data) -> None:
    """Fire-and-forget helper that works from sync contexts."""
    try:
        sio.start_background_task(emit_dashboard, event, data)
    except Exception:
        # We intentionally swallow the exception here to avoid breaking request processing
        # and rely on structured logging from the caller.
        pass

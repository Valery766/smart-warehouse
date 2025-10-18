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

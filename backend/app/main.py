import logging
from typing import List

from fastapi import BackgroundTasks, FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, func, case
from datetime import datetime, timedelta
import io, csv, asyncio, httpx

from .core.settings import get_settings
from .core.db import Base, engine, get_db
from .core.security import (
    hash_password,
    verify_password,
    create_jwt,
    get_current_user,
    require_roles,
    verify_robot_ingest_token,
)
from .core.realtime import with_socketio, emit_dashboard, schedule_dashboard_event
from .models import User, Robot, Product, InventoryHistory
from .schemas import (
    LoginRequest, LoginResponse, UserOut, APIError, RobotIngestRequest, RobotIngestResponse,
    BootstrapResponse, RobotDTO, StatPoint, DashboardStats, RecentScan,
    HistoryResponse, HistoryItem, AIPredictRequest, AIPredictResponse, AIPredictItem
)

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

allowed_origins = settings.cors_origins_list
allow_all_origins = allowed_origins == ["*"]

# создаём обычное FastAPI-приложение
_app = FastAPI(title="Smart Warehouse Backend", version="1.0.0")

# CORS: разрешаем всё (по требованиям кейса)
_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=not allow_all_origins,
)

@_app.on_event("startup")
def on_start():
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)

    if settings.INITIALIZE_DEMO_DATA:
        with engine.begin() as conn:
            email = settings.DEMO_OPERATOR_EMAIL
            user = conn.execute(select(User).where(User.email == email)).scalar_one_or_none()
            password = settings.DEMO_OPERATOR_PASSWORD
            if not password:
                logger.warning("INITIALIZE_DEMO_DATA is enabled but DEMO_OPERATOR_PASSWORD is not provided; skipping user seed")
            elif not user:
                conn.execute(
                    User.__table__.insert().values(
                        email=email,
                        password_hash=hash_password(password),
                        name="Оператор",
                        role="operator",
                        created_at=datetime.utcnow(),
                    )
                )

            existing = conn.execute(select(func.count(Product.id))).scalar() or 0
            if existing < 3:
                conn.execute(
                    Product.__table__.insert(),
                    [
                        {
                            "id": "TEL-4567",
                            "name": "Роутер RT-AC68U",
                            "category": "Сетевое",
                            "min_stock": 10,
                            "optimal_stock": 100,
                        },
                        {
                            "id": "TEL-6789",
                            "name": "IP-телефон T46S",
                            "category": "Телефония",
                            "min_stock": 15,
                            "optimal_stock": 120,
                        },
                        {
                            "id": "TEL-3456",
                            "name": "Кабель UTP Cat6",
                            "category": "Кабели",
                            "min_stock": 50,
                            "optimal_stock": 500,
                        },
                    ],
                )

@_app.get("/health")
def health(): 
    return {"status":"ok"}

# ---------- AUTH ----------
@_app.post(f"{settings.API_PREFIX}/auth/login", response_model=LoginResponse, responses={401:{"model":APIError}})
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email==body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, detail="Invalid credentials")
    token = create_jwt({"sub":user.id, "email":user.email, "role":user.role, "name":user.name})
    return LoginResponse(token=token, user=UserOut(id=user.id, name=user.name, role=user.role, email=user.email))

# ---------- ROBOTS INGEST ----------
def _normalize_status(raw_status: str | None) -> str:
    if not raw_status:
        return "OK"
    value = raw_status.upper()
    if value in {"OK", "LOW_STOCK", "CRITICAL"}:
        return value
    mapping = {
        "LOW": "LOW_STOCK",
        "CRIT": "CRITICAL",
        "CRITICAL": "CRITICAL",
    }
    return mapping.get(value, "OK")


@_app.post(
    f"{settings.API_PREFIX}/robots/data",
    response_model=RobotIngestResponse,
    dependencies=[Depends(verify_robot_ingest_token)],
)
def robots_data(
    background_tasks: BackgroundTasks,
    body: RobotIngestRequest,
    db: Session = Depends(get_db),
):
    logger.debug("Robot %s ingest message %s", body.robot_id, body.message_id)
    rid = body.robot_id
    robot = db.get(Robot, rid) or Robot(id=rid)
    db.add(robot)
    robot.battery_level = body.battery_level
    robot.last_update = body.timestamp
    robot.current_zone = body.location.zone
    robot.current_row = body.location.row
    robot.current_shelf = body.location.shelf
    robot.status = "active" if body.battery_level >= 20 else "low"
    product_ids = {s.product_id for s in body.scan_results if s.product_id}
    existing_products = {}
    if product_ids:
        existing_products = {
            p.id: p
            for p in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars().all()
        }

    for scan in body.scan_results:
        if not scan.product_id:
            logger.warning("Skipping scan result without product_id: %s", scan)
            continue
        product = existing_products.get(scan.product_id)
        if not product:
            product = Product(
                id=scan.product_id,
                name=scan.product_name or scan.product_id,
                category="Прочее",
            )
            db.add(product)
            existing_products[product.id] = product

        normalized_status = _normalize_status(scan.status)
        history = InventoryHistory(
            robot_id=rid,
            product_id=product.id,
            product_name=scan.product_name or product.name,
            quantity=scan.quantity,
            zone=body.location.zone,
            row_number=body.location.row,
            shelf_number=body.location.shelf,
            status=normalized_status,
            scanned_at=body.timestamp,
        )
        db.add(history)
    db.commit()
    payload = {
        "robot_id": rid,
        "zone": body.location.zone,
        "row": body.location.row,
        "battery": body.battery_level,
        "status": robot.status,
        "time": body.timestamp.isoformat(),
    }
    background_tasks.add_task(schedule_dashboard_event, "robot_update", payload)
    mid = body.message_id or f"msg-{rid}-{int(datetime.utcnow().timestamp())}"
    return RobotIngestResponse(message_id=mid)

# ---------- BOOTSTRAP ----------
@_app.get(f"{settings.API_PREFIX}/bootstrap", response_model=BootstrapResponse)
def bootstrap(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    logger.debug("Bootstrap requested by %s", current_user.email)
    robots: List[RobotDTO] = []
    now = datetime.utcnow()
    for r in db.execute(select(Robot)).scalars().all():
        st = "offline" if (now-(r.last_update or now)).total_seconds()>60 else ("low" if (r.battery_level or 0)<20 else "active")
        robots.append(RobotDTO(id=r.id, battery=round(r.battery_level or 0,1),
                               updated=r.last_update or now, zone=r.current_zone or "A",
                               row=r.current_row or 1, status=st))
    recent_rows = (
        db.execute(
            select(InventoryHistory)
            .order_by(InventoryHistory.scanned_at.desc())
            .options(joinedload(InventoryHistory.product))
            .limit(20)
        )
        .scalars()
        .all()
    )
    recent = [
        RecentScan(
            time=row.scanned_at,
            robot_id=row.robot_id,
            zone=row.zone,
            product_id=row.product_id,
            product_name=row.product_name,
            qty=row.quantity,
            status=row.status,
        )
        for row in recent_rows
    ]
    total = len(robots)
    active = sum(1 for r in robots if r.status=="active")
    avg_batt = int(round(sum(r.battery for r in robots)/total,0)) if total>0 else 0
    today = now.date()
    from sqlalchemy import cast, Date
    counts_row = db.execute(
        select(
            func.count().label("checked"),
            func.coalesce(
                func.sum(case((InventoryHistory.status == "CRITICAL", 1), else_=0)),
                0,
            ).label("critical"),
        ).where(cast(InventoryHistory.scanned_at, Date) == today)
    ).one()
    checked = counts_row.checked or 0
    critical = counts_row.critical or 0

    buckets = [0 for _ in range(12)]
    interval_start = now - timedelta(minutes=55)
    history_rows = db.execute(
        select(InventoryHistory.scanned_at).where(InventoryHistory.scanned_at >= interval_start)
    ).all()
    for (scanned_at,) in history_rows:
        if scanned_at is None:
            continue
        diff_seconds = max(0, (now - scanned_at).total_seconds())
        offset = min(11, int(diff_seconds // 300))
        bucket_index = 11 - offset
        buckets[bucket_index] += 1
    points = []
    for i in range(12):
        label_time = now - timedelta(minutes=5 * (11 - i)) + timedelta(minutes=2, seconds=30)
        points.append(StatPoint(t=label_time.strftime("%H:%M"), v=buckets[i]))
    zone_metrics = db.execute(
        select(
            InventoryHistory.zone,
            func.sum(case((InventoryHistory.status == "CRITICAL", 1), else_=0)).label("critical"),
            func.count().label("total"),
        )
        .group_by(InventoryHistory.zone)
    ).all()
    zones_heat: dict[str, float] = {}
    for zone_value, crit_count, total_count in zone_metrics:
        if total_count:
            zones_heat[zone_value] = round((crit_count or 0) / total_count, 3)
    stats = DashboardStats(active=active,total=total,checked=checked,critical=critical,avgBatt=avg_batt,activity=points)
    return BootstrapResponse(robots=robots, recent=recent, stats=stats, zonesHeat=zones_heat)

# ---------- HISTORY ----------
@_app.get(f"{settings.API_PREFIX}/inventory/history", response_model=HistoryResponse)
def history(from_: str = None, to: str = None, zone: str = None, status: str = None,
            q: str = None, page: int = 1, size: int = 20, sort: str = "scanned_at", dir: str = "desc",
            db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    logger.debug("History requested by %s with filters zone=%s status=%s", current_user.email, zone, status)
    from sqlalchemy import or_
    def _parse_dt(value: str | None, label: str) -> datetime | None:
        if not value:
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise HTTPException(400, detail=f"Invalid '{label}' datetime format") from exc

    from_dt = _parse_dt(from_, "from")
    to_dt = _parse_dt(to, "to")
    stmt = select(InventoryHistory)
    if zone:
        stmt = stmt.where(InventoryHistory.zone == zone)
    if status and status.upper() in ("OK", "LOW_STOCK", "CRITICAL"):
        stmt = stmt.where(InventoryHistory.status == status.upper())
    if from_dt:
        stmt = stmt.where(InventoryHistory.scanned_at >= from_dt)
    if to_dt:
        stmt = stmt.where(InventoryHistory.scanned_at <= to_dt)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(InventoryHistory.product_id.ilike(like), InventoryHistory.product_name.ilike(like)))
    col = getattr(InventoryHistory, sort, InventoryHistory.scanned_at)
    size = max(1, min(200, size))
    page = max(1, page)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    stmt = stmt.order_by(col.desc() if dir == "desc" else col)
    offset = max(0, (page-1)) * size
    rows = (
        db.execute(
            stmt.options(joinedload(InventoryHistory.product)).limit(size).offset(offset)
        )
        .scalars()
        .all()
    )
    items: List[HistoryItem] = []
    for r in rows:
        expected = r.product.optimal_stock if r.product and r.product.optimal_stock else r.product.min_stock if r.product else 0
        diff = r.quantity-expected
        items.append(HistoryItem(id=r.id, scanned_at=r.scanned_at.strftime("%Y-%m-%d %H:%M:%S"),
                                 robot_id=r.robot_id, zone=r.zone, product_id=r.product_id, product_name=r.product_name,
                                 expected=expected, quantity=r.quantity, diff=diff, status=r.status))
    return HistoryResponse(total=total, items=items, pagination={"page":page,"size":size})

# ---------- IMPORT CSV ----------
@_app.post(f"{settings.API_PREFIX}/inventory/import")
async def import_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "operator")),
):
    logger.info("Inventory CSV import triggered by %s", current_user.email)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv allowed")
    content = (await file.read()).decode("utf-8", errors="ignore")
    reader = csv.DictReader(content.splitlines(), delimiter=';')
    required = {"product_id","product_name","quantity","zone","date"}
    if not required.issubset(set([c.strip() for c in (reader.fieldnames or [])])):
        raise HTTPException(400, "Missing required columns")
    total = 0; success = 0; errors = []
    try:
        with db.begin():
            for row in reader:
                total += 1
                try:
                    pid = (row.get("product_id") or "").strip()
                    pname = (row.get("product_name") or pid).strip()
                    if not pid:
                        raise ValueError("product_id is required")
                    if not pname:
                        raise ValueError("product_name is required")
                    qty = int((row.get("quantity") or "0").strip())
                    zone = (row.get("zone") or "A").strip()
                    if not zone:
                        raise ValueError("zone is required")
                    date_value = (row.get("date") or datetime.utcnow().isoformat()).strip()
                    day = datetime.fromisoformat(date_value)
                    rnum = int((row.get("row") or row.get("row_number") or "1").strip())
                    shelf = int((row.get("shelf") or row.get("shelf_number") or "1").strip())
                    status = _normalize_status(row.get("status")) if row.get("status") else ("OK" if qty>20 else ("LOW_STOCK" if qty>10 else "CRITICAL"))
                    prod = db.get(Product, pid)
                    if not prod:
                        prod = Product(id=pid, name=pname, category="Импорт")
                        db.add(prod)
                    ih = InventoryHistory(
                        robot_id="RB-IM",
                        product_id=pid,
                        product_name=pname,
                        quantity=qty,
                        zone=zone,
                        row_number=rnum,
                        shelf_number=shelf,
                        status=status,
                        scanned_at=day,
                    )
                    db.add(ih)
                    success += 1
                except Exception as exc:
                    logger.exception("Failed to import row %s", row)
                    errors.append({"row": total, "error": str(exc)})
            if errors:
                raise RuntimeError("import validation errors")
    except RuntimeError:
        raise HTTPException(400, detail={"message": "Import failed", "errors": errors})
    except Exception as exc:
        logger.exception("Unexpected error during CSV import")
        raise HTTPException(500, "Failed to import inventory data") from exc
    asyncio.create_task(emit_dashboard("import_done", {"success":success,"failed":total-success}))
    return {"success": success, "failed": total-success, "errors": errors}

# ---------- EXPORTS ----------
@_app.get(f"{settings.API_PREFIX}/export/excel")
def export_excel(
    ids: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "operator")),
):
    logger.debug("Excel export requested by %s for ids=%s", current_user.email, ids)
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    rows = db.execute(select(InventoryHistory).where(InventoryHistory.id.in_(id_list))).scalars().all()
    import xlsxwriter
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet('Inventory')
    headers = ["id","scanned_at","robot_id","zone","product_id","product_name","quantity","status"]
    for c,h in enumerate(headers): ws.write(0,c,h)
    for r,row in enumerate(rows, start=1):
        ws.write_row(r,0,[row.id, row.scanned_at.strftime("%Y-%m-%d %H:%M:%S"), row.robot_id, row.zone, row.product_id, row.product_name, row.quantity, row.status])
    wb.close(); output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=report.xlsx"})

@_app.get(f"{settings.API_PREFIX}/export/pdf")
def export_pdf(
    ids: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "operator")),
):
    logger.debug("PDF export requested by %s for ids=%s", current_user.email, ids)
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    rows = db.execute(select(InventoryHistory).where(InventoryHistory.id.in_(id_list))).scalars().all()
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    output = io.BytesIO()
    c = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("Helvetica-Bold", 14); c.drawString(40, y, "Inventory Report"); y -= 20
    c.setFont("Helvetica", 10)
    for row in rows:
        line = f"#{row.id} {row.scanned_at:%Y-%m-%d %H:%M:%S} {row.robot_id} {row.zone} {row.product_id} {row.product_name} qty={row.quantity} {row.status}"
        c.drawString(40, y, line); y -= 14
        if y < 40: c.showPage(); y = height - 40
    c.save(); output.seek(0)
    return StreamingResponse(output, media_type="application/pdf",
                             headers={"Content-Disposition": "attachment; filename=report.pdf"})

# ---------- AI PREDICT (proxy) ----------
@_app.post(f"{settings.API_PREFIX}/ai/predict", response_model=AIPredictResponse)
async def ai_predict(
    body: AIPredictRequest,
    current_user: User = Depends(get_current_user),
):
    logger.debug("AI predict requested by %s for %d days", current_user.email, body.period_days)
    try:
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            payload = {"period_days": body.period_days}
            if body.categories:
                payload["categories"] = body.categories
            response = await client.post(
                f"{settings.AI_BASE_URL}/predict",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("AI prediction request failed")
        raise HTTPException(502, "Failed to fetch prediction from AI service") from exc
    items = [AIPredictItem(product_id=p.get("product_id","UNK"), product_name="Товар",
                            current_stock=p.get("current_stock",0), stockout_date=p.get("stockout_date","1970-01-01"),
                            recommended_order_quantity=p.get("reco_order_qty",0))
             for p in data.get("predictions", [])]
    return AIPredictResponse(predictions=items, confidence=float(data.get("confidence",0.5)))

# Экспортируем обёрнутое ASGI-приложение (FastAPI + Socket.IO)
app = with_socketio(_app)

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from datetime import datetime, timedelta
import io, csv, asyncio, httpx
from typing import List

from .core.settings import get_settings
from .core.db import Base, engine, get_db
from .core.security import hash_password, verify_password, create_jwt
from .core.realtime import with_socketio, emit_dashboard
from .models import User, Robot, Product, InventoryHistory
from .schemas import (
    LoginRequest, LoginResponse, UserOut, APIError, RobotIngestRequest, RobotIngestResponse,
    BootstrapResponse, RobotDTO, StatPoint, DashboardStats, RecentScan,
    HistoryResponse, HistoryItem, AIPredictRequest, AIPredictResponse, AIPredictItem
)

settings = get_settings()

# создаём обычное FastAPI-приложение
_app = FastAPI(title="Smart Warehouse Backend", version="1.0.0")

# CORS: разрешаем всё (по требованиям кейса)
_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@_app.on_event("startup")
def on_start():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        u = conn.execute(select(User).where(User.email== "operator@example.com")).scalar_one_or_none()
        if not u:
            conn.execute(User.__table__.insert().values(
                email="operator@example.com",
                password_hash=hash_password("password1234"),
                name="Оператор", role="operator", created_at=datetime.utcnow()
            ))
        existing = conn.execute(select(func.count(Product.id))).scalar() or 0
        if existing < 3:
            conn.execute(Product.__table__.insert(), [
                {"id":"TEL-4567","name":"Роутер RT-AC68U","category":"Сетевое","min_stock":10,"optimal_stock":100},
                {"id":"TEL-6789","name":"IP-телефон T46S","category":"Телефония","min_stock":15,"optimal_stock":120},
                {"id":"TEL-3456","name":"Кабель UTP Cat6","category":"Кабели","min_stock":50,"optimal_stock":500},
            ])

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
@_app.post(f"{settings.API_PREFIX}/robots/data", response_model=RobotIngestResponse)
def robots_data(body: RobotIngestRequest, db: Session = Depends(get_db)):
    rid = body.robot_id
    robot = db.get(Robot, rid) or Robot(id=rid)
    db.add(robot)
    robot.battery_level = body.battery_level
    robot.last_update = body.timestamp
    robot.current_zone = body.location.zone
    robot.current_row = body.location.row
    robot.current_shelf = body.location.shelf
    robot.status = "active" if body.battery_level >= 20 else "low"
    for s in body.scan_results:
        prod = db.get(Product, s.product_id) or Product(id=s.product_id, name=s.product_name or s.product_id, category="Прочее")
        db.add(prod)
        ih = InventoryHistory(robot_id=rid, product_id=s.product_id, product_name=s.product_name or prod.name,
                              quantity=s.quantity, zone=body.location.zone, row_number=body.location.row,
                              shelf_number=body.location.shelf, status=s.status, scanned_at=body.timestamp)
        db.add(ih)
    db.commit()
    try:
        import anyio
        anyio.from_thread.run(asyncio.create_task, emit_dashboard("robot_update", {
            "robot_id": rid, "zone": body.location.zone, "row": body.location.row,
            "battery": body.battery_level, "status": robot.status, "time": body.timestamp.isoformat()
        }))
    except Exception:
        pass
    mid = body.message_id or f"msg-{rid}-{int(datetime.utcnow().timestamp())}"
    return RobotIngestResponse(message_id=mid)

# ---------- BOOTSTRAP ----------
@_app.get(f"{settings.API_PREFIX}/bootstrap", response_model=BootstrapResponse)
def bootstrap(db: Session = Depends(get_db)):
    robots: List[RobotDTO] = []
    now = datetime.utcnow()
    for r in db.execute(select(Robot)).scalars().all():
        st = "offline" if (now-(r.last_update or now)).total_seconds()>60 else ("low" if (r.battery_level or 0)<20 else "active")
        robots.append(RobotDTO(id=r.id, battery=round(r.battery_level or 0,1),
                               updated=r.last_update or now, zone=r.current_zone or "A",
                               row=r.current_row or 1, status=st))
    recent_rows = db.execute(select(InventoryHistory).order_by(InventoryHistory.scanned_at.desc()).limit(20)).scalars().all()
    recent = [RecentScan(time=r.scanned_at, robot_id=r.robot_id, zone=r.zone, product_id=r.product_id,
                         product_name=r.product_name, qty=r.quantity,
                         status=("CRIT" if r.status=="CRITICAL" else ("LOW" if r.status=="LOW_STOCK" else "OK")))
              for r in recent_rows]
    total = len(robots)
    active = sum(1 for r in robots if r.status=="active")
    avg_batt = int(round(sum(r.battery for r in robots)/total,0)) if total>0 else 0
    today = now.date()
    from sqlalchemy import cast, Date
    checked = db.execute(select(func.count()).select_from(InventoryHistory).where(cast(InventoryHistory.scanned_at, Date)==today)).scalar() or 0
    critical = db.execute(select(func.count()).select_from(InventoryHistory).where(InventoryHistory.status=="CRITICAL", cast(InventoryHistory.scanned_at, Date)==today)).scalar() or 0
    points = []
    for i in range(12):
        start = now - timedelta(minutes=5*(11-i))
        end = start + timedelta(minutes=5)
        cnt = db.execute(select(func.count()).select_from(InventoryHistory).where(InventoryHistory.scanned_at>=start, InventoryHistory.scanned_at<end)).scalar() or 0
        points.append(StatPoint(t=(start + timedelta(minutes=2, seconds=30)).strftime("%H:%M"), v=cnt))
    stats = DashboardStats(active=active,total=total,checked=checked,critical=critical,avgBatt=avg_batt,activity=points)
    return BootstrapResponse(robots=robots, recent=recent, stats=stats, zonesHeat={})

# ---------- HISTORY ----------
@_app.get(f"{settings.API_PREFIX}/inventory/history", response_model=HistoryResponse)
def history(from_: str = None, to: str = None, zone: str = None, status: str = None,
            q: str = None, page: int = 1, size: int = 20, sort: str = "scanned_at", dir: str = "desc",
            db: Session = Depends(get_db)):
    from sqlalchemy import or_
    qry = select(InventoryHistory)
    if zone: qry = qry.where(InventoryHistory.zone==zone)
    if status and status.upper() in ("OK","LOW_STOCK","CRITICAL"):
        qry = qry.where(InventoryHistory.status==status.upper())
    if q:
        like=f"%{q}%"
        qry=qry.where(or_(InventoryHistory.product_id.ilike(like), InventoryHistory.product_name.ilike(like)))
    col = getattr(InventoryHistory, sort, InventoryHistory.scanned_at)
    qry = qry.order_by(col.desc() if dir=="desc" else col)
    total = db.execute(select(func.count()).select_from(qry.subquery())).scalar() or 0
    offset = max(0,(page-1))*size
    rows = db.execute(qry.limit(size).offset(offset)).scalars().all()
    items: List[HistoryItem] = []
    for r in rows:
        expected = 100
        diff = r.quantity-expected
        items.append(HistoryItem(id=r.id, scanned_at=r.scanned_at.strftime("%Y-%m-%d %H:%M:%S"),
                                 robot_id=r.robot_id, zone=r.zone, product_id=r.product_id, product_name=r.product_name,
                                 expected=expected, quantity=r.quantity, diff=diff, status=r.status))
    return HistoryResponse(total=total, items=items, pagination={"page":page,"size":size})

# ---------- IMPORT CSV ----------
@_app.post(f"{settings.API_PREFIX}/inventory/import")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv allowed")
    content = (await file.read()).decode("utf-8", errors="ignore")
    reader = csv.DictReader(content.splitlines(), delimiter=';')
    required = {"product_id","product_name","quantity","zone","date"}
    if not required.issubset(set([c.strip() for c in (reader.fieldnames or [])])):
        raise HTTPException(400, "Missing required columns")
    total = 0; success = 0; errors = []
    for row in reader:
        total += 1
        try:
            pid = (row.get("product_id") or "").strip()
            pname = (row.get("product_name") or pid).strip()
            qty = int((row.get("quantity") or "0").strip())
            zone = (row.get("zone") or "A").strip()
            day = datetime.fromisoformat((row.get("date") or datetime.utcnow().isoformat()).strip())
            rnum = int((row.get("row") or row.get("row_number") or "1").strip())
            shelf = int((row.get("shelf") or row.get("shelf_number") or "1").strip())
            status = "OK" if qty>20 else ("LOW_STOCK" if qty>10 else "CRITICAL")
            prod = db.get(Product, pid) or Product(id=pid, name=pname, category="Импорт")
            db.add(prod)
            ih = InventoryHistory(robot_id="RB-IM", product_id=pid, product_name=pname,
                                  quantity=qty, zone=zone, row_number=rnum, shelf_number=shelf,
                                  status=status, scanned_at=day)
            db.add(ih); success += 1
        except Exception as e:
            errors.append({"row": total, "error": str(e)})
    db.commit()
    try:
        import anyio
        anyio.from_thread.run(asyncio.create_task, emit_dashboard("import_done", {"success":success,"failed":total-success}))
    except Exception:
        pass
    return {"success": success, "failed": total-success, "errors": errors}

# ---------- EXPORTS ----------
@_app.get(f"{settings.API_PREFIX}/export/excel")
def export_excel(ids: str, db: Session = Depends(get_db)):
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
def export_pdf(ids: str, db: Session = Depends(get_db)):
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
async def ai_predict(body: AIPredictRequest):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{settings.AI_BASE_URL}/predict", json={"period_days": body.period_days})
        r.raise_for_status()
        data = r.json()
    items = [AIPredictItem(product_id=p.get("product_id","UNK"), product_name="Товар",
                            current_stock=p.get("current_stock",0), stockout_date=p.get("stockout_date","1970-01-01"),
                            recommended_order_quantity=p.get("reco_order_qty",0))
             for p in data.get("predictions", [])]
    return AIPredictResponse(predictions=items, confidence=float(data.get("confidence",0.5)))

# Экспортируем обёрнутое ASGI-приложение (FastAPI + Socket.IO)
app = with_socketio(_app)

from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Optional, List, Literal, Dict
from datetime import datetime, date

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    @field_validator('password')
    @classmethod
    def check_pwd(cls, v):
        if len(v) < 8: raise ValueError('Password too short')
        return v

class UserOut(BaseModel):
    id: int
    name: str
    role: str
    email: EmailStr

class LoginResponse(BaseModel):
    token: str
    user: UserOut

class RobotLocation(BaseModel):
    zone: str
    row: int
    shelf: int

class ScanItem(BaseModel):
    product_id: str
    product_name: Optional[str] = ""
    quantity: int
    status: Literal["OK","LOW_STOCK","CRITICAL"]

class RobotIngestRequest(BaseModel):
    robot_id: str
    timestamp: datetime
    location: RobotLocation
    scan_results: List[ScanItem]
    battery_level: float
    next_checkpoint: Optional[str] = None
    message_id: Optional[str] = None

class RobotIngestResponse(BaseModel):
    status: str = "received"
    message_id: str

class RobotDTO(BaseModel):
    id: str
    battery: float
    updated: datetime
    zone: str
    row: int
    status: Literal["active","low","offline"]

class StatPoint(BaseModel):
    t: str
    v: int

class DashboardStats(BaseModel):
    active: int
    total: int
    checked: int
    critical: int
    avgBatt: int
    activity: List[StatPoint]

class RecentScan(BaseModel):
    time: datetime
    robot_id: str
    zone: str
    product_id: str
    product_name: str
    qty: int
    status: Literal["OK","LOW","CRIT"]

class BootstrapResponse(BaseModel):
    robots: List[RobotDTO]
    recent: List[RecentScan]
    stats: DashboardStats
    zonesHeat: Dict[str, float]

class HistoryItem(BaseModel):
    id: int
    scanned_at: str
    robot_id: str
    zone: str
    product_id: str
    product_name: str
    expected: int
    quantity: int
    diff: int
    status: Literal["OK","LOW_STOCK","CRITICAL"]

class HistoryResponse(BaseModel):
    total: int
    items: List[HistoryItem]
    pagination: dict

class AIPredictRequest(BaseModel):
    period_days: int = Field(ge=1, le=30, default=7)
    categories: Optional[List[str]] = None

class AIPredictItem(BaseModel):
    product_id: str
    product_name: str
    current_stock: int
    stockout_date: date
    recommended_order_quantity: int

class AIPredictResponse(BaseModel):
    predictions: List[AIPredictItem]
    confidence: float

class APIError(BaseModel):
    detail: str

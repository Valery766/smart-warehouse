from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey
from datetime import datetime
from .core.db import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Robot(Base):
    __tablename__ = "robots"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    battery_level: Mapped[float] = mapped_column(Float, default=100.0)
    last_update: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    current_zone: Mapped[str] = mapped_column(String(10), default="A")
    current_row: Mapped[int] = mapped_column(Integer, default=1)
    current_shelf: Mapped[int] = mapped_column(Integer, default=1)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    min_stock: Mapped[int] = mapped_column(Integer, default=10)
    optimal_stock: Mapped[int] = mapped_column(Integer, default=100)

class InventoryHistory(Base):
    __tablename__ = "inventory_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    robot_id: Mapped[str] = mapped_column(String(50), ForeignKey("robots.id"))
    product_id: Mapped[str] = mapped_column(String(50), ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    zone: Mapped[str] = mapped_column(String(10), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=True)
    shelf_number: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # OK | LOW_STOCK | CRITICAL
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

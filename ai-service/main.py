from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import List
from datetime import date, timedelta

app = FastAPI(title="AI Service", version="0.1.0")

class PredictReq(BaseModel):
    period_days: int = Field(7, ge=1, le=30)

class Pred(BaseModel):
    product_id: str
    current_stock: int
    days_until_stockout: int
    stockout_date: date
    reco_order_qty: int

class PredictResp(BaseModel):
    predictions: List[Pred]
    confidence: float

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/predict", response_model=PredictResp)
def predict(r: PredictReq):
    return PredictResp(
        predictions=[
            Pred(
                product_id="TEL-0001",
                current_stock=42,
                days_until_stockout=6,
                stockout_date=date.today() + timedelta(days=6),
                reco_order_qty=120,
            )
        ],
        confidence=0.5,
    )

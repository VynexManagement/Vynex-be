from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ── Enums (important for safety) ───────────────────────────────────────

class LeadStatusEnum(str, Enum):
    valid = "valid"
    broken = "broken"


class BulkActionEnum(str, Enum):
    valid = "valid"
    broken = "broken"
    delete = "delete"


# ── Query / Public API ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    niche: Optional[str] = Field(None, description="Legacy single niche")
    country: Optional[str] = Field(None, description="Legacy single country")
    signal: Optional[str] = Field(None, description="Signal name")
    signal_id: Optional[str] = Field(None, description="Signal UUID")
    niches: List[str] = Field(default_factory=list)
    countries: List[str] = Field(default_factory=list)
    signal_ids: List[str] = Field(default_factory=list)
    signal_names: List[str] = Field(default_factory=list)
    persist: bool = False


class LeadPreview(BaseModel):
    store_name: str
    url: str
    country: str
    signal: str
    niche: Optional[str] = None  # ✅ added


class PreviewResponse(BaseModel):
    dataset_id: str
    items: List[LeadPreview]
    total_count: int

    # ✅ added (already used in backend)
    price_inr: Optional[int] = None
    price_usd: Optional[int] = None
    niche: Optional[str] = None
    country: Optional[str] = None
    signal: Optional[str] = None
    niches: List[str] = Field(default_factory=list)
    countries: List[str] = Field(default_factory=list)
    signal_ids: List[str] = Field(default_factory=list)
    signal_names: List[str] = Field(default_factory=list)


# ── Payments ───────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    dataset_id: str
    amount: int
    currency: str = "INR"


class OrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str


class VerificationRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    dataset_id: str


class VerificationResponse(BaseModel):
    success: bool
    message: str
    dataset_id: Optional[str] = None  # ✅ added


# ── User Purchases ─────────────────────────────────────────────────────

class UserPurchase(BaseModel):
    dataset_id: str
    niche: str
    country: str
    signal: str
    total_leads: int
    purchase_date: datetime


# ── Admin Models ───────────────────────────────────────────────────────

class LeadStatusUpdate(BaseModel):
    status: LeadStatusEnum  # ✅ enum


class BulkLeadAction(BaseModel):
    action: BulkActionEnum  # ✅ enum
    lead_ids: List[str]


class SignalCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rule_definition: str
    active: bool = True


class SignalUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rule_definition: Optional[str] = None
    active: Optional[bool] = None
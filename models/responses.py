from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class LeadPreview(BaseModel):
    store_name: str
    url: str
    country: str
    signal: str
    niche: Optional[str] = None


class PreviewResponse(BaseModel):
    dataset_id: str
    items: List[LeadPreview]
    total_count: int
    price_inr: Optional[int] = None
    price_usd: Optional[int] = None
    niche: Optional[str] = None
    country: Optional[str] = None
    signal: Optional[str] = None
    niches: List[str] = Field(default_factory=list)
    countries: List[str] = Field(default_factory=list)
    signal_ids: List[str] = Field(default_factory=list)
    signal_names: List[str] = Field(default_factory=list)


class RazorpayOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str
    key_id: str


class StripeIntentResponse(BaseModel):
    client_secret: str
    amount: int
    currency: str
    publishable_key: str


class VerificationResponse(BaseModel):
    success: bool
    message: str
    dataset_id: Optional[str] = None


class PurchaseItem(BaseModel):
    id: str
    dataset_id: str
    niche: str
    country: str
    signal: str
    total_leads: int
    price_inr: Optional[int] = None
    price_usd: Optional[int] = None
    payment_method: Optional[str] = None
    purchase_date: datetime
from pydantic import BaseModel, Field
from typing import List, Optional


class QueryRequest(BaseModel):
    """Filter leads by any combination of niches, countries, and signals (AND logic)."""

    # Legacy single-value fields (merged into lists server-side)
    niche: Optional[str] = Field(None, description="Deprecated: use niches")
    country: Optional[str] = Field(None, description="Deprecated: use countries")
    signal: Optional[str] = Field(None, description="Signal name")
    signal_id: Optional[str] = Field(None, description="Single signal UUID")

    niches: List[str] = Field(default_factory=list)
    countries: List[str] = Field(default_factory=list)
    signal_ids: List[str] = Field(default_factory=list)
    # When the UI only has signal names (no catalog UUIDs), resolve via signals.name
    signal_names: List[str] = Field(default_factory=list)

    persist: bool = Field(
        False,
        description="When true, upsert a dataset row and dataset_leads for checkout.",
    )


class RazorpayOrderRequest(BaseModel):
    dataset_id: str
    amount: int = Field(..., description="Amount in paise")
    currency: str = "INR"


class StripeIntentRequest(BaseModel):
    dataset_id: str
    amount: int = Field(..., description="Amount in cents")
    currency: str = "usd"


class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    dataset_id: str


class StripeConfirmRequest(BaseModel):
    payment_intent_id: str
    dataset_id: str
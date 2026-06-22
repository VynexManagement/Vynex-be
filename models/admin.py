from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class LeadStatusEnum(str, Enum):
    valid = "valid"
    broken = "broken"


class BulkActionEnum(str, Enum):
    valid = "valid"
    broken = "broken"
    delete = "delete"


class LeadStatusUpdate(BaseModel):
    status: LeadStatusEnum


class BulkLeadAction(BaseModel):
    action: BulkActionEnum
    lead_ids: List[str]


class SignalCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    weight: Optional[float] = None
    dependencies: Optional[List[str]] = None
    description: Optional[str] = None
    rule_definition: Optional[str] = None
    active: Optional[bool] = None
    is_active: Optional[bool] = None


class SignalUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    weight: Optional[float] = None
    dependencies: Optional[List[str]] = None
    description: Optional[str] = None
    rule_definition: Optional[str] = None
    active: Optional[bool] = None
    is_active: Optional[bool] = None
from fastapi import APIRouter, Depends
from supabase import Client

from config import Settings, get_settings
from dependencies import get_current_user, get_supabase
from models.requests import (
    RazorpayOrderRequest,
    RazorpayVerifyRequest,
    StripeConfirmRequest,
    StripeIntentRequest,
)
from models.responses import (
    RazorpayOrderResponse,
    StripeIntentResponse,
    VerificationResponse,
)
from services import payment_service

router = APIRouter(prefix="/api", tags=["Payments"])


@router.post("/create-razorpay-order", response_model=RazorpayOrderResponse)
async def create_razorpay_order(
    req: RazorpayOrderRequest,
    user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Create a Razorpay order. Returns order_id and key_id for the frontend checkout."""
    return await payment_service.create_razorpay_order(req, settings)


@router.post("/verify-razorpay-payment", response_model=VerificationResponse)
async def verify_razorpay_payment(
    req: RazorpayVerifyRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """Verify Razorpay signature and record purchase in the database."""
    return await payment_service.verify_razorpay_payment(req, user, supabase, settings)


@router.post("/create-stripe-intent", response_model=StripeIntentResponse)
async def create_stripe_intent(
    req: StripeIntentRequest,
    user: dict = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Create a Stripe PaymentIntent. Returns client_secret for Stripe.js."""
    return await payment_service.create_stripe_intent(req, user, settings)


@router.post("/confirm-stripe-payment", response_model=VerificationResponse)
async def confirm_stripe_payment(
    req: StripeConfirmRequest,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
):
    """
    After Stripe.js confirms payment on the frontend, call this endpoint with
    the payment_intent_id to verify status and record the purchase.
    """
    return await payment_service.confirm_stripe_payment(req, user, supabase, settings)

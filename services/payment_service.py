import logging

import razorpay
import stripe
from fastapi import HTTPException
from supabase import Client

from config import Settings
from models.requests import RazorpayOrderRequest, RazorpayVerifyRequest, StripeConfirmRequest, StripeIntentRequest
from models.responses import RazorpayOrderResponse, StripeIntentResponse, VerificationResponse

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# RAZORPAY
# ──────────────────────────────────────────────

async def create_razorpay_order(
    req: RazorpayOrderRequest, settings: Settings
) -> RazorpayOrderResponse:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured.")

    client = razorpay.Client(
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
    )
    try:
        order = client.order.create(
            {
                "amount": req.amount,
                "currency": req.currency,
                "receipt": req.dataset_id[:40],
                "payment_capture": 1,
            }
        )
        return RazorpayOrderResponse(
            order_id=order["id"],
            amount=order["amount"],
            currency=order["currency"],
            key_id=settings.razorpay_key_id,
        )
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create Razorpay order.")


async def verify_razorpay_payment(
    req: RazorpayVerifyRequest,
    user: dict,
    supabase: Client,
    settings: Settings,
) -> VerificationResponse:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured.")

    client = razorpay.Client(
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
    )

    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": req.razorpay_order_id,
                "razorpay_payment_id": req.razorpay_payment_id,
                "razorpay_signature": req.razorpay_signature,
            }
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payment signature.")

    # Record the purchase
    try:
        supabase.table("purchases").insert(
            {
                "user_id": user["id"],
                "dataset_id": req.dataset_id,
                "payment_id": req.razorpay_payment_id,
                "payment_method": "razorpay",
                "status": "completed",
            }
        ).execute()
    except Exception as e:
        logger.error(f"Failed to record Razorpay purchase in DB: {e}")
        # Don't fail the response — payment was valid on Razorpay's end

    return VerificationResponse(
        success=True,
        message="Payment verified successfully.",
        dataset_id=req.dataset_id,
    )


# ──────────────────────────────────────────────
# STRIPE
# ──────────────────────────────────────────────

async def create_stripe_intent(
    req: StripeIntentRequest, user: dict, settings: Settings
) -> StripeIntentResponse:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")

    stripe.api_key = settings.stripe_secret_key

    try:
        intent = stripe.PaymentIntent.create(
            amount=req.amount,
            currency=req.currency,
            automatic_payment_methods={"enabled": True},
            metadata={
                "dataset_id": req.dataset_id,
                "user_id": user["id"],
                "user_email": user.get("email", ""),
            },
        )
        return StripeIntentResponse(
            client_secret=intent.client_secret,
            amount=req.amount,
            currency=req.currency,
            publishable_key=settings.stripe_publishable_key,
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe PaymentIntent creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create Stripe payment intent.")


async def confirm_stripe_payment(
    req: StripeConfirmRequest,
    user: dict,
    supabase: Client,
    settings: Settings,
) -> VerificationResponse:
    """
    After Stripe.js completes the payment on the frontend, the client sends the
    payment_intent_id here. We retrieve it from Stripe to verify status = 'succeeded'.
    This avoids needing a webhook for Phase 1.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Stripe is not configured.")

    stripe.api_key = settings.stripe_secret_key

    try:
        intent = stripe.PaymentIntent.retrieve(req.payment_intent_id)
    except stripe.error.StripeError as e:
        logger.error(f"Stripe PaymentIntent retrieval failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify payment with Stripe.")

    if intent.status != "succeeded":
        raise HTTPException(
            status_code=400,
            detail=f"Payment not completed. Status: {intent.status}",
        )

    # Verify the metadata user matches (security check)
    if intent.metadata.get("user_id") != user["id"]:
        raise HTTPException(
            status_code=403, detail="Payment does not belong to this user."
        )

    # Check dataset_id matches
    if intent.metadata.get("dataset_id") != req.dataset_id:
        raise HTTPException(
            status_code=400, detail="Dataset ID mismatch."
        )

    # Record the purchase
    try:
        # Check for duplicate (idempotency)
        existing = (
            supabase.table("purchases")
            .select("id")
            .eq("payment_id", req.payment_intent_id)
            .execute()
        )
        if not existing.data:
            supabase.table("purchases").insert(
                {
                    "user_id": user["id"],
                    "dataset_id": req.dataset_id,
                    "payment_id": req.payment_intent_id,
                    "payment_method": "stripe",
                    "status": "completed",
                }
            ).execute()
    except Exception as e:
        logger.error(f"Failed to record Stripe purchase in DB: {e}")

    return VerificationResponse(
        success=True,
        message="Stripe payment verified successfully.",
        dataset_id=req.dataset_id,
    )

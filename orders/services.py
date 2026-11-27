import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from products.models import ProductVariant

from .models import Order, OrderItem

logger = logging.getLogger(__name__)


def lock_variants(variant_ids: Iterable[str]) -> dict[str, ProductVariant]:
    # Call inside transaction.atomic(); the rows stay locked until it ends,
    # so two orders for the same variant can't both read the old stock.
    variants = (
        ProductVariant.objects.select_for_update(of=("self",))
        .filter(id__in=list(variant_ids))
        .select_related("product")
    )
    return {str(v.id): v for v in variants}


def reduce_stock(variant: ProductVariant, quantity: int, order: Order) -> None:
    # The customer has already paid, so keep the order and flag the shortfall.
    if variant.stock_qty < quantity:
        logger.warning(
            "Order %s paid for %s of variant %s but only %s in stock",
            order.id, quantity, variant.id, variant.stock_qty,
        )
    variant.stock_qty = max(0, variant.stock_qty - quantity)
    variant.save(update_fields=["stock_qty"])


BLANK_SHIPPING = {
    "first_name": "",
    "last_name": "",
    "address_line1": "",
    "address_line2": "",
    "city": "",
    "state": "",
    "postal_code": "",
}


def parse_metadata_items(items_str: str) -> list[tuple[str, int]]:
    """Parse Stripe metadata "variant_id:qty,variant_id:qty" into pairs, skipping bad entries."""
    pairs = []
    for part in (items_str or "").split(","):
        part = part.strip()
        if ":" not in part:
            continue
        variant_id, qty = part.split(":", 1)
        try:
            pairs.append((variant_id.strip(), int(qty.strip())))
        except ValueError:
            continue
    return pairs


def create_paid_order(
    items: Iterable[tuple[str, int]],
    *,
    email: str,
    total: Decimal,
    user: User | None = None,
    stripe_session_id: str = "",
    stripe_payment_intent_id: str = "",
    shipping: dict[str, str] | None = None,
) -> Order:
    """Create a paid order and reduce stock, or return the existing order for this payment."""
    existing = _find_order_for_payment(stripe_session_id, stripe_payment_intent_id)
    if existing:
        return existing

    items = [(str(variant_id), qty) for variant_id, qty in items if qty > 0]
    with transaction.atomic():
        variant_map = lock_variants(variant_id for variant_id, _ in items)
        order = Order.objects.create(
            stripe_session_id=stripe_session_id or "",
            stripe_payment_intent_id=stripe_payment_intent_id or "",
            user=user,
            email=email or "guest@example.com",
            total=total,
            status="paid",
            paid_at=timezone.now(),
            **{**BLANK_SHIPPING, **(shipping or {})},
        )
        for variant_id, qty in items:
            variant = variant_map.get(variant_id)
            if variant is None:
                continue
            OrderItem.objects.create(
                order=order,
                product_name=variant.product.name,
                product_id=variant.product.id,
                variant_id=variant.id,
                size=variant.size,
                color=variant.color,
                quantity=qty,
                price=variant.price,
            )
            reduce_stock(variant, qty, order)
    return order


def _find_order_for_payment(session_id: str, payment_intent_id: str) -> Order | None:
    if session_id:
        order = Order.objects.filter(stripe_session_id=session_id).first()
        if order:
            return order
    if payment_intent_id:
        return Order.objects.filter(stripe_payment_intent_id=payment_intent_id).first()
    return None


def _read(obj: Any, key: str) -> Any:
    # Stripe objects and plain dicts (in tests) both need to work here.
    if obj is None:
        return None
    if hasattr(obj, "get"):
        return obj.get(key)
    return getattr(obj, key, None)


def _trim(value: Any, field_name: str) -> str:
    max_length = Order._meta.get_field(field_name).max_length
    return str(value or "").strip()[:max_length]


def shipping_fields_from_session(session: Any) -> dict[str, str]:
    """Order name/address fields from a Checkout Session, or {} if none were collected."""
    # API 2025-03-31 moved shipping_details under collected_information;
    # older API versions still send it at the top level.
    details = _read(_read(session, "collected_information"), "shipping_details")
    if not details:
        details = _read(session, "shipping_details")
    address = _read(details, "address")
    if not address:
        return {}

    first_name, _, last_name = str(_read(details, "name") or "").strip().partition(" ")
    return {
        "first_name": _trim(first_name, "first_name"),
        "last_name": _trim(last_name, "last_name"),
        "address_line1": _trim(_read(address, "line1"), "address_line1"),
        "address_line2": _trim(_read(address, "line2"), "address_line2"),
        "city": _trim(_read(address, "city"), "city"),
        "state": _trim(_read(address, "state"), "state"),
        "postal_code": _trim(_read(address, "postal_code"), "postal_code"),
        "country": _trim(_read(address, "country"), "country"),
    }

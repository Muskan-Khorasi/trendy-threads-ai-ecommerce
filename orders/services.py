import logging
from collections.abc import Iterable
from typing import Any

from products.models import ProductVariant

from .models import Order

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

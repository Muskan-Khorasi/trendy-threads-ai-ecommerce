import logging
from collections.abc import Iterable

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

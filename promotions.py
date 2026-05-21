from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

from django.db.models import F
from django.utils import timezone

from .models import PromoCode, PromoCodeRedemption, Ride


MONEY_QUANT = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def normalize_promo_code(code: str) -> str:
    return (code or "").strip().upper()


def calculate_discount_amount(promo: PromoCode, fare_amount: Decimal) -> Decimal:
    fare_amount = _money(fare_amount)
    if fare_amount <= 0:
        return Decimal("0.00")

    if promo.discount_type == PromoCode.DISCOUNT_PERCENT:
        discount = (fare_amount * promo.discount_value / Decimal("100"))
    else:
        discount = _money(promo.discount_value)

    if promo.max_discount_amount is not None:
        discount = min(discount, _money(promo.max_discount_amount))

    discount = max(Decimal("0.00"), min(discount, fare_amount))
    return discount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def validate_promo_for_user(
    promo: PromoCode,
    user,
    *,
    vehicle_type: str,
    fare_amount: Decimal,
    now=None,
) -> Tuple[bool, Optional[str]]:
    now = now or timezone.now()
    fare_amount = _money(fare_amount)

    if not promo.is_active:
        return False, "Promo code is inactive."

    if now < promo.valid_from or now > promo.valid_until:
        return False, "Promo code is expired or not started yet."

    if promo.usage_limit_total is not None and promo.used_count >= promo.usage_limit_total:
        return False, "Promo code usage limit reached."

    if promo.vehicle_type != PromoCode.VEHICLE_ALL and promo.vehicle_type != vehicle_type:
        return False, f"Promo code is not valid for {vehicle_type} rides."

    if fare_amount < _money(promo.min_ride_fare):
        return False, f"Minimum fare for this promo is Rs {_money(promo.min_ride_fare)}."

    if promo.max_ride_fare is not None and fare_amount > _money(promo.max_ride_fare):
        return False, f"Promo code is only valid up to Rs {_money(promo.max_ride_fare)} fare."

    completed_states = ['payment_required', 'payment_confirmed', 'completed']
    if promo.first_ride_only and Ride.objects.filter(passenger=user, status__in=completed_states).exists():
        return False, "This promo is only valid on your first ride."

    user_used = PromoCodeRedemption.objects.filter(
        promo_code=promo,
        user=user,
        status__in=[PromoCodeRedemption.STATUS_RESERVED, PromoCodeRedemption.STATUS_CONSUMED],
    ).count()

    if promo.apply_on_next_ride and user_used > 0:
        return False, "This promo is valid only on your next ride and is already used."

    if promo.usage_limit_per_user and user_used >= promo.usage_limit_per_user:
        return False, "You have reached your usage limit for this promo."

    return True, None


def get_promo_preview(code: str, user, *, vehicle_type: str, fare_amount: Decimal):
    normalized = normalize_promo_code(code)
    if not normalized:
        return {
            'valid': False,
            'message': 'Promo code is required.',
        }

    promo = PromoCode.objects.filter(code=normalized).first()
    if not promo:
        return {
            'valid': False,
            'message': 'Promo code not found.',
        }

    if not user or not user.is_authenticated:
        return {
            'valid': False,
            'message': 'Login required to apply promo code.',
            'code': promo.code,
        }

    is_valid, error = validate_promo_for_user(
        promo,
        user,
        vehicle_type=vehicle_type,
        fare_amount=fare_amount,
    )
    if not is_valid:
        return {
            'valid': False,
            'message': error,
            'code': promo.code,
        }

    discount = calculate_discount_amount(promo, fare_amount)
    final_fare = max(Decimal("0.00"), _money(fare_amount) - discount)
    return {
        'valid': True,
        'code': promo.code,
        'discount': float(discount),
        'fare_before_discount': float(_money(fare_amount)),
        'fare_after_discount': float(final_fare),
        'message': promo.title or promo.description or "Promo applied successfully.",
    }


def lock_validate_and_compute(code: str, user, *, vehicle_type: str, fare_amount: Decimal):
    normalized = normalize_promo_code(code)
    if not normalized:
        return None, None, "Promo code is required."

    promo = PromoCode.objects.select_for_update().filter(code=normalized).first()
    if not promo:
        return None, None, "Promo code not found."

    is_valid, error = validate_promo_for_user(
        promo,
        user,
        vehicle_type=vehicle_type,
        fare_amount=fare_amount,
    )
    if not is_valid:
        return None, None, error

    discount = calculate_discount_amount(promo, fare_amount)
    return promo, discount, None


def consume_promo_for_ride(
    promo: PromoCode,
    user,
    ride: Ride,
    *,
    fare_before_discount: Decimal,
    discount_amount: Decimal,
):
    fare_before_discount = _money(fare_before_discount)
    discount_amount = _money(discount_amount)
    fare_after_discount = max(Decimal("0.00"), fare_before_discount - discount_amount)

    PromoCode.objects.filter(id=promo.id).update(used_count=F('used_count') + 1)
    PromoCodeRedemption.objects.create(
        promo_code=promo,
        user=user,
        ride=ride,
        status=PromoCodeRedemption.STATUS_CONSUMED,
        fare_before_discount=fare_before_discount,
        discount_amount=discount_amount,
        fare_after_discount=fare_after_discount,
    )


def rollback_promo_for_cancelled_ride(ride: Ride):
    if not ride.promo_code_id:
        return
    redemption = PromoCodeRedemption.objects.filter(
        ride=ride,
        status=PromoCodeRedemption.STATUS_CONSUMED,
    ).first()
    if not redemption:
        return
    redemption.status = PromoCodeRedemption.STATUS_CANCELLED
    redemption.cancelled_at = timezone.now()
    redemption.save(update_fields=['status', 'cancelled_at'])
    PromoCode.objects.filter(id=ride.promo_code_id, used_count__gt=0).update(used_count=F('used_count') - 1)

from decimal import Decimal

from django.core.cache import cache
from django.conf import settings

from .models import FareRule


FARE_RULE_CACHE_KEY = "fare_rules:v1"
FARE_RULE_CACHE_TTL_SECONDS = 300


def _fallback_fare_config_map():
    fallback = getattr(settings, "FARE_CONFIG", {}) or {}
    if not isinstance(fallback, dict):
        return {}
    normalized = {}
    for vehicle_type, cfg in fallback.items():
        if not isinstance(cfg, dict):
            continue
        normalized[vehicle_type] = {
            "base_fare": float(cfg.get("base_fare", 0)),
            "per_km": float(cfg.get("per_km", 0)),
            "per_minute": float(cfg.get("per_minute", 0)),
            "surge_multiplier": float(cfg.get("surge_multiplier", 1)),
            "minimum_fare": float(cfg.get("minimum_fare", 0)),
            "cancellation_fee": float(cfg.get("cancellation_fee", 0)),
        }
    return normalized


def invalidate_fare_rules_cache():
    cache.delete(FARE_RULE_CACHE_KEY)


def _serialize_fare_rule(obj):
    return {
        "base_fare": float(obj.base_fare),
        "per_km": float(obj.per_km),
        "per_minute": float(obj.per_minute),
        "surge_multiplier": float(obj.surge_multiplier),
        "minimum_fare": float(obj.minimum_fare),
        "cancellation_fee": float(obj.cancellation_fee),
        "tax_percentage": float(obj.tax_percentage),
    }


def get_fare_config_map():
    cached = cache.get(FARE_RULE_CACHE_KEY)
    if isinstance(cached, dict) and cached:
        return cached

    rules = FareRule.objects.filter(is_active=True)
    fare_map = {rule.vehicle_type: _serialize_fare_rule(rule) for rule in rules}
    if not fare_map:
        fare_map = _fallback_fare_config_map()

    if fare_map:
        cache.set(FARE_RULE_CACHE_KEY, fare_map, timeout=FARE_RULE_CACHE_TTL_SECONDS)
    return fare_map


def get_vehicle_fare_config(vehicle_type: str):
    fare_map = get_fare_config_map()
    return fare_map.get(vehicle_type) or fare_map.get("mini")


def calculate_fare(distance_km, vehicle_type, duration_minutes=0):
    cfg = get_vehicle_fare_config(vehicle_type)
    if not cfg:
        return None
    
    surge_multiplier = Decimal(str(cfg.get("surge_multiplier", 1)))
    tax_percentage = Decimal(str(cfg.get("tax_percentage", 5)))  # Default 5% tax
    
    # Calculate base fare
    base_fare = Decimal(str(cfg.get("base_fare", 0)))
    distance_fare = Decimal(str(distance_km or 0)) * Decimal(str(cfg.get("per_km", 0)))
    time_fare = Decimal(str(duration_minutes or 0)) * Decimal(str(cfg.get("per_minute", 0)))
    
    subtotal = base_fare + distance_fare + time_fare
    
    # Apply surge multiplier
    subtotal_with_surge = subtotal * surge_multiplier
    
    # Calculate tax
    tax_amount = (subtotal_with_surge * tax_percentage) / Decimal('100')
    
    # Final total
    total = subtotal_with_surge + tax_amount
    
    # Apply minimum fare
    minimum_fare = Decimal(str(cfg.get("minimum_fare", 0)))
    if total < minimum_fare:
        total = minimum_fare
    
    return float(total)

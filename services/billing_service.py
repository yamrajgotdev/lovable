"""
Billing Service for Rides
Handles waiting time charges and final fare calculation.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict
from django.utils import timezone

from rides.models import Ride, FareRule

logger = logging.getLogger('rides.billing')


class BillingService:
    """
    Handles all billing calculations for rides.
    
    Waiting Time Billing:
    - First 2 minutes (120 seconds): FREE
    - After 2 minutes: ₹3 per minute
    
    Final Fare Components:
    - Base fare (from fare rules)
    - Distance fare (per km)
    - Time fare (per minute of ride)
    - Waiting charge
    - Platform commission
    """

    # Waiting time constants
    WAITING_FREE_SECONDS = 120  # 2 minutes free
    WAITING_CHARGE_PER_MINUTE = Decimal('3.00')  # ₹3/min after free period

    # Platform commission (typically 15-25%)
    PLATFORM_COMMISSION_PERCENT = Decimal('0.20')  # 20%

    @classmethod
    def calculate_waiting_charge(cls, waiting_seconds: int) -> Decimal:
        """
        Calculate waiting charge based on time.
        
        Args:
            waiting_seconds: Total waiting time in seconds
            
        Returns:
            Decimal: Waiting charge in INR
        """
        if waiting_seconds <= cls.WAITING_FREE_SECONDS:
            return Decimal('0.00')
        
        chargeable_seconds = waiting_seconds - cls.WAITING_FREE_SECONDS
        chargeable_minutes = Decimal(chargeable_seconds) / Decimal('60')
        
        # Round up partial minutes
        chargeable_minutes = chargeable_minutes.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        
        charge = chargeable_minutes * cls.WAITING_CHARGE_PER_MINUTE
        return charge.quantize(Decimal('0.01'))

    @classmethod
    def get_waiting_time_breakdown(cls, waiting_seconds: int) -> Dict:
        """
        Get detailed breakdown of waiting time billing.
        
        Returns:
            Dict with free_time, chargeable_time, charge, message
        """
        free_seconds = min(waiting_seconds, cls.WAITING_FREE_SECONDS)
        chargeable_seconds = max(0, waiting_seconds - cls.WAITING_FREE_SECONDS)
        charge = cls.calculate_waiting_charge(waiting_seconds)
        
        return {
            'total_seconds': waiting_seconds,
            'free_seconds': free_seconds,
            'chargeable_seconds': chargeable_seconds,
            'charge': float(charge),
            'charge_per_minute': float(cls.WAITING_CHARGE_PER_MINUTE),
            'message': cls._get_waiting_message(waiting_seconds)
        }

    @classmethod
    def _get_waiting_message(cls, waiting_seconds: int) -> str:
        """Generate human-readable waiting message."""
        if waiting_seconds <= cls.WAITING_FREE_SECONDS:
            remaining = cls.WAITING_FREE_SECONDS - waiting_seconds
            return f"Free waiting: {remaining} seconds remaining"
        else:
            chargeable = waiting_seconds - cls.WAITING_FREE_SECONDS
            minutes = chargeable // 60 + (1 if chargeable % 60 > 0 else 0)
            return f"₹{cls.WAITING_CHARGE_PER_MINUTE}/min after 2 min free"

    @classmethod
    def calculate_final_fare(cls, ride: Ride) -> Dict:
        """
        Calculate complete fare breakdown for a ride.
        
        Returns:
            Dict with all fare components and total
        """
        try:
            # Get fare rules for vehicle type
            fare_rule = FareRule.objects.filter(
                vehicle_type=ride.vehicle_type,
                is_active=True
            ).first()
            
            if not fare_rule:
                # Fallback to defaults
                base_fare = Decimal('50.00')
                per_km = Decimal('12.00')
                per_minute = Decimal('1.50')
            else:
                base_fare = fare_rule.base_fare
                per_km = fare_rule.per_km
                per_minute = fare_rule.per_minute

            # Calculate distance fare
            distance_km = Decimal(str(ride.distance_km or 0))
            distance_fare = (distance_km * per_km).quantize(Decimal('0.01'))
            
            # Calculate time fare (ride duration)
            time_fare = Decimal('0.00')
            if ride.start_time and ride.end_time:
                ride_duration_minutes = Decimal(
                    (ride.end_time - ride.start_time).total_seconds()
                ) / Decimal('60')
                time_fare = (ride_duration_minutes * per_minute).quantize(Decimal('0.01'))
            
            # Get waiting charge (already calculated at OTP verification)
            waiting_charge = Decimal(str(ride.waiting_charge or 0))
            
            # Calculate subtotal
            subtotal = base_fare + distance_fare + time_fare + waiting_charge
            
            # Apply promo discount if any
            discount = Decimal(str(ride.promo_discount_amount or 0))
            after_discount = subtotal - discount
            if after_discount < Decimal('0'):
                after_discount = Decimal('0')
            
            # Calculate platform commission
            platform_commission = (after_discount * cls.PLATFORM_COMMISSION_PERCENT).quantize(Decimal('0.01'))
            
            # Driver share
            driver_share = after_discount - platform_commission
            
            # Total amount passenger pays
            total = after_discount
            
            return {
                'base_fare': base_fare,
                'distance_fare': distance_fare,
                'time_fare': time_fare,
                'waiting_charge': waiting_charge,
                'subtotal_before_discount': subtotal,
                'promo_discount': discount,
                'total': total.quantize(Decimal('0.01')),
                'platform_commission': platform_commission,
                'driver_share': driver_share.quantize(Decimal('0.01'))
            }
            
        except Exception as e:
            logger.exception(f"Error calculating fare for ride {ride.id}: {e}")
            # Return safe defaults
            return {
                'base_fare': Decimal('0'),
                'distance_fare': Decimal('0'),
                'time_fare': Decimal('0'),
                'waiting_charge': Decimal('0'),
                'subtotal_before_discount': Decimal('0'),
                'promo_discount': Decimal('0'),
                'total': Decimal('0'),
                'platform_commission': Decimal('0'),
                'driver_share': Decimal('0')
            }

    @classmethod
    def calculate_estimate(cls, distance_km: float, vehicle_type: str, 
                          estimated_minutes: int = 0) -> Dict:
        """
        Calculate fare estimate before ride.
        
        Returns:
            Dict with estimate breakdown
        """
        try:
            fare_rule = FareRule.objects.filter(
                vehicle_type=vehicle_type,
                is_active=True
            ).first()
            
            if not fare_rule:
                base_fare = Decimal('50.00')
                per_km = Decimal('12.00')
                per_minute = Decimal('1.50')
                minimum_fare = Decimal('60.00')
            else:
                base_fare = fare_rule.base_fare
                per_km = fare_rule.per_km
                per_minute = fare_rule.per_minute
                minimum_fare = fare_rule.minimum_fare or Decimal('60.00')
            
            distance = Decimal(str(distance_km))
            
            base_fare = base_fare
            distance_fare = (distance * per_km).quantize(Decimal('0.01'))
            time_fare = (Decimal(str(estimated_minutes)) * per_minute).quantize(Decimal('0.01'))
            
            # Note: waiting charge not included in estimate (unknown upfront)
            total = base_fare + distance_fare + time_fare
            
            # Apply minimum fare
            if total < minimum_fare:
                total = minimum_fare
            
            return {
                'base_fare': float(base_fare),
                'distance_fare': float(distance_fare),
                'time_fare': float(time_fare),
                'total': float(total.quantize(Decimal('0.01'))),
                'minimum_fare': float(minimum_fare),
                'note': 'Waiting charges (if any) will be added at ₹3/min after 2 min'
            }
            
        except Exception as e:
            logger.exception(f"Error calculating estimate: {e}")
            return {
                'base_fare': 0,
                'distance_fare': 0,
                'time_fare': 0,
                'total': 0,
                'minimum_fare': 0,
                'note': 'Error calculating estimate'
            }

    @classmethod
    def get_waiting_stopwatch_data(cls, ride_id: int) -> Dict:
        """
        Get real-time stopwatch data for waiting UI.
        Called repeatedly while driver is waiting at pickup.
        """
        try:
            ride = Ride.objects.get(id=ride_id)
            
            if ride.status not in [Ride.STATUS_ARRIVED, Ride.STATUS_OTP_VERIFIED]:
                return {'error': 'Ride not in waiting state', 'active': False}
            
            if not ride.arrival_time:
                return {'error': 'Arrival time not set', 'active': False}
            
            # Calculate elapsed time
            elapsed_seconds = int((timezone.now() - ride.arrival_time).total_seconds())
            
            # Check if waiting charge applies
            free_seconds = cls.WAITING_FREE_SECONDS
            chargeable_seconds = max(0, elapsed_seconds - free_seconds)
            
            # Format time display
            minutes = elapsed_seconds // 60
            seconds = elapsed_seconds % 60
            time_display = f"{minutes}:{seconds:02d}"
            
            return {
                'active': not ride.waiting_charge_locked,
                'elapsed_seconds': elapsed_seconds,
                'time_display': time_display,
                'free_seconds': free_seconds,
                'chargeable_seconds': chargeable_seconds,
                'charge_per_minute': float(cls.WAITING_CHARGE_PER_MINUTE),
                'current_charge': float(cls.calculate_waiting_charge(elapsed_seconds)),
                'charge_applies_after_seconds': free_seconds,
                'charge_applies': elapsed_seconds > free_seconds,
                'message': f"Waiting: {time_display}" + (
                    f" | ₹{cls.WAITING_CHARGE_PER_MINUTE}/min after 2 min" 
                    if elapsed_seconds <= free_seconds 
                    else f" | Charge: ₹{cls.calculate_waiting_charge(elapsed_seconds)}"
                )
            }
            
        except Ride.DoesNotExist:
            return {'error': 'Ride not found', 'active': False}

"""
Admin Pricing Management API
Endpoints for managing fare rules from the frontend admin panel.
"""
import logging
from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache

from .models import FareRule
from .pricing import invalidate_fare_rules_cache

logger = logging.getLogger('rides4u')


class IsAdminUser:
    """Custom permission to check if user is admin/staff."""
    def has_permission(self, request, view):
        return request.user and (request.user.is_staff or request.user.is_superuser)


class FareRulesListView(APIView):
    """
    GET /api/admin/fare-rules/
    List all fare rules for admin panel.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Check if user is admin/staff
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        rules = FareRule.objects.all().order_by('vehicle_type')
        data = []
        for rule in rules:
            data.append({
                'id': rule.id,
                'vehicle_type': rule.vehicle_type,
                'vehicle_type_display': rule.get_vehicle_type_display(),
                'base_fare': float(rule.base_fare),
                'per_km': float(rule.per_km),
                'per_minute': float(rule.per_minute),
                'surge_multiplier': float(rule.surge_multiplier),
                'tax_percentage': float(rule.tax_percentage),
                'minimum_fare': float(rule.minimum_fare),
                'cancellation_fee': float(rule.cancellation_fee),
                'is_active': rule.is_active,
                'updated_at': rule.updated_at.isoformat() if rule.updated_at else None,
            })

        return Response({
            'success': True,
            'fare_rules': data
        })


class FareRuleDetailView(APIView):
    """
    GET /api/admin/fare-rules/<id>/
    PUT /api/admin/fare-rules/<id>/
    Get or update a specific fare rule.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, rule_id):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            rule = FareRule.objects.get(id=rule_id)
        except FareRule.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Fare rule not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            'success': True,
            'fare_rule': {
                'id': rule.id,
                'vehicle_type': rule.vehicle_type,
                'vehicle_type_display': rule.get_vehicle_type_display(),
                'base_fare': float(rule.base_fare),
                'per_km': float(rule.per_km),
                'per_minute': float(rule.per_minute),
                'surge_multiplier': float(rule.surge_multiplier),
                'tax_percentage': float(rule.tax_percentage),
                'minimum_fare': float(rule.minimum_fare),
                'cancellation_fee': float(rule.cancellation_fee),
                'is_active': rule.is_active,
                'updated_at': rule.updated_at.isoformat() if rule.updated_at else None,
            }
        })

    def put(self, request, rule_id):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            rule = FareRule.objects.get(id=rule_id)
        except FareRule.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Fare rule not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Update fields
        try:
            if 'base_fare' in request.data:
                rule.base_fare = Decimal(str(request.data['base_fare']))
            if 'per_km' in request.data:
                rule.per_km = Decimal(str(request.data['per_km']))
            if 'per_minute' in request.data:
                rule.per_minute = Decimal(str(request.data['per_minute']))
            if 'surge_multiplier' in request.data:
                rule.surge_multiplier = Decimal(str(request.data['surge_multiplier']))
            if 'minimum_fare' in request.data:
                rule.minimum_fare = Decimal(str(request.data['minimum_fare']))
            if 'cancellation_fee' in request.data:
                rule.cancellation_fee = Decimal(str(request.data['cancellation_fee']))
            if 'tax_percentage' in request.data:
                rule.tax_percentage = Decimal(str(request.data['tax_percentage']))
            if 'is_active' in request.data:
                rule.is_active = bool(request.data['is_active'])

            rule.save()
            invalidate_fare_rules_cache()

            logger.info(f"Fare rule updated by admin {request.user.id}: {rule.vehicle_type}")

            return Response({
                'success': True,
                'message': 'Fare rule updated successfully',
                'fare_rule': {
                    'id': rule.id,
                    'vehicle_type': rule.vehicle_type,
                    'vehicle_type_display': rule.get_vehicle_type_display(),
                    'base_fare': float(rule.base_fare),
                    'per_km': float(rule.per_km),
                    'per_minute': float(rule.per_minute),
                    'surge_multiplier': float(rule.surge_multiplier),
                    'tax_percentage': float(rule.tax_percentage),
                    'minimum_fare': float(rule.minimum_fare),
                    'cancellation_fee': float(rule.cancellation_fee),
                    'is_active': rule.is_active,
                    'updated_at': rule.updated_at.isoformat() if rule.updated_at else None,
                }
            })
        except Exception as e:
            logger.error(f"Error updating fare rule: {e}")
            return Response(
                {'success': False, 'message': f'Error updating fare rule: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )


class FareRuleBulkUpdateView(APIView):
    """
    POST /api/admin/fare-rules/bulk-update/
    Update multiple fare rules at once.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        fare_rules = request.data.get('fare_rules', [])
        if not fare_rules or not isinstance(fare_rules, list):
            return Response(
                {'success': False, 'message': 'fare_rules array is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        updated = []
        errors = []

        for rule_data in fare_rules:
            rule_id = rule_data.get('id')
            if not rule_id:
                errors.append({'error': 'Rule ID is required'})
                continue

            try:
                rule = FareRule.objects.get(id=rule_id)

                if 'base_fare' in rule_data:
                    rule.base_fare = Decimal(str(rule_data['base_fare']))
                if 'per_km' in rule_data:
                    rule.per_km = Decimal(str(rule_data['per_km']))
                if 'per_minute' in rule_data:
                    rule.per_minute = Decimal(str(rule_data['per_minute']))
                if 'surge_multiplier' in rule_data:
                    rule.surge_multiplier = Decimal(str(rule_data['surge_multiplier']))
                if 'minimum_fare' in rule_data:
                    rule.minimum_fare = Decimal(str(rule_data['minimum_fare']))
                if 'cancellation_fee' in rule_data:
                    rule.cancellation_fee = Decimal(str(rule_data['cancellation_fee']))
                if 'tax_percentage' in rule_data:
                    rule.tax_percentage = Decimal(str(rule_data['tax_percentage']))
                if 'is_active' in rule_data:
                    rule.is_active = bool(rule_data['is_active'])

                rule.save()
                updated.append({
                    'id': rule.id,
                    'vehicle_type': rule.vehicle_type,
                })
            except FareRule.DoesNotExist:
                errors.append({'id': rule_id, 'error': 'Fare rule not found'})
            except Exception as e:
                errors.append({'id': rule_id, 'error': str(e)})

        # Invalidate cache after bulk update
        if updated:
            invalidate_fare_rules_cache()

        return Response({
            'success': len(errors) == 0,
            'message': f'Updated {len(updated)} fare rules',
            'updated': updated,
            'errors': errors if errors else None,
        })


class FareRuleCreateView(APIView):
    """
    POST /api/admin/fare-rules/
    Create a new fare rule (for new vehicle types).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        vehicle_type = request.data.get('vehicle_type')
        if not vehicle_type:
            return Response(
                {'success': False, 'message': 'vehicle_type is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if vehicle type already exists
        if FareRule.objects.filter(vehicle_type=vehicle_type).exists():
            return Response(
                {'success': False, 'message': f'Fare rule for {vehicle_type} already exists'},
                status=status.HTTP_409_CONFLICT
            )

        try:
            rule = FareRule.objects.create(
                vehicle_type=vehicle_type,
                base_fare=Decimal(str(request.data.get('base_fare', 0))),
                per_km=Decimal(str(request.data.get('per_km', 0))),
                per_minute=Decimal(str(request.data.get('per_minute', 0))),
                surge_multiplier=Decimal(str(request.data.get('surge_multiplier', 1.0))),
                minimum_fare=Decimal(str(request.data.get('minimum_fare', 0))),
                cancellation_fee=Decimal(str(request.data.get('cancellation_fee', 0))),
                tax_percentage=Decimal(str(request.data.get('tax_percentage', 5.0))),
                is_active=request.data.get('is_active', True),
            )
            invalidate_fare_rules_cache()

            logger.info(f"New fare rule created by admin {request.user.id}: {vehicle_type}")

            return Response({
                'success': True,
                'message': 'Fare rule created successfully',
                'fare_rule': {
                    'id': rule.id,
                    'vehicle_type': rule.vehicle_type,
                    'vehicle_type_display': rule.get_vehicle_type_display(),
                    'base_fare': float(rule.base_fare),
                    'per_km': float(rule.per_km),
                    'per_minute': float(rule.per_minute),
                    'surge_multiplier': float(rule.surge_multiplier),
                    'tax_percentage': float(rule.tax_percentage),
                    'minimum_fare': float(rule.minimum_fare),
                    'cancellation_fee': float(rule.cancellation_fee),
                    'is_active': rule.is_active,
                }
            }, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Error creating fare rule: {e}")
            return Response(
                {'success': False, 'message': f'Error creating fare rule: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

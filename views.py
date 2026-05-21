"""
Payment API Endpoints
"""
import json
import logging
import os
from decimal import Decimal
from urllib.parse import quote
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny

from rides.models import Ride
from drivers.models import Driver
from payments.models import Payment, SupportTicket, QRCodePayment, WalletTransaction, DriverWallet
from payments.serializers import (
    PaymentSerializer, PaymentOrderSerializer, CreateOrderSerializer,
    ConfirmCashSerializer, WalletSerializer, WalletTransactionSerializer,
    SupportTicketSerializer, CreateSupportTicketSerializer
)
from payments.services.payment_service import PaymentService, PaymentReconciliation
from payments.services.wallet_service import WalletService
from payments.services.support_service import SupportService
from utils.razorpay_service import get_razorpay_service
from authsystem.views import get_authenticated_user
from rides.services.ride_cache_service import RideCacheService
from rides.services.notification_center import NotificationCenter
from rides.models import Notification

logger = logging.getLogger('rides4u')


def _build_canonical_upi_qr(amount, ride_id):
    """
    Build canonical UPI QR payload from env-configured business UPI.
    This is a fallback display payload; webhook verification remains source of truth.
    """
    business_upi = (os.environ.get("RIDES4U_BUSINESS_UPI_ID") or os.environ.get("BUSINESS_UPI_ID") or "").strip()
    if not business_upi:
        return None
    merchant_name = (os.environ.get("RIDES4U_BUSINESS_NAME") or "Rides4U").strip()
    encoded_upi = quote(business_upi, safe="")
    encoded_name = quote(merchant_name, safe="")
    encoded_note = quote(f"Ride_{ride_id}", safe="")
    return f"upi://pay?pa={encoded_upi}&pn={encoded_name}&am={amount}&tn={encoded_note}&cu=INR"


class CreateOrderView(APIView):
    """
    POST /api/v1/payments/create-order/

    Create a payment order (Razorpay or cash).
    Called by passenger after ride finishes to initiate payment.
    Requires authentication.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # request.user is guaranteed by IsAuthenticated
        user = request.user

        serializer = CreateOrderSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        ride_id = serializer.validated_data['ride_id']
        payment_method = serializer.validated_data['payment_method']

        try:
            ride = Ride.objects.get(id=ride_id)

            # ── IDOR fix: compare against request.user, not the broken request.user.id ──
            if ride.passenger_id != user.id:
                logger.warning(
                    'create_order_unauthorized: user_id=%d ride_id=%d', user.id, ride_id
                )
                return Response(
                    {'error': 'You do not have permission to pay for this ride.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Order creation is only valid once payment is explicitly required.
            allowed_payment_states = {
                'payment_required',
                'payment_confirmed',
                'completed',
            }
            if (ride.status or '').lower() not in allowed_payment_states:
                return Response({
                    'error': f'Ride is in {ride.status} state. Payment order can only be created after payment is required.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Create payment intent
            payment = PaymentService.create_payment_intent(ride_id, payment_method)

            response_data = {
                'payment_id': payment.id,
                'ride_id': ride.id,
                'amount': float(payment.amount),
                'method': payment_method,
                'status': 'pending'
            }

            # For Razorpay, include order ID for checkout
            if payment_method == 'razorpay_online' and payment.razorpay_order_id:
                razorpay_service = get_razorpay_service()
                response_data['razorpay_order_id'] = payment.razorpay_order_id
                response_data['razorpay_key_id'] = razorpay_service.key_id

            return Response(response_data, status=status.HTTP_201_CREATED)

        except Ride.DoesNotExist:
            return Response({'error': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error('create_order_error: %s', str(e))
            return Response(
                {'error': 'Payment order could not be created. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@method_decorator(csrf_exempt, name='dispatch')
class WebhookView(APIView):
    """
    POST /api/v1/payments/webhook/

    Razorpay webhook endpoint for payment notifications.
    Must remain AllowAny — Razorpay sends this without user tokens.
    Signature verification provides security instead.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            raw_body = request.body or b''
            content_type = (request.content_type or '').lower()
            data = {}
            if 'application/json' in content_type and raw_body:
                data = json.loads(raw_body)

            event = data.get('event', '')
            event_id = data.get('id')  # Razorpay unique event ID
            payload = data.get('payload', {})
            
            # ── Webhook Idempotency (Atomic Redis + DB Fallback) ──
            if event_id:
                from utils.idempotency import IdempotencyService
                if not IdempotencyService.is_allowed(f"webhook:{event_id}", ttl=3600):
                    logger.warning('webhook_duplicate_detected: event_id=%s', event_id, extra={'event_type': 'webhook_duplicate', 'event_id': event_id})
                    return Response({'status': 'already_processed'}, status=status.HTTP_200_OK)

            payment_entity = payload.get('payment', {}).get('entity', {})
            order_entity = payload.get('order', {}).get('entity', {})

            # Razorpay sends webhook signature in header.
            signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE') or data.get('razorpay_signature')

            order_id = payment_entity.get('order_id') or order_entity.get('id') or request.POST.get('razorpay_order_id')
            payment_id = payment_entity.get('id') or request.POST.get('razorpay_payment_id')
            payment_status = (payment_entity.get('status') or '').lower()

            logger.info(
                '[PAYMENT] webhook_received: event=%s order=%s payment=%s',
                event,
                order_id,
                payment_id,
            )

            razorpay_service = get_razorpay_service()
            if not razorpay_service.verify_webhook_signature(raw_body, signature):
                logger.warning('webhook_signature_invalid: event=%s order=%s', event, order_id)
                return Response({'error': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

            payment = None
            if order_id:
                payment = Payment.objects.filter(razorpay_order_id=order_id).first()
            if not payment and payment_id:
                payment = Payment.objects.filter(razorpay_payment_id=payment_id).first()

            if not payment:
                logger.warning('webhook_payment_not_found_locally: event=%s order=%s', event, order_id)
                return Response({'status': 'success'}, status=status.HTTP_200_OK)

            success_events = {'payment.captured', 'order.paid'}
            failure_events = {'payment.failed'}

            if event in failure_events or payment_status == 'failed':
                PaymentService.handle_payment_failure(payment.id, reason=f'Webhook {event or payment_status or "failed"}')
                logger.info('webhook_marked_failed: payment_id=%d event=%s', payment.id, event)
                return Response({'status': 'success'}, status=status.HTTP_200_OK)

            if event in success_events or payment_status == 'captured':
                resolved_payment_id = payment_id or payment.razorpay_payment_id
                if not resolved_payment_id:
                    logger.error('webhook_missing_payment_id: order=%s event=%s', order_id, event)
                    return Response({'error': 'Missing payment id'}, status=status.HTTP_400_BAD_REQUEST)

                with transaction.atomic():
                    # Lock payment row to prevent race conditions
                    payment = Payment.objects.select_for_update().get(id=payment.id)
                    
                    # Re-check status after locking
                    if payment.status == 'paid':
                        logger.info('webhook_idempotent: order=%s already paid', order_id)
                        return Response({'status': 'success'}, status=status.HTTP_200_OK)
                    
                    # Bug 1 Fix: Ensure cancellation terminates ALL payment flows
                    ride = payment.ride
                    if ride.status == Ride.STATUS_CANCELLED:
                        return Response({
                            'error': 'Ride has been cancelled. Payment cannot be verified.',
                            'code': 'RIDE_CANCELLED'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    PaymentService.process_online_payment(payment.id, resolved_payment_id)
                
                logger.info('webhook_processed_paid: payment_id=%d', payment.id)
                return Response({'status': 'success'}, status=status.HTTP_200_OK)

            logger.info('webhook_ignored_event: event=%s order=%s', event, order_id)
            return Response({'status': 'ignored', 'event': event}, status=status.HTTP_200_OK)

        except ValueError as e:
            logger.error('webhook_validation_error: %s', str(e))
            return Response({'status': 'validation_error'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error('webhook_error: %s', str(e))
            return Response({'status': 'error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ConfirmCashView(APIView):
    """
    POST /api/v1/payments/confirm-cash/

    Driver confirms cash payment collection.
    Only available to drivers after ride finishes.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        serializer = ConfirmCashSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        ride_id = serializer.validated_data['ride_id']

        try:
            ride = Ride.objects.get(id=ride_id)

            # Bug 1 Fix: Ensure cancellation terminates ALL payment flows
            if ride.status == Ride.STATUS_CANCELLED:
                return Response({
                    'error': 'Ride has been cancelled. Payment cannot be confirmed.',
                    'code': 'RIDE_CANCELLED'
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                driver = Driver.objects.get(user=user)
            except Driver.DoesNotExist:
                return Response(
                    {'error': 'User is not a driver'},
                    status=status.HTTP_403_FORBIDDEN
                )

            if ride.driver_id != driver.id:
                return Response(
                    {'error': 'Unauthorized — you are not the driver of this ride'},
                    status=status.HTTP_403_FORBIDDEN
                )

            try:
                payment = Payment.objects.get(ride=ride)
            except Payment.DoesNotExist:
                return Response(
                    {'error': 'No payment found for this ride'},
                    status=status.HTTP_404_NOT_FOUND
                )

            if payment.method != 'cash':
                return Response(
                    {'error': f'Payment method is {payment.method}, not cash'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if payment.status not in ['pending', 'cash_collected']:
                return Response(
                    {'error': f'Payment status is {payment.status}, cannot confirm'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # PaymentService.confirm_cash_payment(ride_id)
            # ── UPDATED LOGIC ──
            # Confirm cash collection, update payment status to completed, 
            # and ride status to payment_confirmed.
            payment = PaymentService.confirm_cash_payment(ride_id)
            RideCacheService.invalidate(ride.id, "payment_update")
            logger.info(
                "[PAYMENT] cash_confirmed ride=%s payment=%s status=%s",
                ride.id,
                payment.id,
                payment.status,
            )

            return Response({
                'payment_id': payment.id,
                'status': payment.status,
                'amount': float(payment.amount),
                'ride_id': ride.id
            }, status=status.HTTP_200_OK)

        except Ride.DoesNotExist:
            return Response({'error': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error('confirm_cash_error: %s', str(e))
            return Response(
                {'error': 'Could not confirm cash payment. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PaymentStatusView(APIView):
    """
    GET /api/v1/payments/ride/{ride_id}/

    Get payment status for a ride.
    Accessible by passenger or driver of the ride.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        user = request.user

        try:
            ride = Ride.objects.get(id=ride_id)

            # Verify user is the passenger or the assigned driver
            is_passenger = ride.passenger_id == user.id
            is_driver = False
            if not is_passenger:
                try:
                    driver = Driver.objects.get(user=user)
                    is_driver = (ride.driver_id == driver.id)
                except Driver.DoesNotExist:
                    pass

            if not is_passenger and not is_driver:
                return Response({'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

            try:
                payment = Payment.objects.get(ride=ride)
            except Payment.DoesNotExist:
                return Response(
                    {'error': 'No payment found for this ride'},
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = PaymentSerializer(payment)
            return Response(serializer.data)

        except Ride.DoesNotExist:
            return Response({'error': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)


class DriverWalletView(APIView):
    """
    GET /api/v1/driver/wallet/

    Get driver's wallet balance and summary including daily/weekly/monthly earnings.
    Only accessible to drivers.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'User is not a driver'},
                status=status.HTTP_403_FORBIDDEN
            )

        summary = WalletService.get_wallet_summary(driver)
        stats = summary['daily_stats']
        
        return Response({
            'wallet': {
                'balance': summary['balance'],
                'total_earned': summary['total_earned'],
                'total_withdrawn': summary['total_withdrawn'],
                'available': summary['available']
            },
            'earnings_stats': {
                'daily': float(stats['daily']),
                'weekly': float(stats['weekly']),
                'monthly': float(stats['monthly']),
                'rides_today': stats['total_rides_today']
            }
        })


class DriverTransactionsView(APIView):
    """
    GET /api/v1/driver/wallet/transactions/

    Get driver's transaction history with pagination.
    Query params: page (DRF PageNumberPagination), type=ride_earning|adjustment
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'User is not a driver'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Keep legacy limit/offset params alongside DRF pagination
        limit = min(int(request.query_params.get('limit', 50)), 200)  # cap at 200
        offset = int(request.query_params.get('offset', 0))
        type_filter = request.query_params.get('type')

        transactions = WalletService.get_transactions(driver, limit, offset, type_filter)
        total = WalletService.get_transaction_count(driver, type_filter)

        serializer = WalletTransactionSerializer(transactions, many=True)

        return Response({
            'total': total,
            'limit': limit,
            'offset': offset,
            'transactions': serializer.data
        })


class PassengerTransactionsView(APIView):
    """
    GET /api/v1/passenger/wallet/transactions/

    Get passenger's transaction history (ride payments).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        limit = min(int(request.query_params.get('limit', 50)), 200)
        offset = int(request.query_params.get('offset', 0))
        
        transactions = WalletTransaction.objects.filter(
            actor='passenger',
            actor_id=str(user.id)
        ).order_by('-created_at')[offset:offset+limit]
        
        total = WalletTransaction.objects.filter(
            actor='passenger',
            actor_id=str(user.id)
        ).count()

        serializer = WalletTransactionSerializer(transactions, many=True)

        return Response({
            'total': total,
            'limit': limit,
            'offset': offset,
            'transactions': serializer.data
        })


class SupportTicketCreateView(APIView):
    """
    POST /api/v1/payments/support-ticket/

    Driver creates a support ticket for payment issue.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        serializer = CreateSupportTicketSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        ride_id = serializer.validated_data['ride_id']
        issue_type = serializer.validated_data['issue_type']
        description = serializer.validated_data['description']

        try:
            ride = Ride.objects.get(id=ride_id)

            try:
                driver = Driver.objects.get(user=user)
            except Driver.DoesNotExist:
                return Response(
                    {'error': 'User is not a driver'},
                    status=status.HTTP_403_FORBIDDEN
                )

            if ride.driver_id != driver.id:
                return Response(
                    {'error': 'Unauthorized — you are not the driver of this ride'},
                    status=status.HTTP_403_FORBIDDEN
                )

            ticket = SupportService.create_ticket(ride, driver, issue_type, description)
            response_data = SupportTicketSerializer(ticket).data
            return Response(response_data, status=status.HTTP_201_CREATED)

        except Ride.DoesNotExist:
            return Response({'error': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error('support_ticket_error: %s', str(e))
            return Response(
                {'error': 'Could not create support ticket. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PaymentReconcileView(APIView):
    """
    POST /api/v1/payments/reconcile/

    Manually trigger payment reconciliation.
    Restricted to staff/admin users only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # ── Staff-only guard ──────────────────────────────────────────────
        if not request.user.is_staff:
            logger.warning(
                'reconcile_forbidden: user_id=%d ip=%s',
                request.user.id, request.META.get('REMOTE_ADDR')
            )
            return Response(
                {'error': 'Admin access required.'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            result = PaymentReconciliation.reconcile_pending_payments()
            return Response(result)
        except Exception as e:
            logger.error('reconciliation_error: %s', str(e))
            return Response(
                {'error': 'Reconciliation failed. Check server logs.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CheckPaymentBeforeCompleteView(APIView):
    """
    GET /api/v1/payments/ride/{ride_id}/check-before-complete/
    
    Driver checks payment status before completing ride.
    Returns payment status and shows if cash confirmation is needed.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'Only drivers can check payment status'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            ride = Ride.objects.get(id=ride_id, driver=driver)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or not assigned to you'},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            payment = Payment.objects.get(ride=ride)
        except Payment.DoesNotExist:
            return Response({
                'ride_id': ride_id,
                'payment_status': 'unknown',
                'payment_method': None,
                'requires_cash_confirmation': False,
                'can_complete': True,
                'message': 'No payment record found'
            })

        # Determine if driver needs to confirm cash
        requires_cash_confirmation = (
            payment.method == 'cash' and 
            payment.status not in ['paid', 'cash_collected']
        )

        # Check if online payment is verified
        is_online_paid = (
            payment.method == 'razorpay_online' and 
            payment.status == 'paid'
        )

        can_complete = is_online_paid or not requires_cash_confirmation

        return Response({
            'ride_id': ride_id,
            'payment_status': payment.status,
            'payment_method': payment.method,
            'amount': float(payment.amount),
            'requires_cash_confirmation': requires_cash_confirmation,
            'is_online_paid': is_online_paid,
            'can_complete': can_complete,
            'message': (
                'Payment verified' if is_online_paid else
                'Please confirm cash collection' if requires_cash_confirmation else
                'Ready to complete'
            )
        })


class DriverQRCodeView(APIView):
    """
    GET/POST /api/v1/payments/ride/{ride_id}/qr-code/
    
    Driver gets or creates QR code for passenger to scan and pay.
    - GET: Returns existing active QR code for the ride
    - POST: Creates new QR code if none exists or expired
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        """Get existing active QR code for the ride."""
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'Only drivers can access QR codes'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            ride = Ride.objects.get(id=ride_id, driver=driver)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or not assigned to you'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Look for existing active QR code
        qr_payment = QRCodePayment.objects.filter(
            ride=ride,
            driver=driver,
            status='active'
        ).first()

        if qr_payment and not qr_payment.is_expired():
            return Response({
                'qr_code_id': qr_payment.id,
                'qr_code_data': qr_payment.qr_code_data,
                'qr_code_image_url': qr_payment.qr_code_image_url,
                'amount': float(qr_payment.amount),
                'upi_id': qr_payment.upi_id,
                'status': qr_payment.status,
                'expires_at': qr_payment.expires_at,
                'message': 'Use existing QR code'
            })

        # No active QR code found
        return Response({
            'qr_code_id': None,
            'message': 'No active QR code. Create one by calling POST.',
            'ride_id': ride_id,
            'amount': float(ride.final_fare) if ride.final_fare else float(ride.estimated_fare) if ride.estimated_fare else 0
        })

    def post(self, request, ride_id):
        """Create new QR code for the ride."""
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'Only drivers can create QR codes'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            ride = Ride.objects.get(id=ride_id, driver=driver)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or not assigned to you'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get payment amount
        try:
            payment = Payment.objects.get(ride=ride)
            amount = payment.amount
        except Payment.DoesNotExist:
            amount = ride.final_fare or ride.estimated_fare or 0

        # Expire any existing active QR codes for this ride
        QRCodePayment.objects.filter(
            ride=ride,
            status='active'
        ).update(status='expired')

        # Get driver's UPI ID if profile field exists, else fallback to request body only.
        driver_upi = (getattr(driver, 'payment_upi', '') or '').strip()
        upi_id = request.data.get('upi_id', '').strip() or driver_upi
        
        # Validate UPI ID is present
        if not upi_id:
            logger.error(f'qr_generation_failed: no_upi_id for driver={driver.id}, ride={ride_id}')
            return Response(
                {'error': 'Driver UPI ID not configured. Please ask driver to set up UPI in profile.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Generate UPI payment string for QR code with proper URL encoding
        # Format: upi://pay?pa=UPI_ID&pn=NAME&am=AMOUNT&cu=INR&tn=NOTE
        from django.utils import timezone
        from datetime import timedelta
        from urllib.parse import quote
        
        # URL encode all parameters to handle special characters
        encoded_upi = quote(upi_id, safe='')
        encoded_name = quote(driver.name or 'Driver', safe='')
        encoded_note = quote(f'Ride {ride_id} Payment', safe='')
        
        qr_data = f"upi://pay?pa={encoded_upi}&pn={encoded_name}&am={amount}&cu=INR&tn={encoded_note}"
        
        # Debug log actual values being used
        logger.info(f'qr_generation: ride={ride_id}, driver={driver.id}, upi={upi_id}, amount={amount}')
        
        # Create new QR code payment record
        qr_payment = QRCodePayment.objects.create(
            ride=ride,
            driver=driver,
            passenger=ride.passenger,
            qr_code_data=qr_data,
            amount=amount,
            upi_id=upi_id,
            status='active',
            expires_at=timezone.now() + timedelta(minutes=30)  # 30 min expiry
        )

        logger.info(f'qr_code_created: ride={ride_id}, driver={driver.id}, amount={amount}')

        return Response({
            'qr_code_id': qr_payment.id,
            'qr_code_data': qr_payment.qr_code_data,
            'amount': float(qr_payment.amount),
            'upi_id': qr_payment.upi_id,
            'status': qr_payment.status,
            'expires_at': qr_payment.expires_at,
            'message': 'QR code created successfully. Show this to passenger for payment.'
        })


# ============================================================
# PAYMENT CONFIRMATION & WALLET CREDIT SYSTEM
# ADDED: Driver cash confirmation and online payment with atomic wallet credit
# ============================================================

class ConfirmCashCollectionView(APIView):
    """
    POST /api/v1/payments/confirm-cash-collection/

    Driver confirms cash payment collection from passenger.
    Triggered when ride is in payment stage and payment_method = CASH.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        ride_id = request.data.get('ride_id')

        if not ride_id:
            return Response(
                {'error': 'ride_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response(
                {'error': 'Only drivers can confirm cash collection'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            ride = Ride.objects.get(id=ride_id, driver=driver)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or not assigned to you'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate ride status - must be at/after destination before payment confirmation
        ride_status = (ride.status or "").lower()
        if ride_status not in {'payment_required', 'payment_confirmed', 'completed'}:
            return Response({
                'error': f'Cannot confirm payment for ride in {ride.status} status'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Validate payment method is CASH (or default to CASH if not set)
        payment_method_value = (ride.payment_method or "").upper()
        if not ride.payment_method or payment_method_value == Ride.PAYMENT_CASH:
            # Default to cash if no payment method selected
            if not ride.payment_method:
                ride.payment_method = Ride.PAYMENT_CASH
                ride.save(update_fields=['payment_method'])
        else:
            return Response({
                'error': f'Payment method is {ride.payment_method}, not CASH'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check if already processed (idempotency)
        if ride.payment_status == Ride.PAYMENT_SUCCESS:
            return Response({
                'success': True,
                'message': 'Payment already confirmed',
                'payment_status': 'SUCCESS',
                'ride_id': ride_id
            })

        with transaction.atomic():
            # Re-lock inside transaction to prevent concurrent double-processing.
            ride = Ride.objects.select_for_update().select_related('driver').get(id=ride.id, driver=driver)
            payment = Payment.objects.select_for_update().filter(ride=ride).first()

            # Idempotency check under lock: if already completed, return success.
            if ride.payment_processed and ride.status == Ride.STATUS_COMPLETED:
                return Response({
                    'success': True,
                    'message': 'Payment already processed',
                    'payment_status': 'SUCCESS',
                    'ride_id': ride_id
                })

            # Update ride payment fields and move through canonical states atomically.
            ride.payment_status = Ride.PAYMENT_SUCCESS
            ride.payment_received_at = timezone.now()
            ride.payment_processed = True
            ride.save(update_fields=['payment_status', 'payment_received_at', 'payment_processed'])
            from rides.services.state_machine import RideStateMachine
            if ride.status == Ride.STATUS_PAYMENT_REQUIRED:
                ok, msg, _ = RideStateMachine.transition(
                    ride_id=ride.id,
                    new_status=Ride.STATUS_PAYMENT_CONFIRMED,
                    actor_type='driver',
                    actor_id=driver.id,
                    metadata={'payment_method': Ride.PAYMENT_CASH},
                )
                if not ok:
                    return Response({
                        'success': False,
                        'error': f'Failed to confirm payment status: {msg}',
                    }, status=status.HTTP_409_CONFLICT)
                ride.refresh_from_db(fields=['status'])
            if ride.status == Ride.STATUS_PAYMENT_CONFIRMED:
                ok, msg, _ = RideStateMachine.transition(
                    ride_id=ride.id,
                    new_status=Ride.STATUS_COMPLETED,
                    actor_type='driver',
                    actor_id=driver.id,
                    metadata={'payment_method': Ride.PAYMENT_CASH},
                )
                if not ok:
                    return Response({
                        'success': False,
                        'error': f'Failed to complete ride: {msg}',
                    }, status=status.HTTP_409_CONFLICT)
                ride.refresh_from_db(fields=['status'])

            if ride.status != Ride.STATUS_COMPLETED:
                return Response({
                    'success': False,
                    'error': f'Ride did not reach completed state. Current status: {ride.status}',
                }, status=status.HTTP_409_CONFLICT)
            RideCacheService.invalidate(ride.id, "payment_update")

            # Update payment record if it exists
            if payment:
                payment.status = 'cash_collected'
                payment.save(update_fields=['status', 'updated_at'])

            # Exactly-once wallet credit using idempotency key
            driver_share = Decimal(ride.final_fare or ride.estimated_fare or 0)
            wallet_tx_key = f"ride_cash_credit:{ride.id}"
            wallet_tx, created = WalletTransaction.objects.get_or_create(
                idempotency_key=wallet_tx_key,
                defaults={
                    'ride': ride,
                    'actor': 'driver',
                    'actor_id': str(driver.id),
                    'transaction_type': 'credit',
                    'amount': driver_share,
                    'type': 'ride_earning',
                    'status': 'completed',
                    'description': f'Cash payment for ride #{ride_id}'
                }
            )

            if created:
                driver_wallet, _ = DriverWallet.objects.select_for_update().get_or_create(driver=driver)
                driver_wallet.balance += driver_share
                driver_wallet.total_earned += driver_share
                driver_wallet.save(update_fields=['balance', 'total_earned', 'updated_at'])
                logger.info("[PAYMENT] wallet_credit cash ride=%s driver=%s amount=%s", ride.id, driver.id, driver_share)
            else:
                logger.info("[PAYMENT] duplicate cash credit suppressed ride=%s tx=%s", ride.id, wallet_tx.id)

            def _post_commit_events():
                try:
                    from asgiref.sync import async_to_sync
                    from channels.layers import get_channel_layer
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'ride_{ride_id}',
                        {
                            'type': 'payment_update',
                            'notification': {
                                'type': 'cash_confirmed',
                                'ride_id': ride_id,
                                'payment_status': 'SUCCESS',
                                'payment_method': 'CASH',
                                'amount': float(ride.final_fare or ride.estimated_fare or 0),
                                'message': 'Cash payment confirmed by driver'
                            }
                        }
                    )
                except Exception as e:
                    logger.error(f'Error broadcasting payment update: {e}')

                NotificationCenter.create_and_broadcast(
                    ride.passenger,
                    Notification.TYPE_PAYMENT_SUCCESS,
                    "Payment successful.",
                    data={"ride_id": ride.id},
                )
                if ride.driver and ride.driver.user:
                    NotificationCenter.create_and_broadcast(
                        ride.driver.user,
                        Notification.TYPE_PAYMENT_SUCCESS,
                        "Payment successful.",
                        data={"ride_id": ride.id},
                    )
            transaction.on_commit(_post_commit_events)

        logger.info("[PAYMENT] cash_collection_confirmed ride=%s driver=%s", ride_id, driver.id)
        return Response({
            'success': True,
            'message': 'Cash payment confirmed',
            'payment_status': 'SUCCESS',
            'ride_id': ride_id,
            'amount': float(ride.final_fare or ride.estimated_fare or 0)
        })


class InitiateOnlinePaymentView(APIView):
    """
    POST /api/v1/payments/initiate-online/

    Passenger initiates online payment for a ride.
    Returns Razorpay QR code/order details.
    Called when passenger clicks "Pay Online" after reaching destination.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        ride_id = request.data.get('ride_id')

        if not ride_id:
            return Response(
                {'error': 'ride_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ride = Ride.objects.get(id=ride_id, passenger=user)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or you are not the passenger'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate ride status
        ride_status = (ride.status or "").lower()
        if ride_status not in {'payment_required', 'payment_confirmed', 'completed'}:
            return Response({
                'error': f'Cannot initiate payment for ride in {ride.status} status'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check if already paid
        if ride.payment_status == Ride.PAYMENT_SUCCESS:
            return Response({
                'success': True,
                'message': 'Payment already completed',
                'payment_status': 'SUCCESS',
                'ride_id': ride_id
            })

        # Set payment method to ONLINE
        ride.payment_method = Ride.PAYMENT_ONLINE
        ride.save(update_fields=['payment_method'])

        # Create or get existing payment order
        try:
            payment = Payment.objects.get(ride=ride)
            if payment.razorpay_order_id:
                razorpay_service = get_razorpay_service()
                return Response({
                    'success': True,
                    'razorpay_order_id': payment.razorpay_order_id,
                    'razorpay_key_id': razorpay_service.key_id,
                    'amount': float(payment.amount),
                    'ride_id': ride_id,
                    'qr_data': _build_canonical_upi_qr(payment.amount, ride_id)
                })
        except Payment.DoesNotExist:
            pass

        # Create new Razorpay order
        try:
            from decimal import Decimal
            razorpay_service = get_razorpay_service()
            amount = Decimal(ride.final_fare or ride.estimated_fare or 0)

            order_response = razorpay_service.create_order(
                amount_paise=int(amount * 100),  # Convert to paise
                ride_id=ride_id,
                passenger_phone=ride.passenger.phone_number,
                description=f'Ride {ride_id} payment'
            )

            if not order_response or not order_response.get('order_id'):
                return Response({
                    'error': 'Failed to create payment order'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Create payment record
            payment = Payment.objects.create(
                ride=ride,
                passenger=user,
                amount=amount,
                method='razorpay_online',
                status='pending',
                razorpay_order_id=order_response['order_id']
            )

            return Response({
                'success': True,
                'razorpay_order_id': order_response['order_id'],
                'razorpay_key_id': razorpay_service.key_id,
                'amount': float(amount),
                'ride_id': ride_id,
                'qr_data': _build_canonical_upi_qr(amount, ride_id)
            })

        except Exception as e:
            logger.error(f'Error creating Razorpay order: {e}')
            return Response({
                'error': 'Failed to create payment order'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VerifyOnlinePaymentView(APIView):
    """
    POST /api/v1/payments/verify-online/

    Verify online payment completion and credit driver wallet.
    Called after Razorpay payment callback/webhook or when passenger confirms payment.
    ATOMIC: Uses payment_processed flag to prevent double credit.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        ride_id = request.data.get('ride_id')
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_signature = request.data.get('razorpay_signature')

        if not ride_id:
            return Response(
                {'error': 'ride_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ride = Ride.objects.select_related('driver').get(id=ride_id, passenger=user)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found or you are not the passenger'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Bug 1 Fix: Ensure cancellation terminates ALL payment flows
        if ride.status == Ride.STATUS_CANCELLED:
            return Response({
                'error': 'Ride has been cancelled. Payment cannot be verified.',
                'code': 'RIDE_CANCELLED'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Validate payment method
        if (ride.payment_method or "").upper() != Ride.PAYMENT_ONLINE:
            return Response({
                'error': f'Payment method is {ride.payment_method}, not ONLINE'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check if already processed (idempotency - CRITICAL for double credit prevention)
        if ride.payment_processed:
            return Response({
                'success': True,
                'message': 'Payment already processed',
                'payment_status': 'SUCCESS',
                'ride_id': ride_id
            })

        try:
            payment = Payment.objects.get(ride=ride)
        except Payment.DoesNotExist:
            return Response({
                'error': 'No payment record found'
            }, status=status.HTTP_404_NOT_FOUND)

        # Signature verification is mandatory for API-driven payment confirmation.
        if not razorpay_payment_id or not razorpay_signature:
            return Response({
                'error': 'razorpay_payment_id and razorpay_signature are required'
            }, status=status.HTTP_400_BAD_REQUEST)

        razorpay_service = get_razorpay_service()
        is_valid = razorpay_service.verify_payment_signature(
            payment.razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature
        )
        if not is_valid:
            return Response({
                'error': 'Invalid payment signature'
            }, status=status.HTTP_400_BAD_REQUEST)

        payment.razorpay_payment_id = razorpay_payment_id

        with transaction.atomic():
            # Re-lock under transaction to prevent concurrent verification races.
            ride = Ride.objects.select_for_update().select_related('driver').get(id=ride.id, passenger=user)
            payment = Payment.objects.select_for_update().get(ride=ride)

            if ride.payment_processed:
                return Response({
                    'success': True,
                    'message': 'Payment already processed',
                    'payment_status': 'SUCCESS',
                    'ride_id': ride_id
                })

            # Mark payment as completed
            payment.status = 'paid'
            if razorpay_payment_id:
                payment.razorpay_payment_id = razorpay_payment_id
                payment.save(update_fields=['status', 'updated_at', 'razorpay_payment_id'])
            else:
                payment.save(update_fields=['status', 'updated_at'])

            # Update ride payment fields and transition through canonical states atomically.
            ride.payment_status = Ride.PAYMENT_SUCCESS
            ride.payment_received_at = timezone.now()
            ride.payment_verified_at = timezone.now()
            ride.payment_processed = True
            ride.save(update_fields=['payment_status', 'payment_received_at', 'payment_verified_at', 'payment_processed'])
            from rides.services.state_machine import RideStateMachine
            if ride.status == Ride.STATUS_PAYMENT_REQUIRED:
                RideStateMachine.transition(
                    ride_id=ride.id,
                    new_status=Ride.STATUS_PAYMENT_CONFIRMED,
                    actor_type='passenger',
                    actor_id=user.id,
                    metadata={'payment_method': Ride.PAYMENT_ONLINE},
                )
                ride.refresh_from_db(fields=['status'])
            if ride.status == Ride.STATUS_PAYMENT_CONFIRMED:
                RideStateMachine.transition(
                    ride_id=ride.id,
                    new_status=Ride.STATUS_COMPLETED,
                    actor_type='passenger',
                    actor_id=user.id,
                    metadata={'payment_method': Ride.PAYMENT_ONLINE},
                )
                ride.refresh_from_db(fields=['status'])
            RideCacheService.invalidate(ride.id, "payment_update")

            # Exactly-once wallet credit using idempotency key
            if ride.driver:
                driver = ride.driver
                driver_share = Decimal(ride.final_fare or ride.estimated_fare or 0)
                wallet_tx_key = f"ride_online_credit:{ride.id}"
                wallet_tx, created = WalletTransaction.objects.get_or_create(
                    idempotency_key=wallet_tx_key,
                    defaults={
                        'ride': ride,
                        'actor': 'driver',
                        'actor_id': str(driver.id),
                        'transaction_type': 'credit',
                        'amount': driver_share,
                        'type': 'ride_earning',
                        'status': 'completed',
                        'description': f'Online payment for ride #{ride_id}'
                    }
                )
                if created:
                    driver_wallet, _ = DriverWallet.objects.select_for_update().get_or_create(driver=driver)
                    driver_wallet.balance += driver_share
                    driver_wallet.total_earned += driver_share
                    driver_wallet.save(update_fields=['balance', 'total_earned', 'updated_at'])
                    logger.info("[PAYMENT] wallet_credit online ride=%s driver=%s amount=%s", ride.id, driver.id, driver_share)
                else:
                    logger.info("[PAYMENT] duplicate online credit suppressed ride=%s tx=%s", ride.id, wallet_tx.id)
            def _post_commit_events():
                try:
                    from asgiref.sync import async_to_sync
                    from channels.layers import get_channel_layer
                    channel_layer = get_channel_layer()

                    notification = {
                        'type': 'online_payment_confirmed',
                        'ride_id': ride_id,
                        'payment_status': 'SUCCESS',
                        'payment_method': 'ONLINE',
                        'amount': float(ride.final_fare or ride.estimated_fare or 0),
                    }

                    async_to_sync(channel_layer.group_send)(
                        f'ride_{ride_id}',
                        {
                            'type': 'payment_update',
                            'notification': {
                                **notification,
                                'message': 'Online payment successful'
                            }
                        }
                    )

                    if ride.driver:
                        async_to_sync(channel_layer.group_send)(
                            f'driver_notifications_{ride.driver.id}',
                            {
                                'type': 'payment_received',
                                'ride_id': ride_id,
                                'amount': float(ride.final_fare or ride.estimated_fare or 0),
                                'message': 'Payment received (Online)',
                                'added_to_wallet': True
                            }
                        )
                except Exception as e:
                    logger.error(f'Error broadcasting payment update: {e}')

                NotificationCenter.create_and_broadcast(
                    ride.passenger,
                    Notification.TYPE_PAYMENT_SUCCESS,
                    "Payment successful.",
                    data={"ride_id": ride.id},
                )
                if ride.driver and ride.driver.user:
                    NotificationCenter.create_and_broadcast(
                        ride.driver.user,
                        Notification.TYPE_PAYMENT_SUCCESS,
                        "Payment successful.",
                        data={"ride_id": ride.id},
                    )
            transaction.on_commit(_post_commit_events)

        logger.info("[PAYMENT] online_payment_verified ride=%s passenger=%s", ride_id, user.id)
        return Response({
            'success': True,
            'message': 'Payment successful',
            'payment_status': 'SUCCESS',
            'payment_method': 'ONLINE',
            'ride_id': ride_id,
            'amount': float(ride.final_fare or ride.estimated_fare or 0)
        })


class PaymentStatusCheckView(APIView):
    """
    GET /api/v1/payments/status/{ride_id}/

    Check current payment status for a ride.
    Used by both passenger and driver for polling fallback.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        user = request.user

        try:
            ride = Ride.objects.select_related('driver').get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verify user is passenger or driver
        is_passenger = ride.passenger == user
        is_driver = False
        if not is_passenger and ride.driver:
            try:
                driver = Driver.objects.get(user=user)
                is_driver = ride.driver.id == driver.id
            except Driver.DoesNotExist:
                pass

        if not is_passenger and not is_driver:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_403_FORBIDDEN
            )

        payment_data = {
            'ride_id': ride_id,
            'payment_status': ride.payment_status,
            'payment_method': ride.payment_method,
            'amount': float(ride.final_fare or ride.estimated_fare or 0),
            'payment_processed': ride.payment_processed,
            'payment_received_at': ride.payment_received_at.isoformat() if ride.payment_received_at else None,
        }

        # Add payment record details if available
        try:
            payment = Payment.objects.get(ride=ride)
            payment_data['razorpay_order_id'] = payment.razorpay_order_id
            payment_data['razorpay_payment_id'] = payment.razorpay_payment_id
            payment_data['payment_record_status'] = payment.status
        except Payment.DoesNotExist:
            pass

        return Response(payment_data)


class SetPaymentMethodView(APIView):
    """
    POST /api/v1/payments/set-method/

    Passenger sets their preferred payment method (cash/online) for a ride.
    This is called when the passenger chooses payment method in the UI.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        data = request.data

        ride_id = data.get('ride_id')
        payment_method = data.get('payment_method')  # 'cash' or 'online'

        if not ride_id:
            return Response(
                {'error': 'ride_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if payment_method not in ['cash', 'online']:
            return Response(
                {'error': 'payment_method must be "cash" or "online"'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'error': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verify user is the passenger
        if ride.passenger_id != user.id:
            return Response(
                {'error': 'You can only set payment method for your own rides'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Payment method can only be selected once destination is reached.
        allowed_states = {
            'reached_destination',
            'payment_required',
            'payment_confirmed',
            'completed',
        }
        if (ride.status or "").lower() not in allowed_states:
            return Response(
                {'error': f'Cannot set payment method in current ride state: {ride.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update ride payment method
        ride.payment_method = payment_method.upper()
        ride.save(update_fields=['payment_method'])

        # Transition REACHED_DESTINATION -> PAYMENT_REQUIRED once method is selected.
        if ride.status == Ride.STATUS_REACHED_DESTINATION:
            from rides.services.state_machine import RideStateMachine
            ok, msg, _ = RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_PAYMENT_REQUIRED,
                actor_type='passenger',
                actor_id=user.id,
                metadata={'payment_method': payment_method.upper()},
            )
            if not ok:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
            ride.refresh_from_db(fields=['status'])

        RideCacheService.invalidate(ride.id, "payment_update")

        logger.info('payment_method_set: ride_id=%s method=%s user=%s', ride_id, payment_method, user.id)

        return Response({
            'success': True,
            'message': f'Payment method set to {payment_method}',
            'ride_id': ride_id,
            'payment_method': ride.payment_method
        })

"""
Payments app tests
"""
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from drivers.models import Driver
from payments.models import DriverWallet, Payment, SupportTicket, WalletTransaction
from payments.services.payment_service import PaymentService
from rides.models import Ride

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class PaymentFlowTests(APITestCase):
    """Covers webhook processing, wallet updates, and failed payment retries."""

    def setUp(self):
        self.passenger = User.objects.create_user(
            phone_number='9000000001',
            name='Passenger Test',
            password='test123'
        )
        self.driver_user = User.objects.create_user(
            phone_number='9000000002',
            name='Driver Test',
            password='test123'
        )
        self.driver = Driver.objects.create(
            user=self.driver_user,
            name='Driver Test',
            vehicle_type='mini',
            is_approved=True,
            approval_status=Driver.APPROVAL_APPROVED,
        )
        self.ride = Ride.objects.create(
            passenger=self.passenger,
            driver=self.driver,
            pickup_lat=12.9,
            pickup_lng=77.6,
            drop_lat=12.95,
            drop_lng=77.65,
            final_fare=100,
            driver_share=Decimal('80.00'),
            status='payment_required',
        )

    def _post_webhook(self, payload: dict):
        return self.client.post(
            '/api/v1/payments/webhook/',
            data=payload,
            format='json',
            HTTP_X_RAZORPAY_SIGNATURE='test-signature',
        )

    def _post_api_webhook_alias(self, payload: dict):
        return self.client.post(
            '/api/payments/razorpay/webhook/',
            data=payload,
            format='json',
            HTTP_X_RAZORPAY_SIGNATURE='test-signature',
        )

    @patch('payments.views.get_razorpay_service')
    def test_webhook_captured_marks_paid_and_credits_driver_wallet(self, mock_get_service):
        mock_service = Mock()
        mock_service.verify_webhook_signature.return_value = True
        mock_get_service.return_value = mock_service

        payment = Payment.objects.create(
            ride=self.ride,
            passenger=self.passenger,
            amount=Decimal('100.00'),
            method='razorpay_online',
            status='pending',
            razorpay_order_id='order_123',
        )

        response = self._post_webhook({
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_123',
                        'order_id': 'order_123',
                        'status': 'captured',
                    }
                }
            }
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.ride.refresh_from_db()

        self.assertEqual(payment.status, 'paid')
        self.assertEqual(payment.razorpay_payment_id, 'pay_123')
        self.assertEqual(self.ride.status, 'payment_confirmed')

        wallet = DriverWallet.objects.get(driver=self.driver)
        self.assertEqual(wallet.balance, Decimal('80.00'))
        self.assertTrue(
            WalletTransaction.objects.filter(
                ride=self.ride,
                actor='driver',
                type='ride_earning',
                status='completed',
            ).exists()
        )

    @patch('payments.views.get_razorpay_service')
    def test_webhook_failed_marks_payment_failed(self, mock_get_service):
        mock_service = Mock()
        mock_service.verify_webhook_signature.return_value = True
        mock_get_service.return_value = mock_service

        payment = Payment.objects.create(
            ride=self.ride,
            passenger=self.passenger,
            amount=Decimal('100.00'),
            method='razorpay_online',
            status='pending',
            razorpay_order_id='order_456',
        )

        response = self._post_webhook({
            'event': 'payment.failed',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_456',
                        'order_id': 'order_456',
                        'status': 'failed',
                    }
                }
            }
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.ride.refresh_from_db()

        self.assertEqual(payment.status, 'failed')
        self.assertEqual(self.ride.status, 'payment_required')
        self.assertFalse(DriverWallet.objects.filter(driver=self.driver).exists())
        self.assertTrue(
            SupportTicket.objects.filter(
                ride=self.ride,
                driver=self.driver,
                issue_type='payment_failed',
            ).exists()
        )

    @patch('payments.views.get_razorpay_service')
    def test_webhook_alias_under_api_processes_events(self, mock_get_service):
        mock_service = Mock()
        mock_service.verify_webhook_signature.return_value = True
        mock_get_service.return_value = mock_service

        payment = Payment.objects.create(
            ride=self.ride,
            passenger=self.passenger,
            amount=Decimal('100.00'),
            method='razorpay_online',
            status='pending',
            razorpay_order_id='order_alias',
        )

        response = self._post_api_webhook_alias({
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': 'pay_alias',
                        'order_id': 'order_alias',
                        'status': 'captured',
                    }
                }
            }
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')

    @patch('payments.services.payment_service.get_razorpay_service')
    def test_retry_failed_online_payment_resets_to_pending(self, mock_get_service):
        mock_service = Mock()
        mock_service.create_order.return_value = {
            'order_id': 'order_new',
            'amount': 10000,
            'currency': 'INR',
        }
        mock_get_service.return_value = mock_service

        Payment.objects.create(
            ride=self.ride,
            passenger=self.passenger,
            amount=Decimal('100.00'),
            method='razorpay_online',
            status='failed',
            razorpay_order_id='order_old',
            razorpay_payment_id='pay_old',
        )

        payment = PaymentService.create_payment_intent(self.ride.id, method='razorpay_online')
        payment.refresh_from_db()

        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.razorpay_order_id, 'order_new')
        self.assertIsNone(payment.razorpay_payment_id)

    def test_retry_failed_cash_payment_resets_to_pending_and_clears_gateway_ids(self):
        Payment.objects.create(
            ride=self.ride,
            passenger=self.passenger,
            amount=Decimal('100.00'),
            method='razorpay_online',
            status='failed',
            razorpay_order_id='order_stale',
            razorpay_payment_id='pay_stale',
        )

        payment = PaymentService.create_payment_intent(self.ride.id, method='cash')
        payment.refresh_from_db()

        self.assertEqual(payment.method, 'cash')
        self.assertEqual(payment.status, 'pending')
        self.assertIsNone(payment.razorpay_order_id)
        self.assertIsNone(payment.razorpay_payment_id)

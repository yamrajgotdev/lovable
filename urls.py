"""
Payment API URLs
"""
from django.urls import path
from payments.views import (
    CreateOrderView, WebhookView, ConfirmCashView, PaymentStatusView,
    DriverWalletView, DriverTransactionsView, PassengerTransactionsView,
    SupportTicketCreateView, PaymentReconcileView, CheckPaymentBeforeCompleteView,
    DriverQRCodeView,
    # NEW: Payment confirmation and online payment views
    ConfirmCashCollectionView, InitiateOnlinePaymentView, VerifyOnlinePaymentView,
    PaymentStatusCheckView
)

urlpatterns = [
    # Payment endpoints
    path('create-order/', CreateOrderView.as_view(), name='create-order'),
    path('webhook/', WebhookView.as_view(), name='webhook'),
    path('confirm-cash/', ConfirmCashView.as_view(), name='confirm-cash'),
    path('ride/<int:ride_id>/', PaymentStatusView.as_view(), name='payment-status'),
    path('reconcile/', PaymentReconcileView.as_view(), name='reconcile'),

    # Driver payment check and QR code
    path('ride/<int:ride_id>/check-before-complete/', CheckPaymentBeforeCompleteView.as_view(), name='check-payment-before-complete'),
    path('ride/<int:ride_id>/qr-code/', DriverQRCodeView.as_view(), name='driver-qr-code'),

    # NEW: Payment confirmation endpoints (Task 1 & 2)
    path('confirm-cash-collection/', ConfirmCashCollectionView.as_view(), name='confirm-cash-collection'),
    path('initiate-online/', InitiateOnlinePaymentView.as_view(), name='initiate-online-payment'),
    path('verify-online/', VerifyOnlinePaymentView.as_view(), name='verify-online-payment'),
    path('status/<int:ride_id>/', PaymentStatusCheckView.as_view(), name='payment-status-check'),

    # Driver wallet endpoints
    path('wallet/', DriverWalletView.as_view(), name='driver-wallet'),
    path('wallet/transactions/', DriverTransactionsView.as_view(), name='driver-transactions'),

    # Passenger wallet history
    path('passenger/transactions/', PassengerTransactionsView.as_view(), name='passenger-transactions'),

    # Support tickets
    path('support-ticket/', SupportTicketCreateView.as_view(), name='support-ticket'),
]

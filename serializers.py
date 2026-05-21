"""
Payment API Serializers
"""
from rest_framework import serializers
from payments.models import Payment, DriverWallet, WalletTransaction, SupportTicket


class PaymentSerializer(serializers.ModelSerializer):
    """Full payment object serialization."""
    ride_id = serializers.IntegerField(source='ride.id', read_only=True)
    passenger_phone = serializers.CharField(source='passenger.phone_number', read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'ride_id', 'amount', 'method', 'status', 'razorpay_order_id', 'razorpay_payment_id', 'created_at', 'updated_at', 'passenger_phone']
        read_only_fields = ['id', 'razorpay_order_id', 'razorpay_payment_id', 'created_at', 'updated_at']


class PaymentOrderSerializer(serializers.Serializer):
    """Response when creating a Razorpay order."""
    order_id = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    status = serializers.CharField()
    method = serializers.CharField()
    payment_method = serializers.CharField()


class CreateOrderSerializer(serializers.Serializer):
    """Request to create a payment order."""
    ride_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=['cash', 'razorpay_online'])


class ConfirmCashSerializer(serializers.Serializer):
    """Request to confirm cash payment collection."""
    ride_id = serializers.IntegerField()


class WalletTransactionSerializer(serializers.ModelSerializer):
    """Wallet transaction serialization."""
    ride_id = serializers.IntegerField(source='ride.id', allow_null=True, read_only=True)

    class Meta:
        model = WalletTransaction
        fields = ['id', 'ride_id', 'amount', 'type', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']


class WalletSerializer(serializers.ModelSerializer):
    """Driver wallet serialization."""
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = DriverWallet
        fields = ['balance', 'total_earned', 'total_withdrawn', 'updated_at', 'recent_transactions']
        read_only_fields = ['balance', 'total_earned', 'total_withdrawn', 'updated_at']

    def get_recent_transactions(self, obj):
        """Get recent 10 transactions."""
        transactions = WalletTransaction.objects.filter(
            actor='driver',
            actor_id=str(obj.driver.id),
        ).order_by('-created_at')[:10]
        return WalletTransactionSerializer(transactions, many=True).data


class SupportTicketSerializer(serializers.ModelSerializer):
    """Support ticket serialization."""
    ride_id = serializers.IntegerField(source='ride.id', read_only=True)
    driver_name = serializers.CharField(source='driver.name', read_only=True)

    class Meta:
        model = SupportTicket
        fields = ['id', 'ride_id', 'driver_name', 'issue_type', 'description', 'status', 'resolution_notes', 'created_at', 'resolved_at']
        read_only_fields = ['id', 'created_at', 'resolved_at']


class CreateSupportTicketSerializer(serializers.Serializer):
    """Request to create a support ticket."""
    ride_id = serializers.IntegerField()
    issue_type = serializers.ChoiceField(choices=['payment_not_received', 'passenger_refused_cash', 'payment_failed', 'other'])
    description = serializers.CharField(max_length=1000)

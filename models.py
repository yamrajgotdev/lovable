from django.db import models
from django.utils import timezone
from django.db.models import Q
from drivers.models import Driver
from rides.models import Ride
from authsystem.models import User


class Payment(models.Model):
    """
    Tracks all payment transactions for rides.
    Supports both cash and online (Razorpay) payments.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('cash_collected', 'Cash Collected'),
    ]

    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('razorpay_online', 'Razorpay Online'),
    ]

    ride = models.OneToOneField(Ride, on_delete=models.CASCADE, related_name='payment')
    passenger = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='razorpay_online')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Razorpay tracking
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    is_quarantined = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ride', 'status']),
            models.Index(fields=['razorpay_order_id']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['status']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['razorpay_order_id'],
                condition=Q(razorpay_order_id__isnull=False) & ~Q(razorpay_order_id=''),
                name='payments_unique_nonblank_razorpay_order_id',
            ),
            models.UniqueConstraint(
                fields=['razorpay_payment_id'],
                condition=Q(razorpay_payment_id__isnull=False) & ~Q(razorpay_payment_id=''),
                name='payments_unique_nonblank_razorpay_payment_id',
            ),
        ]

    def __str__(self):
        return f"Payment {self.id} - Ride {self.ride_id} - {self.status}"

    def is_pending(self):
        return self.status == 'pending'

    def mark_paid(self):
        self.status = 'paid'
        self.updated_at = timezone.now()
        self.save(update_fields=['status', 'updated_at'])

    def mark_failed(self):
        self.status = 'failed'
        self.updated_at = timezone.now()
        self.save(update_fields=['status', 'updated_at'])

    def mark_cash_collected(self):
        self.status = 'cash_collected'
        self.updated_at = timezone.now()
        self.save(update_fields=['status', 'updated_at'])


class PassengerWallet(models.Model):
    """
    Tracks passenger's wallet balance.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='passenger_wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'passenger_wallets'

    def __str__(self):
        return f"Wallet {self.user.phone_number} - Balance: ₹{self.balance}"

    def credit(self, amount):
        self.balance += amount
        self.save(update_fields=['balance', 'updated_at'])
        return self.balance

    def debit(self, amount):
        if self.balance >= amount:
            self.balance -= amount
            self.save(update_fields=['balance', 'updated_at'])
            return True
        return False


class DriverWallet(models.Model):
    """
    Tracks driver's earnings and wallet balance.
    One wallet per driver.
    """
    driver = models.OneToOneField(Driver, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_earned = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_withdrawn = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'driver_wallets'

    def __str__(self):
        return f"Wallet {self.driver.name} - Balance: ₹{self.balance}"

    def credit(self, amount):
        """Credit wallet with amount (used in atomic transaction)."""
        self.balance += amount
        self.total_earned += amount
        self.save(update_fields=['balance', 'total_earned', 'updated_at'])
        return self.balance

    def debit(self, amount):
        """Debit wallet (for reversals). Returns True if successful."""
        if self.balance >= amount:
            self.balance -= amount
            self.save(update_fields=['balance', 'updated_at'])
            return True
        return False


class WalletTransaction(models.Model):
    """
    Double-entry ledger for all wallet transactions.
    Tracks passenger debits, driver credits, and platform commission.
    """
    ACTOR_CHOICES = [
        ('passenger', 'Passenger'),
        ('driver', 'Driver'),
        ('platform', 'Platform'),
    ]

    TRANSACTION_TYPE_CHOICES = [
        ('debit', 'Debit'),
        ('credit', 'Credit'),
    ]

    TYPE_CHOICES = [
        ('ride_earning', 'Ride Earning'),
        ('ride_payment', 'Ride Payment'),
        ('platform_commission', 'Platform Commission'),
        ('adjustment', 'Adjustment'),
        ('payout', 'Payout'),
        ('payout_reversal', 'Payout Reversal'),
        ('refund', 'Refund'),
    ]

    STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
        ('reversed', 'Reversed'),
    ]

    id = models.BigAutoField(primary_key=True)
    ride = models.ForeignKey(Ride, on_delete=models.SET_NULL, null=True, blank=True, related_name='wallet_transactions')
    actor = models.CharField(max_length=20, choices=ACTOR_CHOICES, default='platform')
    actor_id = models.CharField(max_length=50, default='0', help_text="User ID or Driver ID")
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES, default='credit')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_quarantined = models.BooleanField(default=False, db_index=True)
    type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='adjustment')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True,
                                       help_text="Prevents duplicate transactions")
    related_transaction = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='linked_transactions',
                                            help_text="Links debit to credit in double-entry")
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'wallet_transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ride', 'actor', 'created_at']),
            models.Index(fields=['actor', 'actor_id', 'created_at']),
            models.Index(fields=['type', 'status', 'created_at']),
            models.Index(fields=['idempotency_key']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['ride', 'status']),
        ]

    def __str__(self):
        return f"TX {self.id} - {self.actor} {self.transaction_type} ₹{self.amount} - {self.status}"

    def mark_completed(self):
        from django.utils import timezone
        self.status = 'completed'
        self.processed_at = timezone.now()
        self.save(update_fields=['status', 'processed_at', 'updated_at'])

    def mark_failed(self, reason=None):
        self.status = 'failed'
        if reason:
            self.metadata['failure_reason'] = reason
        self.save(update_fields=['status', 'metadata', 'updated_at'])

    def reverse(self, reason=None):
        self.status = 'reversed'
        if reason:
            self.metadata['reversal_reason'] = reason
        self.save(update_fields=['status', 'metadata', 'updated_at'])


class SupportTicket(models.Model):
    """
    Support tickets for all users (drivers and passengers).
    Extended to support comprehensive issue reporting.
    """
    # User types
    USER_TYPE_DRIVER = 'driver'
    USER_TYPE_PASSENGER = 'passenger'
    USER_TYPE_CHOICES = [
        (USER_TYPE_DRIVER, 'Driver'),
        (USER_TYPE_PASSENGER, 'Passenger'),
    ]

    # Ticket status (extended)
    STATUS_PENDING = 'pending'
    STATUS_OPEN = 'open'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_RESOLVED = 'resolved'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_OPEN, 'Open'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_RESOLVED, 'Resolved'),
        (STATUS_CLOSED, 'Closed'),
    ]

    # Extended issue types for both drivers and passengers
    # Driver topics
    TOPIC_REPORT_PASSENGER = 'report_passenger'
    TOPIC_PASSENGER_REFUSED_CASH = 'passenger_refused_cash'
    TOPIC_MONEY_NOT_RECEIVED = 'money_not_received'
    TOPIC_PASSENGER_ABUSE = 'passenger_abuse'
    # Passenger topics
    TOPIC_REPORT_DRIVER = 'report_driver'
    TOPIC_OVERCHARGED = 'overcharged'
    TOPIC_DRIVER_ABUSE = 'driver_abuse'
    # Common topics
    TOPIC_PAYMENT_NOT_RECEIVED = 'payment_not_received'
    TOPIC_PAYMENT_FAILED = 'payment_failed'
    TOPIC_GLITCH = 'glitch'
    TOPIC_RIDE_ISSUE = 'ride_issue'
    TOPIC_APP_NOT_WORKING = 'app_not_working'
    TOPIC_ACCOUNT_ISSUE = 'account_issue'
    TOPIC_OTHER = 'other'

    ISSUE_TYPES = [
        # Driver specific
        ('report_passenger', 'Report Passenger'),
        ('passenger_refused_cash', 'Passenger Refused Cash'),
        ('money_not_received', 'Money Not Received'),
        ('passenger_abuse', 'Passenger Abuse/Misbehavior'),
        # Passenger specific
        ('report_driver', 'Report Driver'),
        ('overcharged', 'Overcharged'),
        ('driver_abuse', 'Driver Abuse/Misbehavior'),
        # Common
        ('payment_not_received', 'Payment Not Received'),
        ('payment_failed', 'Payment Failed'),
        ('glitch', 'Technical Glitch/App Bug'),
        ('ride_issue', 'Ride Issue'),
        ('app_not_working', 'App Not Working'),
        ('account_issue', 'Account Issue'),
        ('other', 'Other'),
    ]

    # Priority levels
    PRIORITY_LOW = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH = 'high'
    PRIORITY_URGENT = 'urgent'
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, 'Low'),
        (PRIORITY_MEDIUM, 'Medium'),
        (PRIORITY_HIGH, 'High'),
        (PRIORITY_URGENT, 'Urgent'),
    ]

    # Auto-set priority based on topic
    HIGH_PRIORITY_TOPICS = [
        TOPIC_PASSENGER_ABUSE, TOPIC_DRIVER_ABUSE,
        TOPIC_PAYMENT_NOT_RECEIVED, TOPIC_MONEY_NOT_RECEIVED,
        TOPIC_OVERCHARGED, TOPIC_PASSENGER_REFUSED_CASH
    ]

    # Relations
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='support_tickets', null=True, blank=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default=USER_TYPE_PASSENGER)
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='legacy_support_tickets', null=True, blank=True)
    
    # Optional ride reference
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='support_tickets', null=True, blank=True)
    
    # Issue details
    issue_type = models.CharField(max_length=30, choices=ISSUE_TYPES)
    topic = models.CharField(max_length=30, choices=ISSUE_TYPES, default='other')  # New field alias
    description = models.TextField()
    
    # Status and priority
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_MEDIUM)
    
    # Admin response (new fields)
    admin_response = models.TextField(blank=True, null=True)
    responded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ticket_responses')
    responded_at = models.DateTimeField(null=True, blank=True)
    
    # Legacy field for backward compatibility
    resolution_notes = models.TextField(blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'support_tickets'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['driver', 'status', 'created_at']),
            models.Index(fields=['user_type', 'status']),
            models.Index(fields=['issue_type', 'status']),
            models.Index(fields=['priority', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        # Sync topic with issue_type for compatibility
        if self.issue_type:
            self.topic = self.issue_type
        # Auto-set priority based on topic
        if self.issue_type in self.HIGH_PRIORITY_TOPICS:
            self.priority = self.PRIORITY_HIGH
        super().save(*args, **kwargs)

    def __str__(self):
        name = self.user.name if self.user else (self.driver.name if self.driver else 'Unknown')
        return f"[{self.user_type.upper()}] {self.get_issue_type_display()} - {name}"

    def to_dict(self):
        """Serialize ticket for API responses."""
        return {
            'id': self.id,
            'user_type': self.user_type,
            'user_name': self.user.name if self.user else (self.driver.name if self.driver else 'Unknown'),
            'topic': self.topic,
            'issue_type': self.issue_type,
            'topic_display': self.get_issue_type_display(),
            'description': self.description,
            'status': self.status,
            'status_display': self.get_status_display(),
            'priority': self.priority,
            'priority_display': self.get_priority_display(),
            'ride_id': self.ride_id,
            'ride_info': {
                'id': self.ride.id,
                'pickup': self.ride.pickup_address,
                'drop': self.ride.drop_address,
                'date': self.ride.requested_at.isoformat() if self.ride.requested_at else None,
            } if self.ride else None,
            'admin_response': self.admin_response,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class QRCodePayment(models.Model):
    """
    QR code payment records for driver-to-passenger payments.
    Driver can generate/show QR for passenger to scan and pay.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('scanned', 'Scanned'),
        ('paid', 'Paid'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ]

    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='qr_payments')
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='created_qr_codes')
    passenger = models.ForeignKey(User, on_delete=models.CASCADE, related_name='qr_payments')
    
    # QR Code data
    qr_code_data = models.TextField(help_text="QR code content (payment URL or UPI string)")
    qr_code_image_url = models.CharField(max_length=500, blank=True, help_text="URL to QR code image if stored")
    
    # Payment details
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    upi_id = models.CharField(max_length=100, blank=True, help_text="Driver's UPI ID for payment")
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    scanned_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    
    # Razorpay tracking (if payment goes through our system)
    razorpay_payment_link_id = models.CharField(max_length=100, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = 'qr_code_payments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ride', 'status']),
            models.Index(fields=['driver', 'status']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"QR Payment {self.id} - Ride {self.ride_id} - {self.status}"

    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def mark_scanned(self):
        from django.utils import timezone
        self.status = 'scanned'
        self.scanned_at = timezone.now()
        self.save(update_fields=['status', 'scanned_at'])

    def mark_paid(self):
        from django.utils import timezone
        self.status = 'paid'
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'paid_at'])

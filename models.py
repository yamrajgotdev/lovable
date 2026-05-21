from django.db import models
from authsystem.models import User


class DriverLocation(models.Model):
    """
    Stores the latest location for each driver.
    Uses OneToOneField to ensure only one record per driver (updated in-place).
    """
    driver = models.OneToOneField(
        'drivers.Driver',  # String reference to avoid circular import
        on_delete=models.CASCADE,
        related_name='location',
        db_index=True
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    # Optional fields for richer tracking
    heading = models.FloatField(null=True, blank=True)  # Direction in degrees (0-360)
    speed = models.FloatField(null=True, blank=True)    # Speed in km/h
    accuracy = models.FloatField(null=True, blank=True)  # GPS accuracy in meters
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        db_table = 'driver_locations'
        indexes = [
            # Composite index for efficient geospatial queries
            models.Index(fields=['latitude', 'longitude']),
            # Index for finding stale locations (cleanup/maintenance)
            models.Index(fields=['updated_at']),
        ]

    def __str__(self):
        return f"{self.driver.name} @ ({self.latitude:.4f}, {self.longitude:.4f})"


class Driver(models.Model):
    VEHICLE_TYPES = [
        ('mini', 'Mini'),
        ('sedan', 'Sedan'),
        ('suv', 'SUV'),
        ('auto', 'Auto'),
        ('bike', 'Bike'),
        ('erickshaw', 'E-Rickshaw'),
    ]

    APPROVAL_PENDING = 'pending'
    APPROVAL_APPROVED = 'approved'
    APPROVAL_REJECTED = 'rejected'
    APPROVAL_STATUS_CHOICES = [
        (APPROVAL_PENDING, 'Pending'),
        (APPROVAL_APPROVED, 'Approved'),
        (APPROVAL_REJECTED, 'Rejected'),
    ]

    # Driver operational status
    STATUS_OFFLINE = 'OFFLINE'
    STATUS_TEMP_OFFLINE = 'TEMP_OFFLINE'
    STATUS_ONLINE = 'ONLINE'
    STATUS_CHOICES = [
        (STATUS_OFFLINE, 'Offline'),
        (STATUS_TEMP_OFFLINE, 'Temp Offline'),
        (STATUS_ONLINE, 'Online'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='driver_profile')
    name = models.CharField(max_length=100)

    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPES, default='mini')
    vehicle_number = models.CharField(max_length=20, blank=True)
    license_number = models.CharField(max_length=50, blank=True)

    aadhaar_number = models.CharField(max_length=14, blank=True)
    pan_number = models.CharField(max_length=10, blank=True)

    profile_photo = models.ImageField(upload_to='drivers/photos/', blank=True, null=True)
    license_photo = models.ImageField(upload_to='drivers/licenses/', blank=True, null=True)
    rc_photo = models.ImageField(upload_to='drivers/rc/', blank=True, null=True)
    aadhaar_photo = models.ImageField(upload_to='drivers/aadhaar/', blank=True, null=True)
    pan_photo = models.ImageField(upload_to='drivers/pan/', blank=True, null=True)

    is_approved = models.BooleanField(default=False)
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default=APPROVAL_PENDING, db_index=True)
    verification_notes = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    # Operational status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OFFLINE, db_index=True)
    is_online = models.BooleanField(default=False)  # Legacy field - maintained for compatibility
    temp_offline_since = models.DateTimeField(null=True, blank=True)  # When location stopped
    temp_offline_started_at = models.DateTimeField(null=True, blank=True)  # When temp offline period started
    temp_offline_notification_sent = models.BooleanField(default=False)  # Whether notification was sent
    last_location_update = models.DateTimeField(null=True, blank=True)

    is_suspended = models.BooleanField(default=False, db_index=True)
    suspension_reason = models.TextField(blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    rating = models.FloatField(default=5.0)
    total_rides = models.IntegerField(default=0)

    current_lat = models.FloatField(null=True, blank=True)
    current_lng = models.FloatField(null=True, blank=True)

    # Safety tracking fields
    consecutive_location_failures = models.IntegerField(default=0)  # Count of missed location updates
    location_permission_granted = models.BooleanField(default=False)  # Frontend confirms location access

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'drivers'
        indexes = [
            models.Index(fields=['is_online', 'is_approved']),
            models.Index(fields=['vehicle_type']),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            old_instance = Driver.objects.get(pk=self.pk)
            if self.is_approved and not old_instance.is_approved:
                from django.utils import timezone
                self.approved_at = timezone.now()
                self.approval_status = self.APPROVAL_APPROVED
            elif not self.is_approved and old_instance.is_approved:
                self.approval_status = self.APPROVAL_PENDING
                self.approved_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.vehicle_type}"

    def can_go_online(self):
        return self.is_approved and all([
            self.license_number,
            self.vehicle_number,
            self.aadhaar_number
        ])

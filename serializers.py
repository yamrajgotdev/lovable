from rest_framework import serializers
from .models import Driver, DriverLocation


class DriverLocationSerializer(serializers.ModelSerializer):
    """Serializer for driver location updates."""
    class Meta:
        model = DriverLocation
        fields = ['latitude', 'longitude', 'heading', 'speed', 'accuracy', 'updated_at']
        read_only_fields = ['updated_at']


class DriverSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(source='user.phone_number', read_only=True)
    
    class Meta:
        model = Driver
        fields = [
            'id', 'name', 'phone_number', 'vehicle_type', 'vehicle_number', 
            'is_approved', 'is_online', 'rating', 'total_rides',
            'current_lat', 'current_lng', 'profile_photo'
        ]
        read_only_fields = ['id', 'is_approved', 'rating', 'total_rides', 'phone_number']


class DriverRegistrationSerializer(serializers.ModelSerializer):
    phone_number = serializers.CharField(write_only=True)
    name = serializers.CharField(max_length=100)
    vehicle_type = serializers.ChoiceField(choices=Driver.VEHICLE_TYPES)
    vehicle_number = serializers.CharField(max_length=20, required=False)
    license_number = serializers.CharField(max_length=50, required=False)
    aadhaar_number = serializers.CharField(max_length=14, required=False)
    pan_number = serializers.CharField(max_length=10, required=False)

    class Meta:
        model = Driver
        fields = [
            'phone_number', 'name', 'vehicle_type', 'vehicle_number',
            'license_number', 'aadhaar_number', 'pan_number'
        ]

    def create(self, validated_data):
        from authsystem.models import User
        
        phone_number = validated_data.pop('phone_number')
        user, _ = User.objects.get_or_create(
            phone_number=phone_number,
            defaults={'is_driver': True}
        )
        user.is_driver = True
        user.save()

        driver, created = Driver.objects.get_or_create(
            user=user,
            defaults=validated_data
        )
        
        if not created:
            for key, value in validated_data.items():
                setattr(driver, key, value)
            driver.save()
        
        return driver


class NearbyDriverSerializer(serializers.ModelSerializer):
    distance_km = serializers.FloatField(read_only=True)
    heading = serializers.SerializerMethodField()

    class Meta:
        model = Driver
        fields = [
            'id', 'name', 'vehicle_type', 'vehicle_number',
            'rating', 'current_lat', 'current_lng', 'distance_km', 'heading'
        ]

    def get_heading(self, obj):
        location = getattr(obj, 'location', None)
        if not location or location.heading is None:
            return 0
        return float(location.heading)

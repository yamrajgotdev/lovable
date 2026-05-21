from math import atan2, cos, radians, sin, sqrt
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone


def haversine_distance(lat1, lon1, lat2, lon2):
    earth_radius_km = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return earth_radius_km * c


class NearbyDriversConsumer(AsyncJsonWebsocketConsumer):
    group_name = 'nearby_drivers_stream'

    async def connect(self):
        params = parse_qs(self.scope.get('query_string', b'').decode())
        try:
            self.center_lat = float(params.get('lat', [0])[0])
            self.center_lng = float(params.get('lng', [0])[0])
        except (TypeError, ValueError):
            await self.close(code=4400)
            return

        self.vehicle = (params.get('vehicle', [''])[0] or '').strip()
        try:
            self.radius_km = float(params.get('radius', ['5'])[0] or 5)
        except (TypeError, ValueError):
            self.radius_km = 5.0

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({
            'type': 'nearby_drivers_snapshot',
            'drivers': await self.get_initial_snapshot(),
        })

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get('type') == 'subscribe':
            try:
                self.center_lat = float(content.get('lat', self.center_lat))
                self.center_lng = float(content.get('lng', self.center_lng))
                self.radius_km = float(content.get('radius', self.radius_km))
            except (TypeError, ValueError):
                return

            self.vehicle = (content.get('vehicle') or self.vehicle or '').strip()
            await self.send_json({
                'type': 'nearby_drivers_snapshot',
                'drivers': await self.get_initial_snapshot(),
            })

    async def nearby_driver_event(self, event):
        driver = event.get('driver') or {}
        driver_id = driver.get('id')
        if not driver_id:
            return

        if event.get('event') == 'offline':
            await self.send_json({
                'type': 'nearby_driver_remove',
                'driverId': str(driver_id),
            })
            return

        if self._matches_filters(driver):
            await self.send_json({
                'type': 'nearby_driver_upsert',
                'driver': driver,
            })
        else:
            await self.send_json({
                'type': 'nearby_driver_remove',
                'driverId': str(driver_id),
            })

    def _matches_filters(self, driver):
        if self.vehicle and driver.get('vehicle') != self.vehicle:
            return False

        lat = driver.get('lat')
        lng = driver.get('lng')
        if lat is None or lng is None:
            return False

        return haversine_distance(self.center_lat, self.center_lng, float(lat), float(lng)) <= self.radius_km

    @database_sync_to_async
    def get_initial_snapshot(self):
        from drivers.models import Driver
        from drivers.state_machine import DriverStateMachine

        drivers = Driver.objects.filter(
            is_online=True,
            is_approved=True,
            current_lat__isnull=False,
            current_lng__isnull=False,
        ).select_related('location').only(
            'id',
            'current_lat',
            'current_lng',
            'vehicle_type',
            'location__heading',
        )

        if self.vehicle:
            drivers = drivers.filter(vehicle_type=self.vehicle)

        payload = []
        for driver in drivers:
            if haversine_distance(self.center_lat, self.center_lng, driver.current_lat, driver.current_lng) > self.radius_km:
                continue
            
            # Fetch real-time status from state machine
            state = DriverStateMachine.get_state(driver.id)
            
            payload.append({
                'id': str(driver.id),
                'lat': driver.current_lat,
                'lng': driver.current_lng,
                'vehicle': driver.vehicle_type,
                'heading': float(getattr(getattr(driver, 'location', None), 'heading', 0) or 0),
                'status': state.value.upper(),
            })
        return payload


class DriverLocationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated or not self.user.is_driver:
            await self.close()
            return

        self.driver = await self.get_driver()
        if not self.driver:
            await self.close()
            return

        await self.accept()

    async def receive_json(self, content, **kwargs):
        lat = content.get('latitude')
        lng = content.get('longitude')
        heading = content.get('heading', 0)

        if lat is None or lng is None:
            return

        driver = await self.update_driver_location(lat, lng, heading)
        if not driver:
            return

        await self.channel_layer.group_send(
            f'driver_{driver["id"]}',
            {
                'type': 'location_update',
                'latitude': driver['lat'],
                'longitude': driver['lng'],
                'heading': driver['heading'],
                'driver_id': driver['id'],
            },
        )
        await self.channel_layer.group_send(
            NearbyDriversConsumer.group_name,
            {
                'type': 'nearby_driver_event',
                'event': 'upsert',
                'driver': {
                    'id': str(driver['id']),
                    'lat': driver['lat'],
                    'lng': driver['lng'],
                    'vehicle': driver['vehicle'],
                    'heading': driver['heading'],
                    'status': driver.get('status', 'ONLINE'),
                },
            },
        )

    @database_sync_to_async
    def get_driver(self):
        try:
            return self.user.driver_profile
        except Exception:
            return None

    @database_sync_to_async
    def update_driver_location(self, lat, lng, heading):
        from drivers.models import Driver, DriverLocation
        from drivers.state_machine import DriverStateMachine
        from rideapp.redis_utils import set_driver_location_ttl, set_driver_online_status
        from django.utils import timezone

        driver = self.user.driver_profile
        driver.current_lat = float(lat)
        driver.current_lng = float(lng)
        driver.last_location_update = timezone.now()

        # RESTORE ONLINE STATUS if driver was temp_offline or offline but is sending location
        if driver.status in [Driver.STATUS_TEMP_OFFLINE, Driver.STATUS_OFFLINE]:
            driver.status = Driver.STATUS_ONLINE
            driver.is_online = True

        driver.save(update_fields=['current_lat', 'current_lng', 'last_location_update', 'status', 'is_online'])

        DriverLocation.objects.update_or_create(
            driver=driver,
            defaults={
                'latitude': driver.current_lat,
                'longitude': driver.current_lng,
                'heading': float(heading or 0),
            },
        )
        # Update Redis tracking (both legacy and new state machine)
        set_driver_location_ttl(driver.id, driver.current_lat, driver.current_lng)
        set_driver_online_status(driver.id, True)

        # CRITICAL: Update DriverStateMachine heartbeat so dispatch service sees them as fresh
        DriverStateMachine.record_heartbeat(driver.id, {'lat': driver.current_lat, 'lng': driver.current_lng})

        return {
            'id': driver.id,
            'lat': driver.current_lat,
            'lng': driver.current_lng,
            'vehicle': driver.vehicle_type,
            'heading': float(heading or 0),
            'status': DriverStateMachine.get_state(driver.id).value.upper(),
        }

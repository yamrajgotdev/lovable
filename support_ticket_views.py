"""
Support Ticket API Views
Endpoints for creating and managing support tickets.
"""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from payments.models import SupportTicket
from .models import Ride

logger = logging.getLogger('rides4u')


# Topics that require a ride to be selected
RIDE_RELATED_TOPICS = [
    'report_passenger',
    'passenger_refused_cash',
    'money_not_received',
    'passenger_abuse',
    'report_driver',
    'overcharged',
    'driver_abuse',
    'payment_not_received',
    'payment_failed',
    'ride_issue',
]


class SupportTicketListView(APIView):
    """
    GET /api/support/tickets/
    List user's support tickets.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        tickets = SupportTicket.objects.filter(user=user).order_by('-created_at')
        
        return Response({
            'success': True,
            'tickets': [ticket.to_dict() for ticket in tickets]
        })


class CreateSupportTicketView(APIView):
    """
    POST /api/support/tickets/create/
    Create a new support ticket.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        topic = request.data.get('topic')
        description = request.data.get('description', '').strip()
        ride_id = request.data.get('ride_id')
        user_type = request.data.get('user_type', 'passenger')
        
        # Validate required fields
        if not topic:
            return Response(
                {'success': False, 'message': 'Topic is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not description:
            return Response(
                {'success': False, 'message': 'Description is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate topic is valid
        valid_topics = [t[0] for t in SupportTicket.ISSUE_TYPES]
        if topic not in valid_topics:
            return Response(
                {'success': False, 'message': 'Invalid topic'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if topic requires a ride
        ride = None
        if topic in RIDE_RELATED_TOPICS:
            if not ride_id:
                return Response(
                    {'success': False, 'message': 'Please select a ride for this issue'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                ride = Ride.objects.get(id=ride_id)
                # Verify the ride belongs to this user
                if ride.passenger_id != user.id and (not ride.driver or ride.driver.user_id != user.id):
                    return Response(
                        {'success': False, 'message': 'Invalid ride selected'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ride.DoesNotExist:
                return Response(
                    {'success': False, 'message': 'Ride not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Create the ticket
        try:
            ticket = SupportTicket.objects.create(
                user=user,
                user_type=user_type,
                issue_type=topic,
                description=description,
                ride=ride,
                status=SupportTicket.STATUS_PENDING,
            )
            
            logger.info(f"Support ticket created: {ticket.id} by user {user.id}")
            
            return Response({
                'success': True,
                'message': 'Support ticket created successfully',
                'ticket': ticket.to_dict()
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error creating support ticket: {e}")
            return Response(
                {'success': False, 'message': f'Error creating ticket: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserRidesForTicketView(APIView):
    """
    GET /api/support/user-rides/
    Get user's rides for ticket dropdown (recent completed/cancelled rides).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_type = request.query_params.get('user_type', 'passenger')
        
        # Get recent rides (last 30 days)
        from datetime import timedelta
        cutoff_date = timezone.now() - timedelta(days=30)
        
        if user_type == 'driver':
            # For driver - rides they completed
            from drivers.models import Driver
            try:
                driver = Driver.objects.get(user=user)
                rides = Ride.objects.filter(
                    driver=driver,
                    status__in=[Ride.STATUS_COMPLETED, Ride.STATUS_PAYMENT_CONFIRMED],
                    completed_at__gte=cutoff_date
                ).order_by('-completed_at')[:20]
            except Driver.DoesNotExist:
                rides = []
        else:
            # For passenger - their rides
            rides = Ride.objects.filter(
                passenger=user,
                status__in=[
                    Ride.STATUS_COMPLETED, 
                    Ride.STATUS_PAYMENT_CONFIRMED,
                    Ride.STATUS_CANCELLED
                ],
                requested_at__gte=cutoff_date
            ).order_by('-requested_at')[:20]
        
        ride_data = []
        for ride in rides:
            ride_info = {
                'id': ride.id,
                'pickup_address': ride.pickup_address or 'Unknown',
                'drop_address': ride.drop_address or 'Unknown',
                'date': ride.requested_at.isoformat() if ride.requested_at else None,
                'completed_at': ride.completed_at.isoformat() if ride.completed_at else None,
                'fare': float(ride.final_fare or ride.estimated_fare or 0),
                'status': ride.status,
            }
            
            # Add driver info for passenger view
            if user_type == 'passenger' and ride.driver:
                ride_info['driver_name'] = ride.driver.name
                ride_info['driver_phone'] = ride.driver.user.phone_number if ride.driver.user else None
            
            # Add passenger info for driver view
            if user_type == 'driver':
                ride_info['passenger_name'] = ride.passenger.name or 'Unknown'
            
            ride_data.append(ride_info)
        
        return Response({
            'success': True,
            'rides': ride_data
        })


class TicketTopicsView(APIView):
    """
    GET /api/support/topics/
    Get available topics based on user type.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_type = request.query_params.get('user_type', 'passenger')
        
        if user_type == 'driver':
            topics = [
                {'value': 'report_passenger', 'label': 'Report Passenger', 'requires_ride': True},
                {'value': 'passenger_refused_cash', 'label': 'Passenger Refused Cash', 'requires_ride': True},
                {'value': 'money_not_received', 'label': 'Money Not Received', 'requires_ride': True},
                {'value': 'passenger_abuse', 'label': 'Passenger Abuse/Misbehavior', 'requires_ride': True},
                {'value': 'payment_not_received', 'label': 'Payment Not Received', 'requires_ride': True},
                {'value': 'ride_issue', 'label': 'Ride Issue', 'requires_ride': True},
                {'value': 'glitch', 'label': 'Technical Glitch/App Bug', 'requires_ride': False},
                {'value': 'app_not_working', 'label': 'App Not Working', 'requires_ride': False},
                {'value': 'account_issue', 'label': 'Account Issue', 'requires_ride': False},
                {'value': 'other', 'label': 'Other', 'requires_ride': False},
            ]
        else:
            topics = [
                {'value': 'report_driver', 'label': 'Report Driver', 'requires_ride': True},
                {'value': 'overcharged', 'label': 'Overcharged', 'requires_ride': True},
                {'value': 'driver_abuse', 'label': 'Driver Abuse/Misbehavior', 'requires_ride': True},
                {'value': 'payment_failed', 'label': 'Payment Failed', 'requires_ride': True},
                {'value': 'ride_issue', 'label': 'Ride Issue', 'requires_ride': True},
                {'value': 'glitch', 'label': 'Technical Glitch/App Bug', 'requires_ride': False},
                {'value': 'app_not_working', 'label': 'App Not Working', 'requires_ride': False},
                {'value': 'account_issue', 'label': 'Account Issue', 'requires_ride': False},
                {'value': 'other', 'label': 'Other', 'requires_ride': False},
            ]
        
        return Response({
            'success': True,
            'topics': topics
        })


class AdminSupportTicketListView(APIView):
    """
    GET /api/admin/support/tickets/
    Admin view to list all tickets with filtering.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Check if user is admin/staff
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get filter parameters
        user_type = request.query_params.get('user_type')
        status_filter = request.query_params.get('status')
        topic = request.query_params.get('topic')
        priority = request.query_params.get('priority')
        
        tickets = SupportTicket.objects.all().order_by('-created_at')
        
        # Apply filters
        if user_type:
            tickets = tickets.filter(user_type=user_type)
        if status_filter:
            tickets = tickets.filter(status=status_filter)
        if topic:
            tickets = tickets.filter(topic=topic)
        if priority:
            tickets = tickets.filter(priority=priority)
        
        # Get summary counts
        summary = {
            'total': SupportTicket.objects.count(),
            'pending': SupportTicket.objects.filter(status=SupportTicket.STATUS_PENDING).count(),
            'in_progress': SupportTicket.objects.filter(status=SupportTicket.STATUS_IN_PROGRESS).count(),
            'resolved': SupportTicket.objects.filter(status=SupportTicket.STATUS_RESOLVED).count(),
            'driver_tickets': SupportTicket.objects.filter(user_type='driver').count(),
            'passenger_tickets': SupportTicket.objects.filter(user_type='passenger').count(),
        }
        
        return Response({
            'success': True,
            'summary': summary,
            'tickets': [ticket.to_dict() for ticket in tickets]
        })


class AdminTicketResponseView(APIView):
    """
    POST /api/admin/support/tickets/<id>/respond/
    Admin can respond to a ticket.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ticket_id):
        # Check if user is admin/staff
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {'success': False, 'message': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            ticket = SupportTicket.objects.get(id=ticket_id)
        except SupportTicket.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ticket not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        response_text = request.data.get('response', '').strip()
        new_status = request.data.get('status')
        
        if not response_text:
            return Response(
                {'success': False, 'message': 'Response text is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update ticket
        ticket.admin_response = response_text
        ticket.responded_by = request.user
        ticket.responded_at = timezone.now()
        
        if new_status and new_status in [s[0] for s in SupportTicket.STATUS_CHOICES]:
            ticket.status = new_status
        
        ticket.save()
        
        logger.info(f"Admin {request.user.id} responded to ticket {ticket.id}")
        
        return Response({
            'success': True,
            'message': 'Response sent successfully',
            'ticket': ticket.to_dict()
        })

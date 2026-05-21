"""
Django Admin configuration for Payments app
"""
from django.contrib import admin
from django.utils.html import format_html
from payments.models import Payment, DriverWallet, WalletTransaction, SupportTicket


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'ride_id', 'passenger', 'amount', 'method', 'status', 'created_at']
    list_filter = ['status', 'method', 'created_at']
    search_fields = ['ride__id', 'passenger__phone_number', 'razorpay_order_id', 'razorpay_payment_id']
    readonly_fields = ['razorpay_order_id', 'razorpay_payment_id', 'created_at', 'updated_at']


@admin.register(DriverWallet)
class DriverWalletAdmin(admin.ModelAdmin):
    list_display = ['driver', 'balance', 'total_earned', 'total_withdrawn', 'updated_at']
    search_fields = ['driver__name', 'driver__user__phone_number']
    readonly_fields = ['updated_at']


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['id', 'actor', 'actor_id', 'ride_id', 'amount', 'transaction_type', 'type', 'status', 'created_at']
    list_filter = ['actor', 'type', 'status', 'created_at']
    search_fields = ['actor_id', 'ride__id', 'idempotency_key']
    readonly_fields = ['created_at', 'processed_at']


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_type', 'get_user_info', 'issue_type', 'priority', 'status', 'get_rider_info', 'get_description_preview', 'has_response', 'created_at']
    list_filter = ['user_type', 'status', 'priority', 'issue_type', 'created_at']
    search_fields = ['user__phone_number', 'user__name', 'driver__name', 'description', 'admin_response']
    readonly_fields = ['created_at', 'updated_at', 'responded_at', 'get_rider_info_readonly']
    ordering = ['-priority', '-created_at']
    list_select_related = ['ride', 'ride__driver', 'user', 'driver']
    
    fieldsets = (
        ('Ticket Info', {
            'fields': ('user', 'user_type', 'driver', 'issue_type', 'priority', 'status')
        }),
        ('User Message', {
            'fields': ('description',),
            'classes': ('wide',)
        }),
        ('Ride & Rider Details', {
            'fields': ('ride', 'get_rider_info_readonly'),
        }),
        ('Admin Response', {
            'fields': ('admin_response', 'responded_by', 'responded_at'),
            'classes': ('collapse',)
        }),
        ('Legacy', {
            'fields': ('resolution_notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'resolved_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['mark_pending', 'mark_in_progress', 'mark_resolved', 'mark_closed']
    
    def get_user_info(self, obj):
        if obj.user:
            return f"{obj.user.name or 'N/A'} ({obj.user.phone_number})"
        elif obj.driver:
            return f"{obj.driver.name} (Driver)"
        return "Unknown"
    get_user_info.short_description = 'User'
    get_user_info.admin_order_field = 'user__name'
    
    def get_rider_info(self, obj):
        """Show rider info in list view"""
        if obj.ride and obj.ride.driver:
            driver = obj.ride.driver
            return f"{driver.name} ({driver.user.phone_number if driver.user else 'N/A'})"
        return "-"
    get_rider_info.short_description = 'Rider'
    
    def get_rider_info_readonly(self, obj):
        """Show detailed rider info in detail view"""
        if obj.ride and obj.ride.driver:
            driver = obj.ride.driver
            user_info = driver.user
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-radius: 4px;">'
                '<strong>Name:</strong> {}<br>'
                '<strong>Phone:</strong> {}<br>'
                '<strong>Vehicle:</strong> {}<br>'
                '<strong>Rating:</strong> {}<br>'
                '<strong>Status:</strong> {}'
                '</div>',
                driver.name,
                user_info.phone_number if user_info else 'N/A',
                driver.vehicle_type or 'N/A',
                driver.rating or 'N/A',
                'Online' if driver.is_online else 'Offline'
            )
        return "No rider assigned to this ride"
    get_rider_info_readonly.short_description = 'Rider Details'
    
    def get_description_preview(self, obj):
        """Show truncated description in list view"""
        if obj.description:
            return obj.description[:100] + '...' if len(obj.description) > 100 else obj.description
        return '-'
    get_description_preview.short_description = 'Message Preview'
    
    def has_response(self, obj):
        return bool(obj.admin_response)
    has_response.boolean = True
    has_response.short_description = 'Responded'
    
    def mark_pending(self, request, queryset):
        queryset.update(status=SupportTicket.STATUS_PENDING)
    mark_pending.short_description = "Mark as Pending"
    
    def mark_in_progress(self, request, queryset):
        queryset.update(status=SupportTicket.STATUS_IN_PROGRESS)
    mark_in_progress.short_description = "Mark as In Progress"
    
    def mark_resolved(self, request, queryset):
        queryset.update(status=SupportTicket.STATUS_RESOLVED)
    mark_resolved.short_description = "Mark as Resolved"
    
    def mark_closed(self, request, queryset):
        queryset.update(status=SupportTicket.STATUS_CLOSED)
    mark_closed.short_description = "Mark as Closed"

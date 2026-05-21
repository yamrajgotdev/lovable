from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.conf import settings
from django.urls import path, reverse
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.contrib.admin.utils import unquote

from .models import Driver


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'name',
        'phone_number',
        'vehicle_type',
        'vehicle_number',
        'approval_status',
        'status',
        'rating',
        'total_rides',
        'date_joined',
        'view_documents_button',
    ]
    list_filter = ['approval_status', 'status', 'vehicle_type', 'created_at', 'is_approved']
    search_fields = ['name', 'user__phone_number', 'vehicle_number', 'license_number', 'aadhaar_number', 'user__name']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    actions = ['mark_approved', 'mark_rejected']
    readonly_fields = [
        'rating',
        'total_rides',
        'created_at',
        'updated_at',
        'temp_offline_since',
        'profile_photo_preview',
        'license_photo_preview',
        'rc_photo_preview',
        'aadhaar_photo_preview',
        'pan_photo_preview',
        'aadhaar_number',
        'pan_number',
        'license_number',
        'approved_at',
        'rejected_at',
        'view_on_site',
    ]

    fieldsets = (
        ('Driver Info', {
            'fields': ('user', 'name', 'phone_number')
        }),
        ('Documents & Verification', {
            'fields': (
                'view_on_site',
                'profile_photo_preview',
                'profile_photo',
                'license_photo_preview',
                'license_photo',
                'rc_photo_preview',
                'rc_photo',
                'aadhaar_photo_preview',
                'aadhaar_photo',
                'pan_photo_preview',
                'pan_photo',
                'license_number',
                'aadhaar_number',
                'pan_number',
                'verification_notes',
            ),
            'description': 'Review uploaded documents before approving/rejecting the driver. Click "View Documents" for full page view. Click on images to view full size.'
        }),
        ('Vehicle Info', {
            'fields': ('vehicle_type', 'vehicle_number')
        }),
        ('Operational Status', {
            'fields': ('status', 'temp_offline_since', 'last_location_update'),
            'description': 'Driver online/offline status. TEMP_OFFLINE = 30 second grace before going fully offline.'
        }),
        ('Approval & Status', {
            'fields': ('approval_status', 'is_approved', 'approved_at', 'rejected_at'),
        }),
        ('Location', {
            'fields': ('current_lat', 'current_lng'),
            'classes': ('collapse',)
        }),
        ('Stats', {
            'fields': ('rating', 'total_rides', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    # Image preview methods
    def profile_photo_preview(self, obj):
        if obj.profile_photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 150px; max-width: 300px; border-radius: 8px; border: 1px solid #ddd;" /></a>',
                obj.profile_photo.url, obj.profile_photo.url
            )
        return "No photo uploaded"
    profile_photo_preview.short_description = 'Profile Photo Preview'
    
    def license_photo_preview(self, obj):
        if obj.license_photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 150px; max-width: 300px; border-radius: 8px; border: 1px solid #ddd;" /></a>',
                obj.license_photo.url, obj.license_photo.url
            )
        return "No photo uploaded"
    license_photo_preview.short_description = 'License Photo Preview'
    
    def rc_photo_preview(self, obj):
        if obj.rc_photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 150px; max-width: 300px; border-radius: 8px; border: 1px solid #ddd;" /></a>',
                obj.rc_photo.url, obj.rc_photo.url
            )
        return "No photo uploaded"
    rc_photo_preview.short_description = 'RC Photo Preview'
    
    def aadhaar_photo_preview(self, obj):
        if obj.aadhaar_photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 150px; max-width: 300px; border-radius: 8px; border: 1px solid #ddd;" /></a>',
                obj.aadhaar_photo.url, obj.aadhaar_photo.url
            )
        return "No photo uploaded"
    aadhaar_photo_preview.short_description = 'Aadhaar Photo Preview'
    
    def pan_photo_preview(self, obj):
        if obj.pan_photo:
            return format_html(
                '<a href="{}" target="_blank"><img src="{}" style="max-height: 150px; max-width: 300px; border-radius: 8px; border: 1px solid #ddd;" /></a>',
                obj.pan_photo.url, obj.pan_photo.url
            )
        return "No photo uploaded"
    pan_photo_preview.short_description = 'PAN Photo Preview'

    def phone_number(self, obj):
        return obj.user.phone_number
    phone_number.short_description = 'Phone'
    phone_number.admin_order_field = 'user__phone_number'

    def date_joined(self, obj):
        return obj.created_at.strftime('%d %b %Y')
    date_joined.short_description = 'Joined On'
    date_joined.admin_order_field = 'created_at'

    def view_documents_button(self, obj):
        """Show quick document status with link to full document viewer"""
        docs = []
        if obj.license_photo:
            docs.append('<span style="color:green">✓ DL</span>')
        else:
            docs.append('<span style="color:red">✗ DL</span>')

        if obj.rc_photo:
            docs.append('<span style="color:green">✓ RC</span>')
        else:
            docs.append('<span style="color:red">✗ RC</span>')

        if obj.aadhaar_photo:
            docs.append('<span style="color:green">✓ Aadhaar</span>')
        else:
            docs.append('<span style="color:red">✗ Aadhaar</span>')

        if obj.pan_photo:
            docs.append('<span style="color:green">✓ PAN</span>')
        else:
            docs.append('<span style="color:red">✗ PAN</span>')

        url = reverse('admin:driver_documents', args=[obj.pk])
        return format_html(
            '<a href="{}" style="text-decoration:none;">{}</a>',
            url, ' | '.join(docs)
        )
    view_documents_button.short_description = 'Documents'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/documents/',
                self.admin_site.admin_view(self.view_documents),
                name='driver_documents',
            ),
            path(
                '<path:object_id>/approve/',
                self.admin_site.admin_view(self.approve_driver),
                name='driver_approve',
            ),
            path(
                '<path:object_id>/reject/',
                self.admin_site.admin_view(self.reject_driver),
                name='driver_reject',
            ),
        ]
        return custom_urls + urls

    def view_documents(self, request, object_id):
        """Custom admin view to display all driver documents for verification."""
        from django.contrib.admin.utils import unquote
        from django.template.response import TemplateResponse

        driver = self.get_object(request, unquote(object_id))
        if driver is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)

        context = {
            'title': f'Documents: {driver.name}',
            'driver': driver,
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request, driver),
            'has_change_permission': self.has_change_permission(request, driver),
            'back_url': reverse('admin:drivers_driver_changelist'),
            'edit_url': reverse('admin:drivers_driver_change', args=[driver.pk]),
            'approve_url': reverse('admin:driver_approve', args=[driver.pk]),
            'reject_url': reverse('admin:driver_reject', args=[driver.pk]),
        }

        return TemplateResponse(request, 'admin/drivers/view_documents.html', context)

    def approve_driver(self, request, object_id):
        """Handle driver approval from the documents page."""
        from django.contrib import messages

        driver = self.get_object(request, unquote(object_id))
        if driver is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)

        if not self.has_change_permission(request, driver):
            messages.error(request, "You don't have permission to approve this driver.")
            return HttpResponseRedirect(reverse('admin:driver_documents', args=[object_id]))

        now = timezone.now()
        driver.approval_status = Driver.APPROVAL_APPROVED
        driver.is_approved = True
        driver.approved_at = driver.approved_at or now
        driver.rejected_at = None
        driver.save()

        messages.success(request, f'Driver "{driver.name}" has been approved successfully.')
        return HttpResponseRedirect(reverse('admin:driver_documents', args=[object_id]))

    def reject_driver(self, request, object_id):
        """Handle driver rejection from the documents page."""
        from django.contrib import messages

        driver = self.get_object(request, unquote(object_id))
        if driver is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)

        if not self.has_change_permission(request, driver):
            messages.error(request, "You don't have permission to reject this driver.")
            return HttpResponseRedirect(reverse('admin:driver_documents', args=[object_id]))

        now = timezone.now()
        driver.approval_status = Driver.APPROVAL_REJECTED
        driver.is_approved = False
        driver.rejected_at = driver.rejected_at or now
        driver.approved_at = None
        driver.save()

        messages.success(request, f'Driver "{driver.name}" has been rejected.')
        return HttpResponseRedirect(reverse('admin:driver_documents', args=[object_id]))

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user')

    def view_on_site(self, obj):
        """Add link to view documents page"""
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse('admin:driver_documents', args=[obj.pk])
        return format_html(
            '<a href="{}" target="_blank" style="padding: 5px 15px; background: #417690; color: white; border-radius: 4px; text-decoration: none;">View Documents</a>',
            url
        )
    view_on_site.short_description = 'Quick Actions'

    def save_model(self, request, obj, form, change):
        if obj.is_approved and obj.approval_status != Driver.APPROVAL_APPROVED:
            obj.approval_status = Driver.APPROVAL_APPROVED
        elif not obj.is_approved and obj.approval_status == Driver.APPROVAL_APPROVED:
            obj.approval_status = Driver.APPROVAL_PENDING

        now = timezone.now()
        if obj.approval_status == Driver.APPROVAL_APPROVED:
            obj.is_approved = True
            obj.approved_at = obj.approved_at or now
            obj.rejected_at = None
        elif obj.approval_status == Driver.APPROVAL_REJECTED:
            obj.is_approved = False
            obj.rejected_at = obj.rejected_at or now
            obj.approved_at = None
        else:
            obj.is_approved = False
            obj.approved_at = None
            obj.rejected_at = None

        super().save_model(request, obj, form, change)

    @admin.action(description='Approve selected drivers')
    def mark_approved(self, request, queryset):
        now = timezone.now()
        queryset.update(
            approval_status=Driver.APPROVAL_APPROVED,
            is_approved=True,
            approved_at=now,
            rejected_at=None,
        )

    @admin.action(description='Reject selected drivers')
    def mark_rejected(self, request, queryset):
        now = timezone.now()
        queryset.update(
            approval_status=Driver.APPROVAL_REJECTED,
            is_approved=False,
            rejected_at=now,
            approved_at=None,
        )

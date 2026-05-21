import { AlertCircle, CheckCircle, AlertTriangle, Info } from "lucide-react";

export type AlertType = "error" | "success" | "warning" | "info";

export interface ErrorAlert {
  type: AlertType;
  title: string;
  message: string;
  code?: string;
  action?: {
    label: string;
    callback: () => void;
  };
}

const ERROR_MESSAGES: Record<string, { title: string; message: string }> = {
  // Auth Errors
  AUTH_INVALID_OTP: {
    title: "Invalid OTP",
    message: "The code you entered is incorrect. Please try again.",
  },
  AUTH_OTP_EXPIRED: {
    title: "Code Expired",
    message: "Your verification code has expired. Request a new one.",
  },
  AUTH_PHONE_EXISTS: {
    title: "Account Already Exists",
    message: "This phone number is already registered. Try signing in instead.",
  },
  AUTH_UNAUTHORIZED: {
    title: "Authentication Failed",
    message: "Please sign in again to continue.",
  },

  // Ride Errors
  RIDE_NOT_FOUND: {
    title: "Ride Not Found",
    message: "This ride no longer exists. It may have been cancelled.",
  },
  RIDE_ALREADY_ACTIVE: {
    title: "Active Ride Exists",
    message: "You already have an active ride. Complete or cancel it first.",
  },
  RIDE_CANCELLED: {
    title: "Ride Cancelled",
    message: "This ride has been cancelled and is no longer available.",
  },
  RIDE_INVALID_LOCATIONS: {
    title: "Invalid Locations",
    message: "Please select valid pickup and drop-off locations.",
  },
  RIDE_MIN_DISTANCE: {
    title: "Distance Too Short",
    message: "The distance between pickup and drop-off is too short for a ride.",
  },
  RIDE_DRIVER_UNAVAILABLE: {
    title: "No Drivers Available",
    message: "No drivers are currently available in your area. Please try again.",
  },

  // Payment Errors
  PAYMENT_FAILED: {
    title: "Payment Failed",
    message: "Your payment could not be processed. Please try again.",
  },
  PAYMENT_INVALID_AMOUNT: {
    title: "Invalid Amount",
    message: "The payment amount is invalid. Please check your fare.",
  },

  // Location Errors
  LOCATION_DENIED: {
    title: "Location Access Denied",
    message: "Please enable location services in your device settings.",
  },
  LOCATION_TIMEOUT: {
    title: "Location Timeout",
    message: "Could not get your location. Please check your GPS signal.",
  },
  LOCATION_UNAVAILABLE: {
    title: "Location Unavailable",
    message: "Your location service is temporarily unavailable. Please try again.",
  },

  // Network Errors
  NETWORK_ERROR: {
    title: "Connection Error",
    message: "Check your internet connection and try again.",
  },
  NETWORK_TIMEOUT: {
    title: "Request Timeout",
    message: "The request took too long. Please try again.",
  },

  // Server Errors
  SERVER_ERROR: {
    title: "Server Error",
    message: "Something went wrong on our end. Please try again later.",
  },
  SERVICE_UNAVAILABLE: {
    title: "Service Unavailable",
    message: "Our service is temporarily unavailable. Please try again soon.",
  },

  // Validation Errors
  VALIDATION_ERROR: {
    title: "Invalid Input",
    message: "Please check your input and try again.",
  },
  INVALID_PHONE: {
    title: "Invalid Phone Number",
    message: "Please enter a valid Indian phone number (10 digits).",
  },
  INVALID_EMAIL: {
    title: "Invalid Email",
    message: "Please enter a valid email address.",
  },

  // Driver/Rider Specific
  DRIVER_OFFLINE: {
    title: "You Are Offline",
    message: "Turn on your location and go online to receive ride requests.",
  },
  DRIVER_UNVERIFIED: {
    title: "Verification Pending",
    message: "Please complete your verification to start earning.",
  },
  DRIVER_INSUFFICIENT_BALANCE: {
    title: "Insufficient Balance",
    message: "You need more balance to continue. Please add funds.",
  },

  // Rating/Feedback
  RATING_FAILED: {
    title: "Rating Failed",
    message: "Could not submit your rating. Please try again.",
  },

  // Generic
  GENERIC_ERROR: {
    title: "Something Went Wrong",
    message: "An unexpected error occurred. Please try again.",
  },
};

export function getErrorMessage(code?: string): ErrorAlert {
  const errorInfo = code && ERROR_MESSAGES[code] ? ERROR_MESSAGES[code] : ERROR_MESSAGES.GENERIC_ERROR;

  return {
    type: "error",
    title: errorInfo.title,
    message: errorInfo.message,
    code,
  };
}

export function getSuccessMessage(action: string): ErrorAlert {
  const messages: Record<string, { title: string; message: string }> = {
    ride_booked: { title: "Ride Booked", message: "Your ride has been confirmed. Driver will arrive soon." },
    ride_completed: { title: "Ride Complete", message: "Thanks for using RIDES4U! Please rate your driver." },
    payment_success: { title: "Payment Successful", message: "Your payment has been processed." },
    profile_updated: { title: "Profile Updated", message: "Your profile has been successfully updated." },
    rating_submitted: { title: "Rating Submitted", message: "Thank you for your feedback!" },
  };

  const info = messages[action] || { title: "Success", message: "Operation completed successfully." };

  return {
    type: "success",
    title: info.title,
    message: info.message,
  };
}

interface AlertIconProps {
  type: AlertType;
  className?: string;
}

export function AlertIcon({ type, className = "w-5 h-5" }: AlertIconProps) {
  switch (type) {
    case "error":
      return <AlertCircle className={className + " text-destructive"} />;
    case "success":
      return <CheckCircle className={className + " text-emerald-500"} />;
    case "warning":
      return <AlertTriangle className={className + " text-yellow-500"} />;
    case "info":
      return <Info className={className + " text-blue-500"} />;
  }
}

interface AlertBoxProps {
  alert: ErrorAlert;
  onDismiss?: () => void;
}

export function AlertBox({ alert, onDismiss }: AlertBoxProps) {
  const bgClass = {
    error: "bg-destructive/10 border-destructive/20",
    success: "bg-emerald-500/10 border-emerald-500/20",
    warning: "bg-yellow-500/10 border-yellow-500/20",
    info: "bg-blue-500/10 border-blue-500/20",
  }[alert.type];

  const textClass = {
    error: "text-destructive",
    success: "text-emerald-600 dark:text-emerald-400",
    warning: "text-yellow-600 dark:text-yellow-400",
    info: "text-blue-600 dark:text-blue-400",
  }[alert.type];

  return (
    <div className={`rounded-lg border ${bgClass} p-4`}>
      <div className="flex gap-3">
        <AlertIcon type={alert.type} />
        <div className="flex-1">
          <h4 className={`font-semibold ${textClass}`}>{alert.title}</h4>
          <p className="text-sm text-muted-foreground mt-1">{alert.message}</p>
          {alert.action && (
            <button
              onClick={alert.action.callback}
              className="mt-2 text-xs font-semibold text-primary hover:underline"
            >
              {alert.action.label}
            </button>
          )}
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}

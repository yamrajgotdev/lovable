import { createFileRoute, Link, useNavigate, useRouteContext } from "@tanstack/react-router";
import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Navbar } from "@/components/Navbar";
import { Btn, Field } from "@/components/Field";
import { MapCanvas } from "@/components/MapCanvas";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTheme } from "@/hooks/useTheme";
import { api, auth, normalizeRide, type Ride } from "@/lib/api";

type RideSocketMessage =
  | { type: "ride_update"; ride?: Ride; status?: string }
  | { type: "ride_sync"; status: string; ride_id: string }
  | { type: "driver_location"; lat: number; lng: number; heading?: number }
  // NEW: Payment status sync via WebSocket
  | { type: "payment_update"; notification: { type: string; payment_status: string; payment_method?: string; amount?: number; message?: string } }
  | { type: "payment_received"; ride_id: string; amount: number; message: string; added_to_wallet: boolean }
  | { type: "notification"; notification: { message?: string } };

// ============================================================
// CHAT SYSTEM - Import chat component
// ADDED: Chat feature for driver-passenger communication
// ============================================================
import { RideChat } from "@/components/RideChat";

export const Route = createFileRoute("/driver/ride/$rideId")({
  head: ({ params }) => ({ meta: [{ title: `Ride ${params.rideId} - Driver` }] }),
  component: DriverRide,
});

// State priority order - higher index = later state (forward only progression)
const STATE_PRIORITY: Record<string, number> = {
  "requested": 0,
  "searching": 1,
  "searching_driver": 1,  // Backend alias for searching
  "accepted": 2,
  "driver_assigned": 2,   // Backend alias for accepted
  "driver_arriving": 3,
  "driver_arrived": 4,    // Fixed: was 5, now sequential
  "arrived": 4,           // Backend alias
  "otp_verified": 5,      // Fixed: was 6, now sequential
  "started": 6,           // Fixed: was 7, now sequential
  "in_progress": 6,       // Backend alias for started
  "reached_destination": 7, // Fixed: was 8, now sequential
  "payment_required": 8,  // Fixed: was 9, now sequential
  "payment_confirmed": 9, // Fixed: was 10, now sequential
  "completed": 10,        // Fixed: was 11, now sequential
  "ride_finished": 10,    // Backend alias
  "cancelled": 11,        // Fixed: was 12, now sequential
};

// Check if new state is forward progression (or same)
function isForwardProgress(current: string, incoming: string): boolean {
  const currentPriority = STATE_PRIORITY[current] ?? -1;
  const incomingPriority = STATE_PRIORITY[incoming] ?? -1;
  return incomingPriority >= currentPriority;
}

// Mask phone number for privacy - show first 2 and last 3 digits only
function maskPhoneNumber(phone: string | undefined | null): string {
  if (!phone) return "";
  const cleanPhone = phone.replace(/\D/g, "");
  if (cleanPhone.length < 5) return phone;
  const firstTwo = cleanPhone.slice(0, 2);
  const lastThree = cleanPhone.slice(-3);
  const middleStars = "*".repeat(cleanPhone.length - 5);
  return `${firstTwo}${middleStars}${lastThree}`;
}

function DriverRide() {
  const { rideId } = Route.useParams();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { notificationMsg } = useRouteContext({ from: "/driver/ride/$rideId" }) as any;
  const [ride, setRide] = useState<Ride | null>(null);
  const RIDE_POLL_INTERVAL = 4000;
  const lastWsEventAtRef = useRef(0);
  const pendingActionRef = useRef<string | null>(null);
  const lastActionTimeRef = useRef<number>(0);

  // Safe state setter with priority check and logging.
  const updateRideState = useCallback((newRide: Ride, source: string) => {
    setRide((current) => {
      const currentStatus = current?.status ?? "";
      const newStatus = newRide.status;

      console.log(`[STATE_SYNC] Source: ${source}, Current: ${currentStatus}, Incoming: ${newStatus}`);

      // Ignore only REST refreshes during local action cooldown.
      const now = Date.now();
      const actionCooldown = now - lastActionTimeRef.current < 3000;
      const isRestSource = source === "API_POLL" || source === "INITIAL_LOAD";
      if (pendingActionRef.current && actionCooldown && isRestSource) {
        console.log(`[STATE_SYNC] Ignoring ${source} due to pending action: ${pendingActionRef.current}`);
        return current;
      }

      if (pendingActionRef.current && newStatus === pendingActionRef.current) {
        console.log(`[STATE_SYNC] Pending action ${pendingActionRef.current} confirmed`);
        pendingActionRef.current = null;
      }

      if (!isForwardProgress(currentStatus, newStatus)) {
        console.log(`[STATE_SYNC] REJECTED backward transition: ${currentStatus} → ${newStatus}`);
        return current;
      }

      console.log(`[STATE_SYNC] ACCEPTED: ${currentStatus} → ${newStatus}`);
      console.log(`[STATE UPDATE] driver ${source}`);
      return newRide;
    });
  }, []);

  const reconcilePaymentStateDriver = (incoming: Ride | null) => {
    if (!incoming) return;
    const status = String(incoming.status || "").toLowerCase();
    const paymentStatusField = String(incoming.paymentStatus || (incoming as any).payment_status || "").toLowerCase();

    const paid = ["paid", "success", "payment_confirmed"].includes(paymentStatusField) || ["paid", "success"].includes(status);
    const terminal = ["completed", "cancelled", "payment_confirmed", "failed"].includes(status) || paid;

    // Only show completed UI if ride is ACTUALLY completed (not just reached_destination or payment_confirmed)
    if (status === "completed" || status === "cancelled") {
      setPaymentStatus("paid");
      setShowCollect(false);
      // normalize local ride to terminal completed
      updateRideState({ ...(incoming as Ride), status: "completed", paymentStatus: "paid" } as Ride, "LOCAL_NORMALIZE");

      // ONLY set pending rating when status is EXACTLY 'completed'
      // Backend requires 'completed' status for rating submission
      if (typeof window !== "undefined" && status === "completed") {
        try { 
          localStorage.setItem("pending_rating_driver_ride_id", incoming.id);
          localStorage.setItem("pending_rating_timestamp", Date.now().toString());
        } catch {}
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }
    }
  };

  const refreshRide = async (force = false) => {
    try {
      // BUG FIX: Add force parameter to bypass throttling when called from WebSocket handler
      // Without this, the timestamp was just updated by onMessage, causing immediate return
      if (!force && Date.now() - lastWsEventAtRef.current < 1500 && rideWsState === "open") {
        console.log("[refreshRide] Skipped due to throttling");
        return;
      }
      console.log("[refreshRide] Fetching ride data...", { force, rideId });
      const { ride: fetchedRide } = await api.ride(rideId);
      console.log("[refreshRide] Got ride data:", { 
        id: fetchedRide?.id, 
        status: fetchedRide?.status, 
        hasPassenger: !!fetchedRide?.passenger,
        passengerName: fetchedRide?.passenger?.name,
        fare: fetchedRide?.fare
      });
      reconcilePaymentStateDriver(fetchedRide);
      updateRideState(fetchedRide, "API_POLL");
    } catch (e) {
      console.error("[refreshRide] Error:", e);
    }
  };

  const { state: rideWsState, setSyncComplete } = useWebSocket<RideSocketMessage>(`/ws/ride/${rideId}/`, {
    onMessage: (message) => {
      lastWsEventAtRef.current = Date.now();
      console.log("[WS EVENT] driver ride", message.type);

      if (message.type === "ride_update") {
        if (message.ride && message.ride.status) {
          const mergedRaw = { ...(ride || {}), ...message.ride };
          const updatedRide = normalizeRide(mergedRaw);
          updateRideState(updatedRide, "WEBSOCKET");

          if (["completed", "cancelled", "payment_confirmed", "failed"].includes(updatedRide.status)) {
            if (typeof window !== "undefined") {
              localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
              window.dispatchEvent(new Event("rides4u:activeCleared"));
            }
          }
        } else if (message.status) {
          const wsStatus = normalizeRide({ status: message.status }).status;
          // BUG FIX: Always fetch full ride data from API when status changes via WebSocket
          // The WebSocket only sends status, not full ride data (passenger, fare, etc.)
          console.log("[WS] Status-only update, triggering refresh:", wsStatus);
          void refreshRide(true);

          if (["completed", "cancelled", "payment_confirmed", "failed"].includes(wsStatus)) {
            if (typeof window !== "undefined") {
              localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
              window.dispatchEvent(new Event("rides4u:activeCleared"));
            }
          }
        } else {
          // BUG FIX: Pass force=true to bypass throttling since timestamp was just updated
          void refreshRide(true);
        }
      }

      if (message.type === "ride_sync") {
        const syncStatus = normalizeRide({ status: message.status }).status;
        if (ride) updateRideState({ ...ride, status: syncStatus }, "WEBSOCKET_SYNC");
        // BUG FIX: Pass force=true to bypass throttling since timestamp was just updated
        else void refreshRide(true);
      }

      if (message.type === "payment_update") {
        const { notification } = message;
        if (typeof notification.amount === "number" && Number.isFinite(notification.amount)) {
          setPaymentAmountHint(notification.amount);
        }
        if (notification.payment_status === "SUCCESS") {
          setPaymentStatus("paid");
          toast.success(notification.message || "Payment received! Great job!");

          // Mark only payment settled locally; avoid forcing overall ride status to 'completed' until REST confirms
          setRide((current) => (current ? { ...current, paymentStatus: "paid" } as Ride : current));

          // BUG FIX: Pass force=true to bypass throttling since timestamp was just updated
          void refreshRide(true);
        }
      }

      if (message.type === "payment_received") {
        if (typeof message.amount === "number" && Number.isFinite(message.amount)) {
          setPaymentAmountHint(message.amount);
        }
        setPaymentStatus("paid");
        toast.success(message.message || "Payment received! Keep driving!");

        // Mark only payment settled locally; avoid forcing overall ride status to 'completed' until REST confirms
        setRide((current) => (current ? { ...current, paymentStatus: "paid" } as Ride : current));

        // BUG FIX: Pass force=true to bypass throttling since timestamp was just updated
        void refreshRide(true);
      }

      if (message.type === "notification") {
        const msg = message.notification?.message;
        if (msg) toast.info(msg); // Keep server message as-is
      }
    },
    onSyncRequired: () => {
      // BUG FIX: Pass force=true to bypass throttling
      refreshRide(true).finally(() => {
        setSyncComplete();
      });
    }
  });

  // Controlled fallback: poll only while socket is disconnected.
  useEffect(() => {
    if (rideWsState !== "closed") return;
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      await refreshRide();
    };
    void poll();
    const interval = setInterval(() => {
      void poll();
    }, RIDE_POLL_INTERVAL);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [rideWsState, rideId]);

  const [showCollect, setShowCollect] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [codeVerified, setCodeVerified] = useState(false);
  const [codeError, setCodeError] = useState(false);
  const [paymentStatus, setPaymentStatus] = useState<"pending" | "checking" | "paid" | "failed">("pending");
  const [paymentAmountHint, setPaymentAmountHint] = useState<number | null>(null);
  const [qrData, setQrData] = useState<string | null>(null);
  const [isFullScreenMap, setIsFullScreenMap] = useState(false);
  const lastLocationPushAt = useRef(0);
  const statusLower = String(ride?.status || "").toLowerCase();
  const ridePaymentStatus = (ride?.paymentStatus || "").toLowerCase();
  const paymentAlreadySettled =
    ["payment_confirmed", "completed", "cancelled"].includes(statusLower) ||
    paymentStatus === "paid" ||
    ((ridePaymentStatus === "paid" || ridePaymentStatus === "success") && statusLower !== "payment_required");
  const collectAmountCandidates = [
    Number(ride?.fare?.total),
    Number((ride as any)?.final_fare),
    Number((ride as any)?.estimated_fare),
    Number(paymentAmountHint),
  ].filter((value) => Number.isFinite(value) && value > 0);
  const collectAmount = collectAmountCandidates.length > 0 ? collectAmountCandidates[0] : 0;

  // Initial load - fetch once, then rely on WebSocket for updates
  useEffect(() => {
    if (typeof window !== "undefined" && (!auth.token || auth.role !== "rider")) {
      navigate({ to: "/" });
      return;
    }

    let alive = true;
    const fetchInitial = async () => {
      try {
        console.log("[Initial Load] Fetching ride...", rideId);
        const { ride: fetchedRide } = await api.ride(rideId);
        console.log("[Initial Load] Got ride:", {
          id: fetchedRide?.id,
          status: fetchedRide?.status,
          hasPassenger: !!fetchedRide?.passenger,
          passengerName: fetchedRide?.passenger?.name,
          fare: fetchedRide?.fare
        });
        if (alive) updateRideState(fetchedRide, "INITIAL_LOAD");
      } catch (error) {
        console.error("[Initial Load] Error:", error);
        if (alive) toast.error("Couldn't load ride details. Please refresh the page.");
      }
    };

    fetchInitial();
    // No polling - WebSocket handles real-time updates
    return () => {
      alive = false;
    };
  }, [rideId, navigate]);

  // NEW: Redirect to home when ride is completed or cancelled
  useEffect(() => {
    if (ride?.status === "completed") {
      toast.success("Ride complete! Excellent service! Returning to home...");
      setPaymentStatus("paid");
      setShowCollect(false); // Ensure payment popup is closed
      if (typeof window !== "undefined") {
        // Check if rating was not already submitted
        const alreadySubmitted = localStorage.getItem(`rating_submitted_${ride.id}`);
        if (!alreadySubmitted) {
          localStorage.setItem("pending_rating_driver_ride_id", ride.id);
          localStorage.setItem("pending_rating_timestamp", Date.now().toString());
        }
        // Announce clear so global popup and caches are cleaned immediately
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }
      setTimeout(() => navigate({ to: "/driver" }), 1500);
    } else if (ride?.status === "cancelled") {
      toast.error("Ride was cancelled by the passenger. Returning to home...");
      setShowCollect(false);
      if (typeof window !== "undefined") {
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }
      setTimeout(() => navigate({ to: "/driver" }), 2000);
    }
  }, [ride?.status, navigate, ride?.id]);

  // Ensure API-refresh path also clears global active ride when terminal states are observed
  useEffect(() => {
    const terminal = ["completed", "cancelled", "payment_confirmed", "failed"];
    if (ride?.status && terminal.includes(ride.status)) {
      if (typeof window !== "undefined") {
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }
    }
  }, [ride?.status]);

  useEffect(() => {
    if (paymentAlreadySettled) {
      setPaymentStatus("paid");
      setShowCollect(false);
    }
  }, [paymentAlreadySettled]);

  useEffect(() => {
    if (typeof window === "undefined" || !navigator.geolocation) return;

    const watcherId = navigator.geolocation.watchPosition(
      (position) => {
        const lat = position.coords.latitude;
        const lng = position.coords.longitude;
        const heading = position.coords.heading ?? undefined;
        const speed = position.coords.speed ?? undefined;
        const accuracy = position.coords.accuracy ?? undefined;

        setRide((current) =>
          current
            ? {
                ...current,
                driver: {
                  ...(current.driver ?? { name: "", phone: "", plate: "" }),
                  location: { lat, lng, heading },
                },
              }
            : current,
        );

        const now = Date.now();
        if (now - lastLocationPushAt.current < 2500) return;
        lastLocationPushAt.current = now;

        void api.updateDriverLocation(lat, lng, heading, speed, accuracy).catch(() => {
          /* ignore transient location failures */
        });
      },
      () => {
        /* ignore */
      },
      { enableHighAccuracy: true, maximumAge: 3000 },
    );

    return () => navigator.geolocation.clearWatch(watcherId);
  }, []);

  const arrived = async () => {
    setBusy(true);
    // Set pending action to prevent stale API responses from overwriting
    pendingActionRef.current = "driver_arrived";
    lastActionTimeRef.current = Date.now();
    console.log("[ACTION] Arrived clicked - blocking stale responses for 3s");

    try {
      const { ride: updatedRide } = await api.arrivedPickup(rideId);
      console.log("[ACTION] Arrived API success, status:", updatedRide.status);

      // Directly set state (bypass pending check since we got confirmation)
      pendingActionRef.current = null;
      setRide(updatedRide);
      toast.success("You've arrived! Waiting for passenger to share the code.");
    } catch (error) {
      pendingActionRef.current = null;
      console.error("[ACTION] Arrived API failed:", error);
      toast.error("Couldn't mark arrival. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (code.length < 4) {
      toast.error("Please enter the 4-digit code from the passenger.");
      setCodeError(true);
      return;
    }

    setBusy(true);
    setCodeError(false);
    // Set pending action to prevent stale API responses
    pendingActionRef.current = "started";
    lastActionTimeRef.current = Date.now();
    console.log("[ACTION] Start clicked - blocking stale responses for 3s");

    try {
      const { ride: updatedRide } = await api.startRide(rideId, code);
      console.log("[ACTION] Start API success, status:", updatedRide.status);

      pendingActionRef.current = null;
      setRide(updatedRide);
      setCodeVerified(true);
      setCodeError(false);
      toast.success("Code verified! Let's go! Drive safe!");
      setCode("");
    } catch (error) {
      pendingActionRef.current = null;
      console.error("[ACTION] Start API failed:", error);
      toast.error("Incorrect code. Please ask the passenger for the correct 4-digit code.");
      setCodeVerified(false);
      setCodeError(true);
    } finally {
      setBusy(false);
    }
  };

  // NEW: Mark destination reached - triggers payment flow
  const reachedDest = async () => {
    setBusy(true);
    // Set pending action to prevent stale API responses
    pendingActionRef.current = "payment_required";
    lastActionTimeRef.current = Date.now();
    console.log("[ACTION] Reached destination clicked - blocking stale responses for 3s");

    try {
      const { ride: updatedRide } = await api.reachedDestination(rideId);
      console.log("[ACTION] Reached destination API success, status:", updatedRide.status);

      pendingActionRef.current = null;
      setRide(updatedRide);
      toast.success("Trip complete! Collect payment from passenger.");
      // Payment popup will be triggered by useEffect when status changes to reached_destination
    } catch (error) {
      pendingActionRef.current = null;
      console.error("[ACTION] Reached destination API failed:", error);
      toast.error("Couldn't complete the trip. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  // NEW: Trigger payment confirmation popup when ride is finished and payment is cash/pending
  useEffect(() => {
    const shouldShowCashPopup =
      ["reached_destination", "payment_required"].includes(ride?.status || "") &&
      (!ride?.paymentMethod || ride?.paymentMethod === "cash") &&
      !paymentAlreadySettled;

    const shouldShowOnlineConfirmation =
      ["reached_destination", "payment_required"].includes(ride?.status || "") &&
      ride?.paymentMethod === "online" &&
      !paymentAlreadySettled;

    if (shouldShowCashPopup) {
      setShowCollect(true);
    } else if (shouldShowOnlineConfirmation) {
      // For online payments, show a different UI
      setShowCollect(true);
      // Start polling for payment status
      startPaymentPolling();
    }
  }, [ride?.status, ride?.paymentMethod, ride?.paymentStatus, paymentAlreadySettled]);

  // If redirected directly into payment_required ride, force-open collection popup.
  useEffect(() => {
    if (statusLower === "payment_required" && !paymentAlreadySettled) {
      setShowCollect(true);
    }
  }, [statusLower, paymentAlreadySettled]);

  // Payment is now handled exclusively via WebSocket - no polling needed
  // WebSocket messages (payment_update, payment_received) handle real-time status
  const startPaymentPolling = () => {
    // DEPRECATED: WebSocket now handles all payment status updates
    // This function is kept for API compatibility but does nothing
    console.log("[Payment] Using WebSocket for payment status - polling deprecated");
  };

  const fetchQrCode = async () => {
    try {
      const res = await api.createPaymentOrder(rideId);
      setQrData(res.qr || null);
    } catch (error) {
      console.error("Failed to fetch QR:", error);
    }
  };

  const checkPaymentStatus = async () => {
    setPaymentStatus("checking");
    try {
      const status = await api.checkPaymentStatus(rideId);
      if (status.status === "paid") {
        setPaymentStatus("paid");
        toast.success("Payment confirmed! Great job today!");
        setTimeout(() => navigate({ to: "/driver" }), 1500);
      } else {
        setPaymentStatus("pending");
        toast.info("Waiting for payment... Ask the passenger to complete the payment.");
      }
    } catch (error) {
      setPaymentStatus("failed");
      toast.error("Couldn't check payment status. Please try again in a moment.");
    }
  };

  // NEW: Confirm cash collection with proper API
  const confirmCashCollection = async () => {
    setBusy(true);
    try {
      const response = await api.confirmCashCollection(rideId);
      if (response.success) {
        toast.success("Cash collected! Added to your earnings.");
        if (typeof response.amount === "number" && Number.isFinite(response.amount)) {
          setPaymentAmountHint(response.amount);
        }
        setPaymentStatus("paid");
        const { ride: refreshedRide } = await api.ride(rideId);
        updateRideState(refreshedRide, "POST_CASH_CONFIRM");
        setShowCollect(false);
        if (typeof window !== "undefined") {
          localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
          window.dispatchEvent(new Event("rides4u:activeCleared"));
        }
        if (refreshedRide.status === "completed" || refreshedRide.status === "cancelled") {
          toast.success("All done! Ride completed successfully!");
          setTimeout(() => navigate({ to: "/driver" }), 1000);
        } else {
          setPaymentStatus("pending");
          toast.error("There was an issue completing the ride. Please try again.");
        }
      }
    } catch (error) {
      toast.error((error as Error).message || "Couldn't confirm cash payment. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  // Legacy collect function for backward compatibility
  const collect = async (method: "cash" | "online") => {
    if (method === "cash") {
      return confirmCashCollection();
    }
    // For online, check backend verified status (webhook is source of truth).
    setBusy(true);
    try {
      const status = await api.getPaymentStatus(rideId);
      if (status.payment_status === "SUCCESS" || status.payment_status === "paid") {
        toast.success("Payment verified! Money added to your wallet.");
        if (typeof status.amount === "number" && Number.isFinite(status.amount)) {
          setPaymentAmountHint(status.amount);
        }
        setPaymentStatus("paid");
        setShowCollect(false);
        // BUG FIX: Pass force=true to ensure immediate refresh
        void refreshRide(true);
        setTimeout(() => navigate({ to: "/driver" }), 1500);
      } else {
        toast.info("Payment is pending confirmation. Please wait for successful capture.");
      }
    } catch (error) {
      toast.error((error as Error).message || "Couldn't verify payment. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const [showCannotCancel, setShowCannotCancel] = useState(false);

  const cancelRide = async () => {
    // Check if ride has already started - cannot cancel after pickup
    const cannotCancelStatuses = ["started", "otp_verified", "payment_required", "payment_confirmed", "reached_destination", "completed"];
    if (cannotCancelStatuses.includes(stage)) {
      setShowCannotCancel(true);
      setTimeout(() => setShowCannotCancel(false), 3000);
      return;
    }

    // NEW: Confirm dialog with clear message
    if (!confirm("Are you sure you want to cancel this ride?\n\nThis action cannot be undone.")) return;
    setBusy(true);
    try {
      await api.driverCancelRide(rideId);
      toast.success("Ride cancelled. Returning to home...");
      navigate({ to: "/driver" });
    } catch (error) {
      toast.error((error as Error).message || "Couldn't cancel the ride. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  // Normalize stage to handle both uppercase (backend) and lowercase (frontend) statuses
  const rawStage = ride?.status ?? "loading";
  const stage = rawStage.toLowerCase();

  if (!ride && rideWsState === "closed") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
        <div className="text-4xl mb-4">📡</div>
        <h2 className="text-xl font-bold mb-2">Connection Lost</h2>
        <p className="text-muted-foreground mb-6 max-w-xs">
          We can't sync with the server. Please check your internet connection.
        </p>
        <Btn onClick={() => window.location.reload()}>Reconnect</Btn>
      </div>
    );
  }

  return (
    <div className="min-h-screen pb-10">
      <Navbar to="/driver" wsMsg={notificationMsg} />

      <div className="px-4 mt-2">
        <div className="space-y-3 rounded-2xl glass p-4">
          <Row dot="bg-white" label="Pickup Location" value={ride?.pickup?.address || (ride?.pickup?.lat ? `${ride.pickup.lat.toFixed(4)}, ${ride.pickup.lng.toFixed(4)}` : "-")} />
          <Row dot="bg-[#5aa9ff]" label="Drop Location" value={ride?.drop?.address || (ride?.drop?.lat ? `${ride.drop.lat.toFixed(4)}, ${ride.drop.lng.toFixed(4)}` : "-")} />
          {/* Total Earning - Driver only sees total, not fare breakdown */}
          <div className="rounded-xl bg-surface-2 p-4 hairline">
            <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-2">Your Earning</div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">Total you will receive</span>
              <span className="font-display text-2xl font-bold text-emerald-400">
                Rs {ride?.fare?.total?.toFixed(0) ?? "-"}
              </span>
            </div>
          </div>

          {/* Passenger Info - Phone masked for privacy */}
          <div className="flex items-center justify-between rounded-xl bg-surface-2 p-3 hairline">
            <div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">Passenger</div>
              <div className="text-sm font-medium">{ride?.passenger?.name ?? "-"}</div>
              <div className="text-xs text-muted-foreground">
                {maskPhoneNumber(ride?.passenger?.phone) ?? ""}
              </div>
            </div>
          </div>
        </div>

        <div className={isFullScreenMap ? "fixed inset-0 z-50 h-screen w-screen bg-background" : "relative mt-3 h-[42vh] min-h-[280px] overflow-hidden rounded-2xl"}>
          <MapCanvas
            theme={theme}
            driver={ride?.driver?.location ? { ...ride.driver.location, vehicle: ride.vehicle } : undefined}
            pickup={ride?.pickup ?? null}
            drop={ride?.drop ?? null}
            polyline={ride?.expected_route_polyline || ride?.polyline}
            driverToPickupPolyline={ride?.driver_to_pickup_polyline || ride?.driverToPickupPolyline}
            showDriverLeg={["accepted", "driver_arriving", "driver_arrived"].includes(stage)}
          />
          {!isFullScreenMap && (
            <>
              <div className="absolute right-3 top-3 rounded-full glass px-3 py-1.5 text-xs">
                <span className="pulse-dot" />
                {stage.replace("_", " ")}
              </div>
              <button 
                onClick={() => {
                  const url = `https://www.google.com/maps/dir/?api=1&origin=${ride?.pickup?.lat},${ride?.pickup?.lng}&destination=${ride?.drop?.lat},${ride?.drop?.lng}&travelmode=driving`;
                  window.open(url, '_blank');
                }}
                className="absolute bottom-3 right-3 rounded-xl glass px-4 py-2 text-xs font-bold shadow-lg hover:bg-white/20"
              >
                Open in Google Maps
              </button>
            </>
          )}

          {isFullScreenMap && (
            <>
              <button 
                onClick={() => setIsFullScreenMap(false)}
                className="absolute left-4 top-10 grid h-12 w-12 place-items-center rounded-full glass bg-background/50 text-xl shadow-lg hover:bg-background/80"
              >
                ✕
              </button>
              
              <div className="absolute bottom-6 left-4 right-4 flex flex-col gap-3">
                {/* Directions/Steps Panel */}
                <div className="max-h-[30vh] overflow-y-auto rounded-2xl glass p-4 shadow-2xl backdrop-blur-xl bg-background/90">
                  <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-3 font-bold">Directions</div>
                  <div className="space-y-4">
                    {ride?.status === "accepted" || ride?.status === "driver_arriving" ? (
                      // Show steps to pickup
                      ride.driver_to_pickup_steps && ride.driver_to_pickup_steps.length > 0 ? (
                        ride.driver_to_pickup_steps.map((step, i) => (
                          <div key={i} className="flex gap-3 items-start border-l-2 border-emerald-500/30 pl-3 py-1">
                            <div className="text-sm leading-tight text-white/90">{step.instruction || step.maneuver}</div>
                            <div className="text-[10px] text-muted-foreground whitespace-nowrap mt-1">{step.distance ? `${(step.distance/1000).toFixed(1)}km` : ""}</div>
                          </div>
                        ))
                      ) : (
                        <div className="text-sm text-muted-foreground">Head to pickup location</div>
                      )
                    ) : (
                      // Show steps to drop
                      ride?.expected_route_steps && ride.expected_route_steps.length > 0 ? (
                        ride.expected_route_steps.map((step, i) => (
                          <div key={i} className="flex gap-3 items-start border-l-2 border-[#5aa9ff]/30 pl-3 py-1">
                            <div className="text-sm leading-tight text-white/90">{step.instruction || step.maneuver}</div>
                            <div className="text-[10px] text-muted-foreground whitespace-nowrap mt-1">{step.distance ? `${(step.distance/1000).toFixed(1)}km` : ""}</div>
                          </div>
                        ))
                      ) : (
                        <div className="text-sm text-muted-foreground">Route to drop location</div>
                      )
                    )}
                  </div>
                </div>

                {/* Locations Panel */}
                <div className="rounded-2xl glass p-4 shadow-2xl backdrop-blur-xl bg-background/90">
                  <div className="space-y-3">
                    <div className="flex items-start gap-3">
                      <span className="mt-1 h-2.5 w-2.5 rounded-full bg-white ring-2 ring-background flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">Pickup Location</div>
                        <div className="text-sm font-medium leading-relaxed truncate">{ride?.pickup?.address ?? "-"}</div>
                      </div>
                    </div>
                    <div className="flex items-start gap-3">
                      <span className="mt-1 h-2.5 w-2.5 rounded-full bg-[#5aa9ff] ring-2 ring-background flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">Drop Location</div>
                        <div className="text-sm font-medium leading-relaxed truncate">{ride?.drop?.address ?? "-"}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* ACTION BUTTONS - Below the Map - ALL WHITE BUTTONS */}
        <div className="mt-4 grid gap-2">
          {/* Stage: Driver Assigned / Accepted - Arrived at pickup + Cancel */}
          {(stage === "accepted" || stage === "driver_arriving") && (
            <>
              <Btn className="w-full bg-foreground text-background hover:bg-foreground/90" onClick={arrived} disabled={busy}>
                I have arrived at pickup
              </Btn>
              <Btn variant="outline" className="w-full border-white/30 text-white hover:bg-white/10" onClick={cancelRide} disabled={busy}>
                Cancel ride
              </Btn>
            </>
          )}

          {/* Stage: Driver Arrived - OTP verification */}
          {(stage === "driver_arrived") && !codeVerified && (
            <>
              <div className={`rounded-2xl bg-gradient-to-br ${codeError ? 'from-rose-500/20 to-red-600/20 border-2 border-rose-500/50' : 'from-white/10 to-white/5 border border-white/20'} p-5 transition-all duration-300`}>
                <div className="text-center mb-4">
                  <div className={`font-bold text-lg uppercase tracking-wider ${codeError ? 'text-rose-400' : 'text-white'}`}>
                    {codeError ? 'Wrong code! Try again' : 'Enter the code to start the ride'}
                  </div>
                  <div className="text-white/70 text-sm mt-1">
                    Ask passenger for their 4-digit OTP code
                  </div>
                </div>
                <div className="flex justify-center mb-4">
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder="••••"
                    value={code}
                    onChange={(event) => {
                      setCode(event.target.value.replace(/\D/g, "").slice(0, 4));
                      if (codeError) setCodeError(false);
                    }}
                    className={`w-48 text-center bg-background/80 border-2 rounded-xl px-4 py-4 font-display text-4xl font-bold tracking-[0.3em] placeholder:text-white/30 focus:outline-none focus:ring-2 transition-all duration-300 ${
                      codeError
                        ? 'border-rose-500 text-rose-400 focus:border-rose-400 focus:ring-rose-500/30'
                        : 'border-white/50 text-white focus:border-white focus:ring-white/30'
                    }`}
                  />
                </div>
                {codeError && (
                  <div className="text-center mb-3">
                    <span className="text-rose-400 text-sm font-medium">Code doesn't match. Ask passenger again.</span>
                  </div>
                )}
                <Btn className="w-full font-bold bg-foreground text-background hover:bg-foreground/90" onClick={start} disabled={busy}>
                  {codeError ? 'Try again' : 'Start trip'}
                </Btn>
              </div>
              <Btn variant="outline" className="w-full border-white/30 text-white hover:bg-white/10" onClick={cancelRide} disabled={busy}>
                Cancel ride
              </Btn>
            </>
          )}


          {/* OTP Verified / Started / In Progress - Show Reached destination button ONLY after trip started */}
          {/* Only show this AFTER backend confirms status is started/otp_verified - not just local codeVerified */}
          {["started", "otp_verified"].includes(stage) && (
            <>
              <div className="rounded-2xl bg-white/10 border border-white/20 p-5 text-center">
                <div className="text-5xl mb-2">✓</div>
                <div className="text-white font-bold text-lg uppercase tracking-wider">
                  Trip in Progress
                </div>
                <div className="text-white/80 text-sm mt-1">
                  Drive safely to destination
                </div>
              </div>
              <Btn className="w-full bg-foreground text-background hover:bg-foreground/90" onClick={reachedDest} disabled={busy}>
                Reached destination
              </Btn>
              <Btn variant="outline" className="w-full border-white/30 text-white hover:bg-white/10" onClick={cancelRide} disabled={busy}>
                Cancel ride
              </Btn>
            </>
          )}

          {/* Show loading state while waiting for backend to confirm trip started */}
          {codeVerified && !["started", "otp_verified"].includes(stage) && !["driver_arrived"].includes(stage) && (
            <>
              <div className="rounded-2xl bg-amber-500/20 border border-amber-500/30 p-4 text-center">
                <div className="text-amber-400 font-bold">Starting trip...</div>
                <div className="text-sm text-amber-400/70 mt-1">Please wait</div>
              </div>
            </>
          )}


          {/* Stage: Payment required - show payment collection trigger */}
          {["reached_destination", "payment_required"].includes(stage) && !paymentAlreadySettled && (
            <>
              <div className="rounded-2xl bg-white/10 border border-white/20 p-4 text-center">
                <div className="text-white font-bold">Destination Reached!</div>
                <div className="text-sm text-white/70 mt-1">Please confirm payment collection</div>
              </div>
              <Btn className="w-full bg-foreground text-background hover:bg-foreground/90" onClick={() => setShowCollect(true)} disabled={busy}>
                Collect Payment
              </Btn>
            </>
          )}

          {/* Stage: Payment confirmed - show finalizing message while waiting for completed */}
          {stage === "payment_confirmed" && (
            <>
              <div className="rounded-2xl bg-amber-500/20 border border-amber-500/50 p-4 text-center">
                <div className="text-4xl mb-2 animate-pulse">⏳</div>
                <div className="text-amber-400 font-bold">Finalizing Ride...</div>
                <div className="text-sm text-white/70 mt-1">Please wait while we complete the ride</div>
              </div>
            </>
          )}

          {/* FALLBACK: Show generic status when no buttons match but ride exists */}
          {ride && !["driver_arriving", "driver_arrived", "started", "payment_required", "payment_confirmed", "reached_destination", "otp_verified", "accepted"].includes(stage) && stage !== "loading" && !codeVerified && (
            <>
              <div className="rounded-2xl bg-white/10 border border-white/20 p-4 text-center">
                <div className="text-white/70">Status: {stage.replace("_", " ")}</div>
              </div>
              {/* Always show cancel for early stages */}
              {["requested", "searching", "accepted"].includes(stage) && (
                <Btn variant="outline" className="w-full border-white/30 text-white hover:bg-white/10" onClick={cancelRide} disabled={busy}>
                  Cancel ride
                </Btn>
              )}
              {/* Show reached destination for any in-progress ride stages - EXCLUDE all pre-start stages */}
              {!["requested", "searching", "accepted", "cancelled", "completed", "driver_arriving", "driver_arrived", "waiting", "waiting_for_passenger", "pickup", "payment_required", "payment_confirmed"].includes(stage) && (
                <Btn className="w-full bg-foreground text-background hover:bg-foreground/90" onClick={reachedDest} disabled={busy}>
                  Reached destination
                </Btn>
              )}
            </>
          )}
        </div>
      </div>

      {/* Cannot Cancel Popup Notification */}
      <AnimatePresence>
        {showCannotCancel && (
          <motion.div
            initial={{ opacity: 0, y: -50 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -50 }}
            className="fixed top-20 left-1/2 -translate-x-1/2 z-50 bg-rose-500/90 text-white px-6 py-4 rounded-2xl shadow-2xl backdrop-blur-md"
          >
            <div className="text-center">
              <div className="text-2xl mb-1">⚠️</div>
              <div className="font-bold">Cannot cancel ride</div>
              <div className="text-sm text-white/80 mt-1">Ride has already started</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showCollect && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-30 flex items-end justify-center bg-background/70 backdrop-blur-sm"
            onClick={() => setShowCollect(false)}
          >
            <motion.div
              initial={{ y: 60 }}
              animate={{ y: 0 }}
              exit={{ y: 60 }}
              transition={{ type: "spring", damping: 22 }}
              onClick={(event) => event.stopPropagation()}
              className="w-full max-w-md rounded-t-3xl glass p-5 pb-8"
            >
              <div className="mx-auto mb-4 h-1.5 w-12 rounded-full bg-foreground/20" />
              <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Complete trip</div>
              <div className="font-display text-2xl font-bold">Collect payment - Rs {collectAmount.toFixed(0)}</div>

              {/* NEW: Cash Payment Confirmation Popup - per requirements */}
              {(statusLower === "payment_required" || !ride?.paymentMethod || ride?.paymentMethod === "cash") ? (
                <div className="mt-4 rounded-xl bg-white/10 border border-white/20 p-4">
                  {paymentStatus === "paid" ? (
                    <div className="text-center">
                      <div className="text-white font-bold text-lg">Payment Confirmed!</div>
                      <div className="text-sm text-white/70 mt-1">Cash payment added to your Earnings</div>
                    </div>
                  ) : (
                    <>
                      {/* Task 1: Driver Cash Confirmation Popup */}
                      <div className="text-center mb-4">
                        <div className="text-4xl mb-2">💰</div>
                        <div className="font-bold text-lg text-white">Have you collected payment from passenger?</div>
                        <div className="text-sm text-white/70 mt-1">Amount: Rs {collectAmount.toFixed(0)}</div>
                      </div>

                      <div className="grid grid-cols-2 gap-3 mt-4">
                        {/* Task 1: "Yes, I have collected" button - WHITE */}
                        <Btn
                          className="w-full bg-foreground text-background hover:bg-foreground/90"
                          onClick={confirmCashCollection}
                          disabled={busy}
                        >
                          ✅ Yes, I have collected
                        </Btn>

                        {/* Task 1: "Not yet" button - WHITE OUTLINE */}
                        <Btn
                          variant="outline"
                          className="w-full border-white/50 text-white hover:bg-white/10"
                          onClick={() => setShowCollect(false)}
                          disabled={busy}
                        >
                          ❌ Not yet
                        </Btn>
                      </div>

                      <div className="mt-3 text-xs text-white/50 text-center">
                        Only confirm after receiving cash from passenger
                      </div>
                    </>
                  )}
                </div>
              ) : (
                /* Online Payment Section */
                <div className="mt-4 rounded-xl bg-white/10 border border-white/20 p-4">
                  {paymentStatus === "paid" ? (
                    <div className="text-center">
                      <div className="text-white font-bold text-lg">Payment Received!</div>
                      <div className="text-sm text-white/70 mt-1">Online payment added to your Wallet</div>
                    </div>
                  ) : (
                    <>
                      <div className="text-sm mb-3 text-white/80">Show this QR code to passenger for UPI payment:</div>
                      {qrData ? (
                        <div className="bg-white p-2 rounded-lg mx-auto w-fit hairline shadow-sm">
                          <img
                            src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(qrData)}`}
                            alt="Payment QR Code"
                            className="w-[200px] h-[200px] block"
                            onLoad={() => console.log('QR Code loaded')}
                            onError={(e) => {
                              console.error('QR Code failed to load');
                              e.currentTarget.src = ''; // Clear broken image
                            }}
                          />
                          <div className="text-[10px] text-black/40 text-center mt-1 font-mono">{qrData.slice(0, 20)}...</div>
                        </div>
                      ) : (
                        <div className="text-white/50 text-sm">Loading QR code...</div>
                      )}
                      <div className="flex gap-2 mt-4">
                        <Btn variant="outline" onClick={checkPaymentStatus} disabled={busy || paymentStatus === "checking"} className="flex-1 border-white/50 text-white hover:bg-white/10">
                          {paymentStatus === "checking" ? "Checking..." : "Check Payment"}
                        </Btn>
                        <Btn onClick={() => collect("online")} disabled={busy} className="flex-1 bg-foreground text-background hover:bg-foreground/90">
                          Confirm Paid
                        </Btn>
                      </div>
                      {paymentStatus === "pending" && (
                        <div className="mt-2 text-xs text-white/50">Passenger hasn't paid yet</div>
                      )}
                    </>
                  )}
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ============================================================
          CHAT SYSTEM - Ride chat component
          ADDED: Floating chat button for driver-passenger communication
          ============================================================ */}
      {ride && (
        <RideChat
          rideId={rideId}
          role="rider"
          rideStatus={ride.status}
        />
      )}
    </div>
  );
}

function Row({ dot, label, value }: { dot: string; label: string; value: string }) {
  return (
    <div className="flex items-start gap-3 rounded-xl bg-surface-2 p-3 hairline">
      <span className={`mt-1 h-2.5 w-2.5 rounded-full ${dot} ring-2 ring-background flex-shrink-0`} />
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">{label}</div>
        <div className="text-sm font-medium leading-relaxed">{value}</div>
      </div>
    </div>
  );
}

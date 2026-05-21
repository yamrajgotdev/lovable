import { createFileRoute, Link, useNavigate, useRouteContext } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Navbar } from "@/components/Navbar";
import { MapCanvas } from "@/components/MapCanvas";
import { Btn } from "@/components/Field";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTheme } from "@/hooks/useTheme";
import { api, auth, type Ride } from "@/lib/api";

// ============================================================
// CHAT SYSTEM - Import chat component
// ADDED: Chat feature for passenger-driver communication
// ============================================================
import { RideChat } from "@/components/RideChat";

export const Route = createFileRoute("/passenger/ride/$rideId")({
  head: ({ params }) => ({ meta: [{ title: `Ride ${params.rideId} - RIDES4U` }] }),
  component: PassengerRide,
});

type RideSocketMessage =
  | { type: "ride_update"; status: string; ride_id: string }
  | { type: "driver_location"; latitude: number; longitude: number; heading?: number }
  // NEW: Payment status sync via WebSocket
  | { type: "payment_update"; notification: { type: string; payment_status: string; payment_method?: string; amount?: number; message?: string } }
  | { type: "notification"; notification: { message?: string } };

const STATE_PRIORITY: Record<string, number> = {
  requested: 0,
  searching: 1,
  searching_driver: 1,  // Backend alias for searching
  accepted: 2,
  driver_assigned: 2,   // Backend alias for accepted
  driver_arriving: 3,
  driver_arrived: 4,    // Fixed: was 5, now sequential
  arrived: 4,           // Backend alias
  otp_verified: 5,      // Fixed: was 6, now sequential
  started: 6,           // Fixed: was 7, now sequential
  in_progress: 6,       // Backend alias for started
  reached_destination: 7, // Fixed: was 8, now sequential
  payment_required: 8,  // Fixed: was 9, now sequential
  payment_confirmed: 9, // Fixed: was 10, now sequential
  completed: 10,        // Fixed: was 11, now sequential
  ride_finished: 10,    // Backend alias
  cancelled: 11,        // Fixed: was 12, now sequential
};

function isForwardProgress(current?: string, incoming?: string): boolean {
  const currentPriority = STATE_PRIORITY[(current || "").toLowerCase()] ?? -1;
  const incomingPriority = STATE_PRIORITY[(incoming || "").toLowerCase()] ?? -1;
  return incomingPriority >= currentPriority;
}

function PassengerRide() {
  const { rideId } = Route.useParams();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { notificationMsg } = useRouteContext({ from: "/passenger/ride/$rideId" }) as any;
  const [ride, setRide] = useState<Ride | null>(null);
  const [method, setMethod] = useState<"cash" | "online" | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [cashCode, setCashCode] = useState<string | null>(null);
  const [showCancelMessage, setShowCancelMessage] = useState(false);
  const [isFullScreenMap, setIsFullScreenMap] = useState(false);
  // NEW: Payment state
  const [paymentStatus, setPaymentStatus] = useState<"pending" | "processing" | "completed" | "failed">("pending");
  const [showPaymentSuccess, setShowPaymentSuccess] = useState(false);
  const RIDE_POLL_INTERVAL = 4000;
  const lastDriverLocAtRef = useRef(0);
  const lastWsEventAtRef = useRef(0);
  const paymentSettled = ride?.status === "completed" || ride?.status === "cancelled";

  const distanceMeters = (aLat: number, aLng: number, bLat: number, bLng: number) => {
    const dx = (aLat - bLat) * 111_320;
    const dy = (aLng - bLng) * 111_320;
    return Math.sqrt(dx * dx + dy * dy);
  };

  // BUG FIX: Validate coordinates to prevent map glitches to Africa (0,0)
  const isValidCoordinate = (lat: number | null | undefined, lng: number | null | undefined): boolean => {
    if (lat === null || lat === undefined || lng === null || lng === undefined) return false;
    if (Number.isNaN(lat) || Number.isNaN(lng)) return false;
    if (lat === 0 && lng === 0) return false; // Reject 0,0 (Africa)
    if (lat < -90 || lat > 90) return false; // Invalid latitude
    if (lng < -180 || lng > 180) return false; // Invalid longitude
    return true;
  };

  const reconcilePaymentState = (incoming: Ride | null) => {
    if (!incoming) return;
    const status = String(incoming.status || "").toLowerCase();
    const paymentStatusField = String(incoming.paymentStatus || (incoming as any).payment_status || "").toLowerCase();

    const paid = ["paid", "success", "payment_confirmed"].includes(paymentStatusField) || ["paid", "success"].includes(status);
    const terminal = ["completed", "cancelled", "payment_confirmed", "failed"].includes(status) || paid;

    // Only show completed UI if ride is ACTUALLY completed (not just reached_destination or payment_confirmed)
    if (status === "completed" || status === "cancelled") {
      // Normalize local representation: mark completed so UI shows terminal screens
      setPaymentStatus("completed");
      setShowPaymentSuccess(true);
      setRide((current) => (incoming ? { ...incoming, status: "completed", paymentStatus: "paid" } as Ride : current));

      // ONLY set pending rating when status is EXACTLY 'completed'
      // Backend requires 'completed' status for rating submission
      if (typeof window !== "undefined" && status === "completed") {
        try { 
          localStorage.setItem("pending_rating_passenger_ride_id", incoming.id);
          localStorage.setItem("pending_rating_timestamp", Date.now().toString());
        } catch {}
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }
    }
  };

  const refreshRide = async (signal?: AbortSignal, force = false) => {
    if (!force && Date.now() - lastWsEventAtRef.current < 1500 && rideWsState === "open") {
      return;
    }
    try {
      const response = await api.ride(rideId, signal);
      // Apply REST state but reconcile payment immediately to avoid UI split
      setRide((current) => {
        if (!current) {
          reconcilePaymentState(response.ride);
          return response.ride;
        }
        if (!isForwardProgress(current.status, response.ride.status)) {
          console.log("[STATE UPDATE] passenger stale_api_ignored", current.status, "->", response.ride.status);
          // still reconcile payment in case REST says paid
          reconcilePaymentState(current);
          return current;
        }
        // REST is source of truth - prefer it over WebSocket state
        const sameStatus = current.status === response.ride.status;
        const samePayment =
          current.paymentStatus === response.ride.paymentStatus &&
          current.paymentMethod === response.ride.paymentMethod;
        const sameDriverLocation =
          current.driver?.location?.lat === response.ride.driver?.location?.lat &&
          current.driver?.location?.lng === response.ride.driver?.location?.lng &&
          current.driver?.location?.heading === response.ride.driver?.location?.heading;
        if (sameStatus && samePayment && sameDriverLocation) {
          reconcilePaymentState(current);
          return current;
        }
        console.log("[STATE UPDATE] passenger api_refresh");
        reconcilePaymentState(response.ride);
        return response.ride;
      });
    } catch (err) {
      // Silently fail - WS updates will resume on timeout
      console.warn("[REST Sync] Failed to fetch ride:", err);
    }
  };

  // Loading state with timeout to ensure UI never gets stuck
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Initial load - fetch once, then rely on WebSocket for updates
  useEffect(() => {
    if (typeof window !== "undefined" && (!auth.token || auth.role !== "passenger")) {
      navigate({ to: "/" });
      return;
    }

    let alive = true;
    const loadTimeout = setTimeout(() => {
      if (alive && isLoading) {
        setIsLoading(false); // Force exit loading state after 10s
        if (!ride) {
          setLoadError("Taking longer than expected. Please check your connection.");
        }
      }
    }, 10000);

    const fetchInitial = async () => {
      try {
        setIsLoading(true);
        setLoadError(null);
        const response = await api.ride(rideId);
        if (!alive) return;
        setRide(response.ride);
      } catch (error) {
        if (alive) {
          const message = (error as Error).message || "Failed to load ride";
          setLoadError(message);
          toast.error(message);
        }
      } finally {
        if (alive) setIsLoading(false);
      }
    };

    fetchInitial();
    // No polling - WebSocket handles real-time updates
    return () => {
      alive = false;
      clearTimeout(loadTimeout);
    };
  }, [rideId, navigate]);

  // Handle ride status changes (WebSocket-driven)
  useEffect(() => {
    if (!ride) return;

    if (ride.status === "cancelled") {
      setShowCancelMessage(true);
      toast.error("Driver cancelled the ride. Finding a new driver for you...");
      setTimeout(() => {
        // Announce clear so global popup and caches are cleaned immediately
        if (typeof window !== "undefined") {
          localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
          window.dispatchEvent(new Event("rides4u:activeCleared"));
        }
        navigate({ to: "/passenger" });
      }, 3000);
    } else if (ride.status === "completed") {
      // ONLY redirect and set pending rating when ride status is EXACTLY 'completed'
      toast.success("Ride completed! Thank you for riding with us.");
      
      // Set pending rating for completed ride (with timestamp)
      if (typeof window !== "undefined") {
        const alreadySubmitted = localStorage.getItem(`rating_submitted_${ride.id}`);
        if (!alreadySubmitted) {
          localStorage.setItem("pending_rating_passenger_ride_id", ride.id);
          localStorage.setItem("pending_rating_timestamp", Date.now().toString());
        }
        // Announce clear so global popup and caches are cleaned immediately
        localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
        window.dispatchEvent(new Event("rides4u:activeCleared"));
      }

      setTimeout(() => {
        navigate({ to: "/passenger" });
        window.location.href = "/passenger";
      }, 2000);
    }
    // Note: We do NOT redirect on 'payment_confirmed' - we wait for 'completed'
  }, [ride?.status, navigate]);

  // Determine if ride is in active state for faster WebSocket retry
  const isActiveRide = !!ride && !["completed", "cancelled", "payment_confirmed", "failed"].includes(ride.status);

  const { state: rideWsState, setSyncComplete } = useWebSocket<RideSocketMessage>(`/ws/ride/${rideId}/`, {
    isActiveContext: isActiveRide, // Fast retry (5-10s) during active rides
    onReconnectSuccess: () => {
      // Auto-refetch ride state after successful reconnect
      console.log("[WS] Reconnect success - refetching ride state");
      void refreshRide(undefined, true);
    },
    onMessage: (message) => {
      console.log("[WS EVENT] passenger ride", message.type);
      lastWsEventAtRef.current = Date.now();
      
      if (message.type === "ride_update") {
        const wsStatus = (message as any).ride?.status?.toLowerCase?.() ?? (message as any).status?.toLowerCase?.();
        if (wsStatus && ["completed", "cancelled", "payment_confirmed", "failed"].includes(wsStatus)) {
          if (typeof window !== "undefined") {
            localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
            window.dispatchEvent(new Event("rides4u:activeCleared"));
          }
        }

        void refreshRide(undefined, true);
        return;
      }

      if (message.type === "driver_location") {
        const now = Date.now();
        if (now - lastDriverLocAtRef.current < 1200) return;
        // BUG FIX: Validate coordinates before accepting to prevent map glitches
        if (!isValidCoordinate(message.latitude, message.longitude)) {
          console.warn("[WebSocket] Ignoring invalid driver coordinates:", message.latitude, message.longitude);
          return;
        }
        lastDriverLocAtRef.current = now;
        setRide((current) => {
          if (!current) return current;
          const next = {
            ...current,
            driver: current.driver
              ? {
                  ...current.driver,
                  location: {
                    lat: message.latitude,
                    lng: message.longitude,
                    heading: message.heading,
                  },
                }
              : current.driver,
          };
          return next;
        });
      }

      if (message.type === "notification") {
        const msg = message.notification?.message;
        if (msg) toast.info(msg);
      }

      if (message.type === "payment_update") {
        const { notification } = message;
        if (notification.payment_status === "SUCCESS") {
          // Normalize local payment flags
          setPaymentStatus("completed");
          setShowPaymentSuccess(true);
          toast.success(notification.message || "Payment successful!");

          // Mark only payment settled locally; avoid forcing overall ride status to 'completed' until REST confirms
          setRide((current) =>
            current ? { ...current, paymentStatus: "paid" } as Ride : current,
          );

          // NOTE: Do NOT set pending rating here - wait for ride_update when status becomes 'completed'
          // Backend requires 'completed' status for rating submission, not just payment success
          if (typeof window !== "undefined") {
            localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
            window.dispatchEvent(new Event("rides4u:activeCleared"));
          }

          // BUG FIX: Pass force=true to bypass throttling
          void refreshRide(undefined, true);
          // One-shot follow-up REST recheck to cover flaky WS delivery/back-end race
          setTimeout(() => void refreshRide(undefined, true), 2000);
        }
      }
    },
    onSyncRequired: () => {
      // Hardened sync safety logic with 5s timeout
      console.log("[SYNC] Started");
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);

      // BUG FIX: Pass force=true to bypass throttling since timestamp was just updated by WebSocket event
      refreshRide(controller.signal, true)
        .catch(() => {
          // REST failed - resume WS updates via timeout fallback
          console.warn("[SYNC] REST sync timed out, resuming WebSocket updates");
        })
        .finally(() => {
          clearTimeout(timeout);
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
      try {
        await refreshRide();
      } catch {
        // Ignore transient polling errors.
      }
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

  const choosePayment = async (paymentMethod: "cash" | "online") => {
    setMethod(paymentMethod);
    setPaymentStatus("processing");
    try {
      // First, set payment method on the ride
      await api.setPaymentMethod(rideId, paymentMethod);
      
      if (paymentMethod === "online") {
        // NEW: Use initiate online payment API
        const response = await api.initiateOnlinePayment(rideId);
        setQr(response.qr_data);
        // Start polling for payment status
        startPaymentPolling();
      } else {
        // Cash payment - just show confirmation
        setPaymentStatus("pending");
        toast.info("Please pay the driver in cash");
      }
    } catch (error) {
      setPaymentStatus("failed");
      toast.error((error as Error).message || "Could not set payment method");
    }
  };

  // Payment is now handled exclusively via WebSocket - no polling needed
  // WebSocket message (payment_update) handles real-time status updates
  const startPaymentPolling = () => {
    // DEPRECATED: WebSocket now handles all payment status updates
    // This function is kept for API compatibility but does nothing
    console.log("[Payment] Using WebSocket for payment status - polling deprecated");
  };

  // Manual payment check now relies on backend verified status/webhook.
  const verifyPayment = async () => {
    try {
      setPaymentStatus("processing");
      const status = await api.getPaymentStatus(rideId);
      if (status.payment_status === "SUCCESS" || status.payment_status === "paid") {
        setPaymentStatus("completed");
        setShowPaymentSuccess(true);
        toast.success("Payment verified.");
        // BUG FIX: Pass force=true to ensure immediate refresh
        void refreshRide(undefined, true);
      } else {
        setPaymentStatus("pending");
        toast.info("Payment is still processing. We'll update automatically.");
      }
    } catch (error) {
      setPaymentStatus("failed");
      toast.error((error as Error).message || "Unable to check payment status");
    }
  };

  const cancel = async () => {
    if (!confirm("Are you sure you want to cancel this ride?")) return;
    try {
      await api.cancelRide(rideId);
      toast.success("Ride cancelled");
      navigate({ to: "/passenger" });
    } catch (error) {
      toast.error((error as Error).message || "Cancel failed");
    }
  };

  // UPDATED: Cancelable statuses - only before ride starts
  const isCancelable = ["requested", "searching", "accepted", "driver_arriving", "driver_arrived"].includes(ride?.status || "");


  // Loading state - show spinner with "Still working..." after timeout
  if (isLoading && !ride) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-400 mb-4"></div>
        <h2 className="text-xl font-bold mb-2">
          {loadError ? "Still working..." : "Loading your ride..."}
        </h2>
        <p className="text-muted-foreground text-sm">
          {loadError ? "Retrying in the background" : "This won't take long"}
        </p>
      </div>
    );
  }

  // Error state with retry button (only show after extended failure)
  if (!ride && (rideWsState === "closed" && loadError)) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-6 text-center">
        <div className="text-4xl mb-4">📡</div>
        <h2 className="text-xl font-bold mb-2">Connection Issue</h2>
        <p className="text-muted-foreground mb-6 max-w-xs">
          {loadError || "We're having trouble loading your ride details. Please check your connection."}
        </p>
        <div className="flex gap-3">
          <Btn onClick={() => window.location.reload()}>Retry Connection</Btn>
          <Btn variant="outline" onClick={() => navigate({ to: "/passenger" })}>Go Home</Btn>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen pb-10">
      <Navbar to="/passenger" wsMsg={notificationMsg} />

      {/* Driver Cancelled Message Overlay */}
      {showCancelMessage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/90 backdrop-blur-md">
          <div className="text-center px-6">
            <div className="text-6xl mb-4">🚗</div>
            <div className="text-rose-400 font-bold text-2xl mb-2">Ride Cancelled</div>
            <div className="text-white/80 text-lg mb-4">Finding a new driver for you...</div>
            <div className="animate-pulse text-muted-foreground">Please wait</div>
          </div>
        </div>
      )}

      <div className="px-4">
        <div className="space-y-3 rounded-2xl glass p-4">
          <div className="flex items-start gap-3">
            <span className="mt-1 h-2.5 w-2.5 rounded-full bg-white ring-2 ring-background flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">Pickup Location</div>
              <div className="text-sm font-medium leading-relaxed">{ride?.pickup.address || (ride?.pickup.lat ? `${ride.pickup.lat.toFixed(4)}, ${ride.pickup.lng.toFixed(4)}` : "-")}</div>
            </div>
          </div>
          <div className="flex items-start gap-3">
            <span className="mt-1 h-2.5 w-2.5 rounded-full bg-[#5aa9ff] ring-2 ring-background flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">Drop Location</div>
              <div className="text-sm font-medium leading-relaxed">{ride?.drop.address || (ride?.drop.lat ? `${ride.drop.lat.toFixed(4)}, ${ride.drop.lng.toFixed(4)}` : "-")}</div>
            </div>
          </div>
        </div>

        <div className={isFullScreenMap ? "fixed inset-0 z-50 h-screen w-screen bg-background" : "relative mt-3 h-[38vh] min-h-[260px] overflow-hidden rounded-2xl"}>
          <MapCanvas
            theme={theme}
            driver={ride?.driver?.location ? { ...ride.driver.location, vehicle: ride.vehicle } : null}
            pickup={ride?.pickup ?? null}
            drop={ride?.drop ?? null}
            polyline={ride?.expected_route_polyline || ride?.polyline}
            driverToPickupPolyline={ride?.driver_to_pickup_polyline || ride?.driverToPickupPolyline}
            showDriverLeg={["accepted", "driver_arriving", "driver_arrived"].includes(ride?.status || "")}
          />
          {!isFullScreenMap && (
            <>
              <div className="absolute right-3 top-3 flex items-center gap-2 rounded-full glass px-3 py-1.5 text-xs">
                <span className="pulse-dot" />
                <span>{ride?.status?.replace("_", " ") ?? "loading"}</span>
                <span className="text-muted-foreground">· live tracking</span>
              </div>
              <button 
                onClick={() => setIsFullScreenMap(true)}
                className="absolute bottom-3 right-3 grid h-10 w-10 place-items-center rounded-full glass text-xl shadow-lg hover:bg-white/20"
              >
                ⛶
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
                        <div className="text-sm text-muted-foreground">Driver is on the way to your location</div>
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
                        <div className="text-sm text-muted-foreground">Route to your destination</div>
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
                        <div className="text-sm font-medium leading-relaxed truncate">{ride?.pickup.address ?? "-"}</div>
                      </div>
                    </div>
                    <div className="flex items-start gap-3">
                      <span className="mt-1 h-2.5 w-2.5 rounded-full bg-[#5aa9ff] ring-2 ring-background flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">Drop Location</div>
                        <div className="text-sm font-medium leading-relaxed truncate">{ride?.drop.address ?? "-"}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Driver</div>
          <div className="mt-2 flex items-center justify-between">
            <div>
              <div className="font-display text-xl font-bold">{ride?.driver?.name ?? "Assigning..."}</div>
              <div className="text-sm text-muted-foreground">{ride?.driver?.phone ?? ""}</div>
            </div>
            <div className="rounded-lg bg-foreground px-3 py-2 font-display text-base font-bold tracking-widest text-background">
              {ride?.driver?.plate ?? "- - -"}
            </div>
          </div>
        </section>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Fare Details</div>
          <div className="mt-3 space-y-2">
            {(() => {
              if (!ride?.fare) return (
                <>
                  <div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Base fare</span><span>Rs -</span></div>
                  <div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Per km</span><span>Rs -</span></div>
                  <div className="flex items-center justify-between text-sm"><span className="text-muted-foreground">Tax</span><span>Rs 0</span></div>
                </>
              );
              const discount = ride.fare.discount || 0;
              const originalTotal = ride.fare.beforeDiscount || (ride.fare.total + discount);
              const tax = ride.fare.tax || 0;
              const perKmRate = ride.fare.perKm || 10;
              // Use route distance calculated by Ola Maps (saved on ride) as source of truth.
              const distanceKm = Math.max(0, Number(ride.distanceKm || 0));
              const distanceFare = distanceKm > 0
                ? Math.max(0, Math.round(distanceKm * perKmRate))
                : Math.max(0, Math.round((ride.fare.total - (ride.fare.base || 0) - tax + discount)));
              const baseFare = Math.max(0, Math.round(originalTotal - distanceFare - tax));
              return (
                <>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Base fare</span>
                    <span>Rs {ride.fare.base?.toFixed?.(0) || baseFare}</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      Per km ({perKmRate} Rs/km{distanceKm > 0 ? ` x ${distanceKm.toFixed(1)} km` : ""})
                    </span>
                    <span>Rs {distanceFare.toFixed(0)}</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">Tax</span>
                    <span>Rs {tax.toFixed(0)}</span>
                  </div>
                  {discount > 0 && (
                    <>
                      <div className="flex items-center justify-between border-t border-border/50 pt-1 mt-1 text-sm">
                        <span className="text-muted-foreground">Subtotal</span>
                        <span>Rs {originalTotal.toFixed(0)}</span>
                      </div>
                      <div className="flex items-center justify-between text-sm text-emerald-400">
                        <span>Discount</span>
                        <span>- Rs {discount.toFixed(0)}</span>
                      </div>
                    </>
                  )}
                </>
              );
            })()}
            <div className="flex items-center justify-between border-t border-border pt-2 mt-2">
              <span className="font-semibold">Total to pay</span>
              <span className="font-display text-2xl font-bold text-emerald-400">
                Rs {ride?.fare?.total?.toFixed(0) ?? "-"}
              </span>
            </div>
          </div>
          <div className="mt-3 text-xs text-muted-foreground">
            You will pay this amount to the driver upon reaching your destination.
          </div>
        </section>

        {/* Payment section - show only during payment stages */}
        {(["reached_destination", "payment_required", "payment_confirmed"].includes(ride?.status || "")) && !showPaymentSuccess && !paymentSettled && (
        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground mb-3">Complete Payment</div>

          {/* Payment method selection - show only if not yet selected */}
          {!method && (
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => choosePayment("online")}
                className="lift press rounded-xl p-4 text-left transition bg-surface-2 hairline hover:bg-elevated"
              >
                <div className="font-semibold">Pay Online</div>
                <div className="text-xs text-muted-foreground">Scan QR & pay via Razorpay</div>
              </button>
              <button
                onClick={() => choosePayment("cash")}
                className="lift press rounded-xl p-4 text-left transition bg-surface-2 hairline hover:bg-elevated"
              >
                <div className="font-semibold">Pay Cash</div>
                <div className="text-xs text-muted-foreground">Pay driver directly</div>
              </button>
            </div>
          )}

          {/* Online payment QR display */}
          {method === "online" && qr && paymentStatus !== "completed" && (
            <div className="mt-4">
              <div className="flex flex-col items-center gap-3 rounded-xl bg-white p-4">
                <QRCodeSVG value={qr} size={200} />
                <div className="text-xs font-medium text-black">Scan & pay via UPI</div>
                <div className="text-[10px] text-black/50 text-center">Amount: Rs {ride?.fare?.total?.toFixed(0)}</div>
              </div>
              {/* Automatic payment confirmation - no manual verify button */}
              <div className="mt-3 text-center text-xs text-muted-foreground">
                {paymentStatus === "processing" ? "Processing payment..." : "Payment will be confirmed automatically"}
              </div>
            </div>
          )}

          {/* Cash payment instructions */}
          {method === "cash" && (
            <div className="mt-4 rounded-xl bg-surface-2 p-4 hairline">
              <div className="text-sm font-medium mb-2">Cash Payment</div>
              <div className="text-sm text-muted-foreground">Please pay Rs {ride?.fare?.total?.toFixed(0)} to the driver.</div>
              <div className="mt-2 text-xs text-amber-400">Waiting for driver to confirm...</div>
            </div>
          )}

          {/* Payment processing indicator */}
          {paymentStatus === "processing" && (
            <div className="mt-4 flex items-center justify-center gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-400">
              <span className="animate-spin">⏳</span>
              <span>Processing payment...</span>
            </div>
          )}

          {/* Change payment method button */}
          {method && paymentStatus !== "completed" && (
            <button
              onClick={() => { setMethod(null); setQr(null); setPaymentStatus("pending"); }}
              className="mt-3 text-xs text-muted-foreground hover:text-foreground underline"
            >
              Change payment method
            </button>
          )}
        </section>
        )}

        {/* NEW: Payment success overlay */}
        {(showPaymentSuccess || paymentSettled) && (
          <section className="mt-4 rounded-2xl bg-emerald-500/20 border border-emerald-500/50 p-6 text-center">
            <div className="text-4xl mb-2">✓</div>
            <div className="text-emerald-400 font-bold text-lg uppercase tracking-wider mb-1">Payment Successful</div>
            <div className="text-white/80 text-sm">Thank you for riding with us!</div>
          </section>
        )}

        {/* NEW: Finalizing ride message for payment_confirmed status */}
        {ride?.status === "payment_confirmed" && (
          <section className="mt-4 rounded-2xl bg-amber-500/20 border border-amber-500/50 p-6 text-center">
            <div className="text-4xl mb-2 animate-pulse">⏳</div>
            <div className="text-amber-400 font-bold text-lg uppercase tracking-wider mb-1">Finalizing Ride</div>
            <div className="text-white/80 text-sm">Please wait while we complete your ride...</div>
          </section>
        )}

        {/* Ride Code Section - visible when driver has arrived at pickup */}
        {ride?.code && ride?.status === "driver_arrived" && (
          <section className="mt-4 rounded-2xl bg-gradient-to-br from-amber-500/20 to-yellow-600/20 border-2 border-amber-500/50 p-6">
            <div className="text-center">
              <div className="text-amber-400 font-bold text-lg uppercase tracking-wider mb-2">
                Meet your driver
              </div>
              <div className="text-white/80 text-sm mb-6">
                Share this code with your driver to start the ride
              </div>
              <div className="inline-block rounded-2xl bg-background/90 backdrop-blur px-10 py-6 border-2 border-amber-500/30 shadow-lg shadow-amber-500/20">
                <div className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">Pickup Code</div>
                <div className="font-display text-6xl font-bold tracking-[0.5em] text-yellow-400 drop-shadow-lg">
                  {ride.code}
                </div>
              </div>
              <div className="text-xs text-white/60 mt-4">
                Share this 4-digit code with your driver to start the ride
              </div>
            </div>
          </section>
        )}

        {isCancelable && (
          <Btn variant="outline" className="mt-4 w-full" onClick={cancel}>
            Cancel ride
          </Btn>
        )}

        {/* ============================================================
            CHAT SYSTEM - Ride chat component
            ADDED: Floating chat button for passenger-driver communication
            ============================================================ */}
        {ride && (
          <RideChat
            rideId={rideId}
            role="passenger"
            rideStatus={ride.status}
          />
        )}
      </div>
    </div>
  );
}

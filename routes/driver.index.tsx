import { createFileRoute, Link, useNavigate, useRouteContext } from "@tanstack/react-router";
import { useEffect, useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Navbar } from "@/components/Navbar";
import { Btn } from "@/components/Field";
import { MapCanvas } from "@/components/MapCanvas";
import { RatingPrompt } from "@/components/RatingPrompt";
import { OnboardingModal } from "@/components/OnboardingModal";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTheme } from "@/hooks/useTheme";
import { useTranslation } from "@/hooks/useTranslation";
import { api, auth, type IncomingRide, type Ride } from "@/lib/api";

export const Route = createFileRoute("/driver/")({
  head: () => ({ meta: [{ title: "Driver - RIDES4U" }] }),
  component: DriverHome,
});

function normalizeIncomingRidePayload(msg: any): IncomingRide | null {
  if (!msg) return null;

  const source =
    msg.type === "notification"
      ? (msg.data || msg.payload || {})
      : (msg.data?.ride_id || msg.data?.pickup || msg.data?.pickup_address ? msg.data : msg);
  const pickup = source.pickup || {};
  const drop = source.drop || {};

  const rideId = source.ride_id ?? source.rideId ?? msg.ride_id;
  if (!rideId) return null;

  const pickupLat = Number(pickup.lat ?? source.pickup_lat ?? 0);
  const pickupLng = Number(pickup.lng ?? source.pickup_lng ?? 0);
  const dropLat = Number(drop.lat ?? source.drop_lat ?? 0);
  const dropLng = Number(drop.lng ?? source.drop_lng ?? 0);
  const pickupToDropKm = Number(
    source.pickup_to_drop_km ??
      source.trip_distance_km ??
      source.distance_km ??
      source.route_distance_km ??
      source.distances?.pickupToDropKm ??
      0
  );

  return {
    id: String(rideId),
    pickup: {
      lat: Number.isFinite(pickupLat) ? pickupLat : 0,
      lng: Number.isFinite(pickupLng) ? pickupLng : 0,
      address: pickup.address ?? source.pickup_address ?? "",
    },
    drop: {
      lat: Number.isFinite(dropLat) ? dropLat : 0,
      lng: Number.isFinite(dropLng) ? dropLng : 0,
      address: drop.address ?? source.drop_address ?? "",
    },
    vehicle: (source.vehicle_type ?? source.vehicle ?? "auto") as IncomingRide["vehicle"],
    fare: {
      total: Number(source.estimated_fare ?? source.fare?.total ?? 0),
      base: Number(source.fare?.base ?? 0),
      perKm: Number(source.fare?.perKm ?? source.fare?.per_km ?? 0),
      perMinute: Number(source.fare?.perMinute ?? source.fare?.per_minute ?? 0),
      distanceKm: Number(source.fare?.distanceKm ?? pickupToDropKm ?? 0),
      distanceFare: Number(source.fare?.distanceFare ?? 0),
      timeFare: Number(source.fare?.timeFare ?? 0),
      subtotal: Number(source.fare?.subtotal ?? 0),
      tax: Number(source.fare?.tax ?? 0),
      discount: Number(source.fare?.discount ?? 0),
    },
    distances: {
      driverToPickupKm: Number(
        source.driver_to_pickup_km ??
        source.driverToPickupKm ??
        source.distances?.driverToPickupKm ??
        0
      ),
      pickupToDropKm: Number.isFinite(pickupToDropKm) ? pickupToDropKm : 0,
    },
    status: "pending",
    otp: String(source.otp ?? "0000"),
  };
}

function DriverHome() {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { t } = useTranslation();
  const { notificationMsg } = useRouteContext({ from: "/driver/" }) as any;
  const [online, setOnline] = useState(false);
  const [stats, setStats] = useState<{
    earningsToday: number;
    cashEarningsToday: number;
    onlineEarningsToday: number;
    totalRides: number;
    todayRides: number;
    walletBalance: number;
    rating: number;
  } | null>(null);
  const [incoming, setIncoming] = useState<IncomingRide | null>(null);
  const [busy, setBusy] = useState(false);
  // NEW: Track active ride
  const [activeRide, setActiveRide] = useState<Ride | null>(null);
  const [pendingRatingRideId, setPendingRatingRideId] = useState<string | null>(null);
  const [showRatingPrompt, setShowRatingPrompt] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);

  // Check for onboarding flag on mount
  useEffect(() => {
    const shouldShowOnboarding = localStorage.getItem("show_onboarding");
    if (shouldShowOnboarding === "true") {
      setShowOnboarding(true);
      localStorage.removeItem("show_onboarding");
    }
  }, []);

  const handleOnboardingClose = () => {
    setShowOnboarding(false);
  };

  // Track driver's live location for map display
  const [driverLocation, setDriverLocation] = useState<{ lat: number; lng: number; heading?: number } | null>(null);
  const watchIdRef = useRef<number | null>(null);
  const locationIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const latestLocationRef = useRef<{ lat: number; lng: number; heading?: number; timestamp: number } | null>(null);
  const locationWsRef = useRef<WebSocket | null>(null);
  const locationWsIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    // Delay auth check slightly to allow localStorage to sync after login
    const checkAuth = setTimeout(() => {
      if (typeof window !== "undefined" && (!auth.token || auth.role !== "rider")) {
        navigate({ to: "/" });
        return;
      }

      const user = auth.user;
      if (user && user.verification_status && user.verification_status !== "approved") {
        navigate({ to: "/driver/pending" });
        return;
      }

      api.driverStats().then(setStats).catch(() =>
        setStats({ earningsToday: 0, cashEarningsToday: 0, onlineEarningsToday: 0, totalRides: 0, todayRides: 0, walletBalance: 0, rating: 0 }),
      );
      api.driverStatus().then((res) => setOnline(!!res.is_online)).catch(() => {});

      // NEW: Check for active ride (only redirect if ride is truly in progress)
      api.driverActiveRide().then((res) => {
        const recentlyClearedAt = Number(
          typeof window !== "undefined" ? localStorage.getItem("rides4u_active_ride_cleared") || 0 : 0
        );
        const recentlyCleared = Date.now() - recentlyClearedAt < 30000;
        if (recentlyCleared) {
          setActiveRide(null);
          return;
        }
        if (res.ride) {
          // Only accept truly-active statuses (backend is canonical)
          const activeStatuses = new Set([
            "requested",
            "searching_driver",
            "searching",
            "accepted",
            "driver_arriving",
            "driver_arrived",
            "otp_verified",
            "started",
            "reached_destination",
            "payment_required",
          ]);

          const status = String(res.ride.status || "").toLowerCase();
          if (activeStatuses.has(status)) {
            setActiveRide(res.ride);
            navigate({ to: "/driver/ride/$rideId", params: { rideId: res.ride.id } });
          } else {
            // Terminal ride - ensure we don't resurrect popup
            if (typeof window !== "undefined") {
              localStorage.setItem("rides4u_active_ride_cleared", Date.now().toString());
              window.dispatchEvent(new Event("rides4u:activeCleared"));
            }
            setActiveRide(null);
          }
        }
      }).catch(() => {
        // Ignore errors
      });
    }, 100); // Small delay to ensure localStorage is synced

    return () => clearTimeout(checkAuth);
  }, [navigate]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    
    // Delay showing rating prompt to allow page to settle and any active ride state to clear
    const timer = setTimeout(() => {
      const rideId = localStorage.getItem("pending_rating_driver_ride_id");
      if (rideId) {
        // Check if rating was already submitted for this ride
        const alreadySubmitted = localStorage.getItem(`rating_submitted_${rideId}`);
        if (alreadySubmitted) {
          // Clear the pending rating since it was already submitted
          localStorage.removeItem("pending_rating_driver_ride_id");
          localStorage.removeItem("pending_rating_timestamp");
          return;
        }
        
        // Check if the pending rating was set recently (within 60 seconds)
        // This ensures the popup only shows on redirect from completed ride, not on page reloads
        const pendingTimestamp = localStorage.getItem("pending_rating_timestamp");
        if (pendingTimestamp) {
          const setTime = parseInt(pendingTimestamp, 10);
          const now = Date.now();
          if (now - setTime > 60000) {
            // More than 60 seconds have passed, clear the pending rating
            localStorage.removeItem("pending_rating_driver_ride_id");
            localStorage.removeItem("pending_rating_timestamp");
            return;
          }
        } else {
          // No timestamp set, this is an old pending rating from before the fix - clear it
          localStorage.removeItem("pending_rating_driver_ride_id");
          return;
        }
        
        setPendingRatingRideId(rideId);
        setShowRatingPrompt(true);
        // Clear timestamp so popup won't show again on reload
        localStorage.removeItem("pending_rating_timestamp");
      }
    }, 500); // Small delay to allow state to settle

    // Listen for global clears (terminal ride) to immediately hide popup
    const handler = () => setActiveRide(null);
    const storageHandler = (e: StorageEvent) => {
      if (e.key === "rides4u_active_ride_cleared") handler();
    };
    window.addEventListener("storage", storageHandler);
    window.addEventListener("rides4u:activeCleared", handler as EventListener);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("storage", storageHandler);
      window.removeEventListener("rides4u:activeCleared", handler as EventListener);
    };
  }, []);

  useEffect(() => {
    if (driverLocation) {
      latestLocationRef.current = { ...driverLocation, timestamp: Date.now() };
    }
  }, [driverLocation]);

  useEffect(() => {
    if (!online) {
      setIncoming(null);
    }
  }, [online]);

  // WebSocket for incoming rides (single resilient connection while online)
  useWebSocket(online ? "/ws/driver/notifications/" : null, {
    isActiveContext: online,
    onMessage: (msg: any) => {
      if (activeRide && !["completed", "cancelled", "payment_confirmed"].includes(activeRide.status)) {
        return;
      }
      console.log("[Driver] WS notification:", msg?.type, msg);
      if (msg?.type === "ride_taken") {
        const takenRideId = String(msg?.data?.ride_id ?? msg?.ride_id ?? "");
        setIncoming((prev) => {
          if (prev && takenRideId && prev.id === takenRideId) {
            toast.info("Another driver accepted this ride. Looking for new requests...");
            return null;
          }
          return prev;
        });
        return;
      }

      const supportedType =
        msg?.type === "ride_request" ||
        msg?.type === "broadcast_ride_request" ||
        msg?.type === "broadcast_ride" ||
        msg?.type === "nearby_ride_request" ||
        (msg?.type === "notification" &&
          (
            msg?.data?.type === "ride_request" ||
            msg?.data?.type === "broadcast_ride_request" ||
            msg?.data?.type === "broadcast_ride" ||
            msg?.data?.type === "nearby_ride_request"
          ));
      if (!supportedType) return;

      const ride = normalizeIncomingRidePayload(msg);
      if (ride) {
        // Filter by vehicle type - only show rides matching driver's vehicle
        const driverVehicle = auth.user?.vehicle_type;
        if (driverVehicle && ride.vehicle !== driverVehicle) {
          console.log(`[Driver] Ignoring ride ${ride.id} - vehicle type mismatch (ride: ${ride.vehicle}, driver: ${driverVehicle})`);
          return;
        }
        setIncoming(ride);
        // Notify driver with sound and vibration
        notifyDriver();
      }
    },
  });

  // Play buzzing sound and vibrate to notify driver of new ride
  const notifyDriver = () => {
    // Play buzzing sound
    try {
      const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const oscillator = audioCtx.createOscillator();
      const gainNode = audioCtx.createGain();

      oscillator.connect(gainNode);
      gainNode.connect(audioCtx.destination);

      // Buzzing pattern: low frequency, pulsing
      oscillator.frequency.value = 200; // Low buzz frequency
      oscillator.type = 'sawtooth';

      // Pulsing gain for buzz effect
      gainNode.gain.setValueAtTime(0.8, audioCtx.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.1, audioCtx.currentTime + 0.3);
      gainNode.gain.setValueAtTime(0.8, audioCtx.currentTime + 0.4);
      gainNode.gain.exponentialRampToValueAtTime(0.1, audioCtx.currentTime + 0.7);

      oscillator.start(audioCtx.currentTime);
      oscillator.stop(audioCtx.currentTime + 0.8);
    } catch (e) {
      console.error('[Driver] Audio notification failed:', e);
    }

    // Vibrate device (pattern: 3 short bursts)
    if (navigator.vibrate) {
      navigator.vibrate([300, 100, 300, 100, 300]);
    }
  };

  // WebSocket for real-time stats updates (replaces 30s polling)
  useWebSocket("/ws/driver/stats/", {
    onMessage: (message: any) => {
      if (message.type === "stats_update" && message.stats) {
        setStats(message.stats);
      }
    },
  });

  // Fallback polling for incoming rides while online (covers missed/broken push notifications).
  useEffect(() => {
    if (!online) return;
    let alive = true;
    const poll = async () => {
      if (!alive) return;
      if (activeRide && !["completed", "cancelled", "payment_confirmed"].includes(activeRide.status)) {
        setIncoming(null);
        return;
      }
      try {
        const res = await api.driverIncoming();
        if (!alive) return;
        // Only notify if this is a new ride (was null before, now has ride)
        if (res.ride && !incoming) {
          setIncoming(res.ride);
          notifyDriver();
        } else {
          setIncoming(res.ride ?? null);
        }
      } catch {
        // Keep silent; websocket will continue.
      }
    };
    void poll();
    const interval = setInterval(poll, 4000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [online, activeRide, incoming]);

  // Connect to driver location WebSocket
  const connectLocationWebSocket = () => {
    if (locationWsRef.current?.readyState === WebSocket.OPEN) return;

    const wsUrl = `${import.meta.env.VITE_WS_URL || `wss://${window.location.host}`}/ws/driver/location/`;
    const ws = new WebSocket(wsUrl);
    locationWsRef.current = ws;

    ws.onopen = () => {
      console.log("[Driver] Location WebSocket connected");
    };

    ws.onclose = () => {
      console.log("[Driver] Location WebSocket closed");
      locationWsRef.current = null;
    };

    ws.onerror = (event) => {
      console.error("[Driver] Location WebSocket error:", event);
    };
  };

  // Send location via WebSocket
  const sendLocationViaWebSocket = () => {
    if (!locationWsRef.current || locationWsRef.current.readyState !== WebSocket.OPEN) return;
    if (!latestLocationRef.current) return;

    const loc = latestLocationRef.current;
    locationWsRef.current.send(JSON.stringify({
      type: "location_update",
      lat: loc.lat,
      lng: loc.lng,
      heading: loc.heading,
      timestamp: loc.timestamp,
    }));
  };

  // Start location tracking
  const startLocationTracking = () => {
    // Clear any existing watch
    if (watchIdRef.current !== null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
    }

    // Set up geolocation watch
    watchIdRef.current = navigator.geolocation.watchPosition(
      (position) => {
        const loc = {
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          heading: position.coords.heading ?? undefined,
        };
        setDriverLocation(loc);
        latestLocationRef.current = { ...loc, timestamp: Date.now() };
      },
      (err) => {
        console.error("[Driver] Geolocation watch error:", err);
        // If permission denied or position unavailable, mark driver offline
        if (err.code === 1 || err.code === 2) {
          toast.error("Your location is turned off or unavailable. We've taken you offline for safety.");
          // Force offline without needing current location
          api.setOnline(false).then(() => {
            setOnline(false);
            stopLocationTracking();
            setDriverLocation(null);
          }).catch(() => {});
        }
      },
      {
        enableHighAccuracy: true,
        maximumAge: 5000,
        timeout: 10000,
      }
    );

    // Connect WebSocket
    connectLocationWebSocket();

    // Send location every 2 seconds via WebSocket
    locationWsIntervalRef.current = setInterval(() => {
      sendLocationViaWebSocket();
    }, 2000);
  };

  // Stop location tracking
  const stopLocationTracking = () => {
    if (watchIdRef.current !== null) {
      navigator.geolocation.clearWatch(watchIdRef.current);
      watchIdRef.current = null;
    }
    // Clear the old HTTP heartbeat interval (legacy)
    if (locationIntervalRef.current) {
      clearInterval(locationIntervalRef.current);
      locationIntervalRef.current = null;
    }
    // Clear the new WebSocket location interval
    if (locationWsIntervalRef.current) {
      clearInterval(locationWsIntervalRef.current);
      locationWsIntervalRef.current = null;
    }
    // Close WebSocket
    if (locationWsRef.current) {
      locationWsRef.current.close();
      locationWsRef.current = null;
    }
  };

  // Toggle online status
  const toggleOnline = async () => {
    if (busy) return;
    setBusy(true);

    try {
      if (!online) {
        // Going online - get location first
        const position = await new Promise<GeolocationPosition>((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(
            resolve,
            (err) => {
              if (err.code === 1) {
                reject(new Error("Please turn on location services on your device to go online."));
              } else if (err.code === 3) {
                reject(new Error("Taking too long to find your location. Please check your GPS signal and try again."));
              } else {
                reject(new Error("Something went wrong with location services. Please try again."));
              }
            },
            {
              enableHighAccuracy: true,
              maximumAge: 0,
              timeout: 15000,
            }
          );
        });

        const loc = {
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          heading: position.coords.heading ?? undefined,
        };

        const result = await api.setOnline(true, loc);
        console.log("[Driver] Go online response:", result);
        setDriverLocation(loc);
        latestLocationRef.current = { ...loc, timestamp: Date.now() };
        startLocationTracking();
      } else {
        await api.setOnline(false);
        stopLocationTracking();
        setDriverLocation(null);
      }

      setOnline((current) => !current);
      toast.success(!online ? "You're now online and ready to receive rides!" : "You're now offline. Take a break!");
    } catch (error: any) {
      console.error("[Driver] Toggle online error:", error);
      const message = error?.message || "Couldn't go online right now. Please try again.";
      toast.error(message);
    } finally {
      setBusy(false);
    }
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopLocationTracking();
    };
  }, []);

  // Handle browser close/tab close - mark driver offline
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (online && locationWsRef.current?.readyState === WebSocket.OPEN) {
        // Send manual offline message before closing
        locationWsRef.current.send(JSON.stringify({ type: 'manual_offline' }));
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [online]);

  // Handle page visibility change - keep online in recent apps
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleVisibilityChange = () => {
      if (document.hidden) {
        // Page is hidden (in recent apps) - keep WebSocket alive
        // Don't stop tracking, just log it
        console.log('[Driver] App moved to background (recent apps)');
      } else {
        // Page is visible again
        console.log('[Driver] App back in foreground');
        // Ensure WebSocket is still connected, reconnect if needed
        if (online && (!locationWsRef.current || locationWsRef.current.readyState !== WebSocket.OPEN)) {
          console.log('[Driver] Reconnecting location WebSocket...');
          connectLocationWebSocket();
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [online]);

  const accept = async (id: string) => {
    try {
      const { ride } = await api.acceptRide(id);
      navigate({ to: "/driver/ride/$rideId", params: { rideId: ride.id } });
    } catch (error: any) {
      if (error?.code === "RIDE_ALREADY_ACCEPTED" || error?.message?.includes("already accepted")) {
        toast.error("This ride was taken by another driver. Next one is yours!");
        setIncoming(null); // Remove from this driver's screen
      } else {
        toast.error(error?.message || "Couldn't accept this ride. Please try again.");
      }
    }
  };

  const reject = async (id: string) => {
    try {
      await api.driverRejectRide(id);
      setIncoming(null);
      toast.info("Ride skipped. We'll find you another one.");
    } catch (error: any) {
      toast.error(error?.message || "Couldn't skip this ride. Please try again.");
      setIncoming(null);
    }
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    auth.clear();
    navigate({ to: "/" });
  };

  return (
    <div className="min-h-screen pb-10">
      <Navbar to="/driver" wsMsg={notificationMsg} />

      <div className="px-4 mt-2">
        <button
          onClick={toggleOnline}
          disabled={busy}
          className={`lift press relative w-full overflow-hidden rounded-2xl p-6 text-left transition ${
            online ? "bg-foreground text-background" : "glass text-foreground"
          }`}
        >
          <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-foreground/10 blur-2xl" />
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.2em] opacity-70">Status</div>
              <div className="font-display text-3xl font-bold">{online ? "You're online" : "Go online"}</div>
              <div className={`mt-1 flex items-center gap-2 text-sm ${online ? "text-background/70" : "text-muted-foreground"}`}>
                {online && <span className="pulse-dot" />}
                {online ? "Sharing your location and looking for ride requests..." : "Tap to start receiving rides"}
              </div>
            </div>
            <div className={`grid h-14 w-14 place-items-center rounded-full text-xl ${online ? "bg-background/10" : "bg-foreground text-background"}`}>
              {online ? "■" : "▶"}
            </div>
          </div>
        </button>

        <section className="mt-4 grid grid-cols-2 gap-2">
          <Stat label="Cash Today" value={stats ? `Rs ${(stats.cashEarningsToday ?? 0).toFixed(0)}` : "-"} />
          <Stat label="Wallet (Online)" value={stats ? `Rs ${stats.walletBalance.toFixed(0)}` : "-"} />
          <Stat label="Total Today" value={stats ? `Rs ${stats.earningsToday.toFixed(0)}` : "-"} />
          <Stat label="Rides Today" value={stats ? `${stats.todayRides ?? 0}` : "-"} />
        </section>

        <Link to="/driver/earnings" className="lift mt-3 flex items-center justify-between rounded-2xl glass p-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Earnings dashboard</div>
            <div className="font-display text-base font-semibold">Weekly chart, payouts & withdrawals</div>
          </div>
          <span className="grid h-9 w-9 place-items-center rounded-full bg-foreground text-background">→</span>
        </Link>

        {/* NEW: Active Ride Button - only show for truly active rides */}
        {activeRide && !["completed", "cancelled", "payment_confirmed"].includes(activeRide.status) && (
          <Link
            to="/driver/ride/$rideId"
            params={{ rideId: activeRide.id }}
            className="lift mt-3 flex items-center justify-between rounded-2xl bg-emerald-500/20 border border-emerald-500/50 p-4 text-sm hover:bg-emerald-500/30 transition"
          >
            <div>
              <div className="font-medium text-emerald-400">Active Ride: {activeRide.status.replace("_", " ")}</div>
              <div className="text-xs text-muted-foreground">Tap to continue ride</div>
            </div>
            <span className="text-emerald-400">→</span>
          </Link>
        )}

        {/* Logout Button */}
        <div className="mt-3">
          <Btn
            variant="outline"
            className="w-full text-muted-foreground hover:text-rose-400 hover:border-rose-400/50"
            onClick={logout}
          >
            Logout
          </Btn>
        </div>

        <section className="mt-4">
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Live map</div>
          <div className="h-[40vh] min-h-[260px] overflow-hidden rounded-2xl">
            <MapCanvas 
              theme={theme}
              driver={driverLocation ? { 
                lat: driverLocation.lat, 
                lng: driverLocation.lng, 
                heading: driverLocation.heading,
                vehicle: auth.user?.vehicle_type as any
              } : null} 
            />
          </div>
        </section>
      </div>

      <AnimatePresence>
        {incoming && (
          <motion.div
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 25, stiffness: 300 }}
            className="fixed inset-x-0 bottom-0 z-50 mx-auto w-full max-w-md"
          >
            <div className="glass rounded-t-3xl p-5 pb-8 shadow-2xl">
              {/* Handle bar */}
              <div className="mx-auto mb-4 h-1.5 w-12 rounded-full bg-foreground/20" />

              {/* Header */}
              <div className="flex items-center justify-between">
                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">New Ride Request</div>
                <span className="rounded-full bg-emerald-500 px-3 py-1 text-[10px] font-bold uppercase text-white capitalize">
                  {incoming.vehicle}
                </span>
              </div>

              {/* Pickup Location */}
              <div className="mt-4 rounded-xl bg-surface-2 p-4 hairline">
                <div className="flex items-start gap-3">
                  <div className="mt-1 h-3 w-3 rounded-full bg-emerald-500 ring-2 ring-emerald-500/30 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-1">Pickup Location</div>
                    <div className="text-sm font-medium leading-relaxed">{incoming.pickup.address}</div>
                    <div className="mt-1 text-xs text-emerald-400">{incoming.distances.driverToPickupKm.toFixed(1)} km from you</div>
                  </div>
                </div>
              </div>

              {/* Drop Location */}
              <div className="mt-2 rounded-xl bg-surface-2 p-4 hairline">
                <div className="flex items-start gap-3">
                  <div className="mt-1 h-3 w-3 rounded-full bg-[#5aa9ff] ring-2 ring-[#5aa9ff]/30 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-1">Drop Location</div>
                    <div className="text-sm font-medium leading-relaxed">{incoming.drop.address}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{incoming.distances.pickupToDropKm.toFixed(1)} km trip</div>
                  </div>
                </div>
              </div>

              {/* Fare Breakdown */}
              <div className="mt-3 rounded-xl bg-surface-2 p-4 hairline">
                <div className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground mb-2">Fare Breakdown</div>
                <div className="space-y-1 text-sm">
                  {/* Use backend fare breakdown from ride request (Ola distance based). */}
                  {(() => {
                    const distanceKm = Number(incoming.fare.distanceKm ?? incoming.distances.pickupToDropKm ?? 0);
                    const perKmRate = Number(incoming.fare.perKm ?? 0);
                    const distanceFare = Number(
                      incoming.fare.distanceFare ?? (distanceKm > 0 ? distanceKm * perKmRate : 0)
                    );
                    const baseFare = Number(incoming.fare.base ?? 0);
                    const timeFare = Number(incoming.fare.timeFare ?? 0);
                    const tax = Number(incoming.fare.tax ?? 0);
                    const subtotal = Number(
                      incoming.fare.subtotal ?? (baseFare + distanceFare + timeFare + tax)
                    );
                    return (
                      <>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Base fare</span>
                          <span>Rs {baseFare.toFixed(0)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Per km ({perKmRate.toFixed(0)} Rs/km x {distanceKm.toFixed(1)} km)</span>
                          <span>Rs {distanceFare.toFixed(0)}</span>
                        </div>
                        {timeFare > 0 && (
                          <div className="flex justify-between">
                            <span className="text-muted-foreground">Time fare</span>
                            <span>Rs {timeFare.toFixed(0)}</span>
                          </div>
                        )}
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">Tax</span>
                          <span>Rs {tax.toFixed(0)}</span>
                        </div>
                        <div className="flex justify-between border-t border-border/50 pt-1 mt-1">
                          <span className="text-muted-foreground">Subtotal</span>
                          <span>Rs {subtotal.toFixed(0)}</span>
                        </div>
                        {incoming.fare.discount > 0 && (
                          <div className="flex justify-between text-emerald-400">
                            <span>Discount</span>
                            <span>- Rs {incoming.fare.discount.toFixed(0)}</span>
                          </div>
                        )}
                      </>
                    );
                  })()}
                  <div className="flex justify-between border-t border-border pt-2 mt-2">
                    <span className="font-semibold">Total Earning</span>
                    <span className="font-display text-xl font-bold text-emerald-400">Rs {incoming.fare.total.toFixed(0)}</span>
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="mt-4 grid grid-cols-2 gap-3">
                <Btn variant="outline" onClick={() => reject(incoming.id)} className="h-12">
                  Skip
                </Btn>
                <Btn onClick={() => accept(incoming.id)} className="h-12 bg-emerald-500 hover:bg-emerald-600">
                  Accept Ride
                </Btn>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <RatingPrompt
        open={showRatingPrompt}
        rideId={pendingRatingRideId}
        title="Rate your passenger"
        onClose={() => {
          setShowRatingPrompt(false);
          // If rating was submitted, the RatingPrompt component already cleared localStorage
          // If user closed without submitting, we keep the pending rating for next time
          // unless rating was actually submitted (checked via rating_submitted flag)
          const wasSubmitted = pendingRatingRideId && localStorage.getItem(`rating_submitted_${pendingRatingRideId}`);
          if (wasSubmitted) {
            localStorage.removeItem("pending_rating_driver_ride_id");
            localStorage.removeItem(`rating_submitted_${pendingRatingRideId}`);
          }
        }}
      />

      {/* Onboarding Modal for new drivers */}
      <OnboardingModal
        isOpen={showOnboarding}
        onClose={handleOnboardingClose}
        userType="driver"
        isFirstTime={true}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  const isLoading = value === "-";
  return (
    <div className="glass rounded-xl p-3 shadow-lg ring-1 ring-white/5">
      <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-1">{label}</div>
      {isLoading ? (
        <div className="h-6 w-16 skeleton rounded-md" />
      ) : (
        <div className="font-display text-lg font-bold">{value}</div>
      )}
    </div>
  );
}



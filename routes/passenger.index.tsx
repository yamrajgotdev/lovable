import { createFileRoute, Link, useNavigate, useRouteContext } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Navbar } from "@/components/Navbar";
import { MapCanvas } from "@/components/MapCanvas";
import { RatingPrompt } from "@/components/RatingPrompt";
import { OnboardingModal } from "@/components/OnboardingModal";
import { Btn, Field } from "@/components/Field";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTheme } from "@/hooks/useTheme";
import { useTranslation } from "@/hooks/useTranslation";
import { api, auth, type NearbyDriver, type Quote, type Vehicle } from "@/lib/api";

export const Route = createFileRoute("/passenger/")({
  head: () => ({ meta: [{ title: "Book a ride - RIDES4U" }] }),
  component: PassengerHome,
});

const getVehicles = (t: (key: string) => string): { id: Vehicle; name: string; sub: string; emoji: string }[] => [
  { id: "bike", name: t("bike"), sub: t("bikeSub"), emoji: "🏍️" },
  { id: "auto", name: t("auto"), sub: t("autoSub"), emoji: "🛺" },
  { id: "erickshaw", name: t("erickshaw"), sub: t("erickshawSub"), emoji: "🔋" },
];

type NearbyStreamMessage =
  | { type: "nearby_drivers_snapshot"; drivers: NearbyDriver[] }
  | { type: "nearby_driver_upsert"; driver: NearbyDriver }
  | { type: "nearby_driver_remove"; driverId: string };

const ACTIVE_RIDE_STATUSES = new Set([
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

function PassengerHome() {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { t } = useTranslation();
  const VEHICLES = useMemo(() => getVehicles(t), [t]);
  const { notificationMsg } = useRouteContext({ from: "/passenger/" }) as any;
  const [pickup, setPickup] = useState("");
  const [drop, setDrop] = useState("");
  const [pickupLL, setPickupLL] = useState<{ lat: number; lng: number } | null>(null);
  const [dropLL, setDropLL] = useState<{ lat: number; lng: number } | null>(null);
  const [vehicle, setVehicle] = useState<Vehicle>("auto");
  const [quotes, setQuotes] = useState<Quote[] | null>(null);
  const [loadingQuote, setLoadingQuote] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [promo, setPromo] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [nearby, setNearby] = useState<NearbyDriver[]>([]);
  // Track nearby counts for all vehicle types separately
  const [nearbyAll, setNearbyAll] = useState<NearbyDriver[]>([]);
  // NEW: Track active ride with full details (including driver location for map)
  const [activeRide, setActiveRide] = useState<Ride | null>(null);
  const [pendingRatingRideId, setPendingRatingRideId] = useState<string | null>(null);
  const [showRatingPrompt, setShowRatingPrompt] = useState(false);
  // Saved places state
  const [savedPlaces, setSavedPlaces] = useState<{ id: string; label: string; address: string; lat: number; lng: number }[]>([]);
  const [showSavedPlaces, setShowSavedPlaces] = useState<"pickup" | "drop" | null>(null);
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

  // Load saved places on mount
  useEffect(() => {
    api.savedPlaces().then((r) => setSavedPlaces(r.places)).catch(() => setSavedPlaces([]));
  }, []);

  useEffect(() => {
    // Delay auth check slightly to allow localStorage to sync after login
    const checkAuth = setTimeout(() => {
      if (typeof window !== "undefined" && (!auth.token || auth.role !== "passenger")) {
        navigate({ to: "/" });
        return;
      }
    }, 100);

    return () => clearTimeout(checkAuth);
    // Note: We don't auto-request location anymore
    // User must click "Use current" to share their location
  }, [navigate]);

  // NEW: Check for active ride on mount - fetch full ride data with driver location
  useEffect(() => {
    const checkActiveRide = async () => {
      try {
        const response = await api.passengerActiveRide();
        if (response.ride) {
          setActiveRide(response.ride);
          // If ride has pickup/drop, set them for map display
          if (response.ride.pickup?.lat && response.ride.pickup?.lng) {
            setPickupLL({ lat: response.ride.pickup.lat, lng: response.ride.pickup.lng });
            setPickup(response.ride.pickupAddress || '');
          }
          if (response.ride.drop?.lat && response.ride.drop?.lng) {
            setDropLL({ lat: response.ride.drop.lat, lng: response.ride.drop.lng });
            setDrop(response.ride.dropAddress || '');
          }
          setActiveRide(null);
        }
      } catch {
        // Ignore errors
      }
    };
    checkActiveRide();
  }, []);

  // Listen for global clear events (terminal ride) to immediately hide popup
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = () => setActiveRide(null);
    window.addEventListener("storage", (e) => {
      if (e.key === "rides4u_active_ride_cleared") handler();
    });
    window.addEventListener("rides4u:activeCleared", handler as EventListener);
    return () => {
      window.removeEventListener("rides4u:activeCleared", handler as EventListener);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    
    // Delay showing rating prompt to allow page to settle and any active ride state to clear
    const timer = setTimeout(() => {
      const rideId = localStorage.getItem("pending_rating_passenger_ride_id");
      if (rideId) {
        // Check if rating was already submitted for this ride
        const alreadySubmitted = localStorage.getItem(`rating_submitted_${rideId}`);
        if (alreadySubmitted) {
          // Clear the pending rating since it was already submitted
          localStorage.removeItem("pending_rating_passenger_ride_id");
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
            localStorage.removeItem("pending_rating_passenger_ride_id");
            localStorage.removeItem("pending_rating_timestamp");
            return;
          }
        } else {
          // No timestamp set, this is an old pending rating from before the fix - clear it
          localStorage.removeItem("pending_rating_passenger_ride_id");
          return;
        }
        
        setPendingRatingRideId(rideId);
        setShowRatingPrompt(true);
        // Clear timestamp so popup won't show again on reload
        localStorage.removeItem("pending_rating_timestamp");
      }
    }, 500); // Small delay to allow state to settle
    
    return () => clearTimeout(timer);
  }, []);

  // Load nearby drivers whenever we have a location (initial or selected)
  useEffect(() => {
    if (!pickupLL) {
      console.log("[Passenger] No pickup location yet, skipping nearby fetch");
      return;
    }

    console.log("[Passenger] Fetching nearby drivers at:", pickupLL);

    // Fetch all drivers for counts
    api.nearbyDrivers(pickupLL.lat, pickupLL.lng)
      .then((response) => {
        console.log("[Passenger] NearbyAll drivers fetched:", response.drivers?.length || 0, response.drivers);
        setNearbyAll(response.drivers);
      })
      .catch((err) => {
        console.error("[Passenger] Failed to fetch nearbyAll:", err);
        setNearbyAll([]);
      });

    // Fetch drivers for map display (all vehicles, filtered by distance only)
    api.nearbyDrivers(pickupLL.lat, pickupLL.lng)
      .then((response) => {
        console.log("[Passenger] Nearby drivers fetched:", response.drivers?.length || 0, response.drivers);
        setNearby(response.drivers);
      })
      .catch((err) => {
        console.error("[Passenger] Failed to fetch nearby:", err);
        setNearby([]);
      });
  }, [pickupLL?.lat, pickupLL?.lng]);

  // WebSocket for live driver locations - show all vehicles on map
  const nearbyWsPath = pickupLL
    ? `/ws/drivers/nearby?lat=${pickupLL.lat}&lng=${pickupLL.lng}&radius=5`
    : null;

  useWebSocket<NearbyStreamMessage>(nearbyWsPath, {
    onMessage: (message) => {
      console.log("[Passenger] WebSocket message:", message.type, message);

      if (message.type === "nearby_drivers_snapshot") {
        console.log("[Passenger] WebSocket snapshot - drivers:", message.drivers?.length || 0);
        setNearby(message.drivers);
        return;
      }

      if (message.type === "nearby_driver_upsert") {
        console.log("[Passenger] WebSocket upsert - driver:", message.driver);
        setNearby((current) => {
          const next = current.filter((item) => item.id !== message.driver.id);
          next.push(message.driver);
          return next;
        });
        return;
      }

      if (message.type === "nearby_driver_remove") {
        console.log("[Passenger] WebSocket remove - driverId:", message.driverId);
        setNearby((current) => current.filter((item) => item.id !== message.driverId));
      }
    },
  });


  function useCurrent() {
    console.log("[Passenger] Getting current location...");
    if (!navigator.geolocation) {
      toast.error("Geolocation is not supported by your browser");
      return;
    }

    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const { latitude, longitude } = position.coords;
        console.log("[Passenger] Got location:", { lat: latitude, lng: longitude });
        // Set pickup location - this triggers nearby driver loading
        setPickupLL({ lat: latitude, lng: longitude });
        try {
          const { address } = await api.reverse(latitude, longitude);
          setPickup(address);
        } catch {
          setPickup(`${latitude.toFixed(4)}, ${longitude.toFixed(4)}`);
        }
      },
      (err) => {
        console.error("[Passenger] Geolocation error:", err);
        toast.error("Could not get your location. Please check your browser permissions.");
      },
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  const loadQuotes = async () => {
    if (!pickup || !drop || !pickupLL || !dropLL) return;

    setLoadingQuote(true);
    try {
      const response = await api.quote({ ...pickupLL, address: pickup }, { ...dropLL, address: drop });
      setQuotes(response.quotes);
    } catch (error) {
      toast.error((error as Error).message || "Could not fetch fare");
    } finally {
      setLoadingQuote(false);
    }
  };

  useEffect(() => {
    if (pickup && drop && pickupLL && dropLL) {
      // Debounce to prevent route flashing while user is still typing/selecting
      const timeoutId = setTimeout(loadQuotes, 400);
      return () => clearTimeout(timeoutId);
    }

    setQuotes(null);
  }, [pickup, drop, pickupLL, dropLL]);

  const selected = useMemo(() => quotes?.find((item) => item.vehicle === vehicle), [quotes, vehicle]);

  const onRequest = () => {
    if (!pickup) return toast.error(t("noPickup"));
    if (!drop) return toast.error(t("noDrop"));
    if (!selected) return toast.error(t("gettingQuote"));
    setShowConfirm(true);
  };

  const confirmRide = async () => {
    if (!selected || !pickupLL || !dropLL) return;

    setRequesting(true);
    try {
      const { ride } = await api.request({
        pickup: { ...pickupLL, address: pickup },
        drop: { ...dropLL, address: drop },
        vehicle,
        promo: promo || undefined,
      });
      navigate({ to: "/passenger/ride/$rideId", params: { rideId: ride.id } });
    } catch (error: any) {
      // Check if error is ACTIVE_RIDE_EXISTS
      const errorBody = error?.body || {};
      const errorMessage = error?.message || "";
      
      if (errorBody?.code === "ACTIVE_RIDE_EXISTS" || 
          errorMessage.includes("active ride") ||
          error?.status === 409) {
        // User has an active ride - redirect to it
        const activeRideId = errorBody?.active_ride?.id;
        if (activeRideId) {
          toast.info(t("activeRideRedirect"));
          navigate({ to: "/passenger/ride/$rideId", params: { rideId: activeRideId } });
        } else {
          toast.error(t("activeRideExists"));
        }
      } else {
        toast.error(errorMessage || "Request failed");
      }
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className="min-h-screen pb-28">
      <Navbar to="/passenger" wsMsg={notificationMsg} />

      <div className="px-4 mt-2">
        <div className="relative h-[38vh] min-h-[260px] overflow-hidden rounded-2xl shadow-xl ring-1 ring-white/5">
          <MapCanvas
            theme={theme}
            pickup={pickupLL ? { ...pickupLL, label: t("pickup") } : null}
            drop={dropLL ? { ...dropLL, label: t("drop") } : null}
            polyline={selected?.polyline || activeRide?.polyline}
            // Show driver location when ride has driver assigned (use driver.location from normalized data)
            driver={activeRide?.driver?.location?.lat && activeRide?.driver?.location?.lng ? {
              lat: activeRide.driver.location.lat,
              lng: activeRide.driver.location.lng,
              label: t("yourDriver")
            } : null}
            driverToPickupPolyline={activeRide?.driverToPickupPolyline || activeRide?.driver_to_pickup_polyline}
            // Show yellow route line when ride is booked/started
            showDriverLeg={!!activeRide && !['completed', 'cancelled', 'payment_confirmed'].includes(activeRide.status)}
            nearby={nearby}
          />
        </div>

        <section className="mt-4 glass rounded-2xl p-5 fade-in">
          <h2 className="font-display text-xl font-bold">{t("bookRideTitle")}</h2>
          <div className="mt-4 space-y-3">
            <div className="relative">
              <button
                onClick={() => {
                  setShowSavedPlaces("pickup");
                  document.activeElement instanceof HTMLElement && document.activeElement.blur();
                }}
                className="absolute left-2 top-7 z-10 rounded-md bg-secondary px-2.5 py-1.5 text-[11px] font-semibold text-secondary-foreground hover:bg-accent transition-colors border border-border"
                title={t("saved")}
              >
                {t("saved")}
              </button>
              <Field
                label={t("pickup")}
                placeholder={t("whereFrom")}
                value={pickup}
                onChange={(event) => {
                  setPickup(event.target.value);
                  setPickupLL(null);
                }}
                onSelect={(place: any) => {
                  if (place.address) {
                    setPickup(place.address);
                    setPickupLL({ lat: place.lat, lng: place.lng });
                  }
                }}
                className="pl-[72px]"
              />
              <button
                onClick={useCurrent}
                className="absolute right-2 top-7 rounded-md bg-foreground px-2.5 py-1.5 text-[11px] font-semibold text-background hover:opacity-90"
              >
                {t("useCurrent")}
              </button>
            </div>
            <div className="relative">
              <button
                onClick={() => {
                  setShowSavedPlaces("drop");
                  document.activeElement instanceof HTMLElement && document.activeElement.blur();
                }}
                className="absolute left-2 top-7 z-10 rounded-md bg-secondary px-2.5 py-1.5 text-[11px] font-semibold text-secondary-foreground hover:bg-accent transition-colors border border-border"
                title={t("saved")}
              >
                {t("saved")}
              </button>
              <Field
                label={t("drop")}
                placeholder={t("whereTo")}
                value={drop}
                onChange={(event) => {
                  setDrop(event.target.value);
                  setDropLL(null);
                }}
                onSelect={(place: any) => {
                  if (place.address) {
                    setDrop(place.address);
                    setDropLL({ lat: place.lat, lng: place.lng });
                  }
                }}
                className="pl-[72px]"
              />
            </div>
          </div>

          <div className="mt-5">
            <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
              {t("vehicle")} {loadingQuote && <span className="ml-2 text-muted-foreground">· {t("gettingQuote")}</span>}
            </div>
            <div className="grid grid-cols-1 gap-2">
              {VEHICLES.map((item) => {
                const quote = quotes?.find((candidate) => candidate.vehicle === item.id);
                const active = vehicle === item.id;
                const nearbyCount = nearbyAll.filter((d) => d.vehicle === item.id).length;
                
                if (loadingQuote && !quotes) {
                  return (
                    <div key={item.id} className="h-20 w-full skeleton rounded-xl ring-1 ring-white/5" />
                  );
                }

                return (
                  <button
                    key={item.id}
                    onClick={() => setVehicle(item.id)}
                    className={`lift press flex items-center justify-between rounded-xl p-3.5 text-left transition ${
                      active ? "bg-white text-black ring-1 ring-white/90 hover:bg-white" : "bg-surface-2 hairline hover:bg-elevated"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <div className={`grid h-10 w-10 place-items-center rounded-lg text-xl ${active ? "bg-black/10" : "bg-elevated"}`}>
                        {item.emoji}
                      </div>
                      <div>
                        <div className="font-semibold">{item.name}</div>
                        <div className={`text-xs ${active ? "text-black/70" : "text-muted-foreground"}`}>{item.sub}</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-display text-lg font-bold">{quote ? `Rs ${quote.total.toFixed(0)}` : "-"}</div>
                      <div className={`text-[11px] ${active ? "text-black/70" : "text-muted-foreground"}`}>
                        {quote ? `${quote.eta} ${t("min")} · ${quote.distanceKm.toFixed(1)} ${t("km")} · ${nearbyCount} ${t("nearbyDrivers")}` : t("addLocations")}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <Btn className="mt-5 w-full" onClick={onRequest}>
            {t("requestRide")}
          </Btn>

          {/* NEW: Active ride buttons */}
          {activeRide && (
            <div className="mt-4 space-y-2">
              <Link
                to="/passenger/ride/$rideId"
                params={{ rideId: activeRide.id }}
                className="flex items-center justify-between rounded-xl bg-amber-500/20 border border-amber-500/50 p-4 text-sm hover:bg-amber-500/30 transition"
              >
                <div>
                  <div className="font-medium text-amber-400">{t("activeRide")}: {activeRide.status.replace("_", " ")}</div>
                  <div className="text-xs text-muted-foreground">{t("tapToView")}</div>
                </div>
                <span className="text-amber-400">→</span>
              </Link>

              {/* NEW: Cancel button for early-stage rides */}
              {["requested", "searching", "accepted", "driver_arriving", "driver_arrived"].includes(activeRide.status) && (
                <Btn
                  variant="outline"
                  className="w-full text-rose-400 border-rose-400/50 hover:bg-rose-500/10"
                  onClick={async () => {
                    if (!confirm(t("confirmCancelRide"))) return;
                    try {
                      await api.cancelRide(activeRide.id);
                      toast.success(t("rideCancelled"));
                      setActiveRide(null);
                    } catch (error) {
                      toast.error((error as Error).message || t("cancelFailed"));
                    }
                  }}
                >
                  {t("cancelRide")}
                </Btn>
              )}
            </div>
          )}
        </section>
      </div>

      <BottomBar />
      <RatingPrompt
        open={showRatingPrompt}
        rideId={pendingRatingRideId}
        title={t("rateYourDriver")}
        onClose={() => {
          setShowRatingPrompt(false);
          // If rating was submitted, the RatingPrompt component already cleared localStorage
          // If user closed without submitting, we keep the pending rating for next time
          // unless rating was actually submitted (checked via rating_submitted flag)
          const wasSubmitted = pendingRatingRideId && localStorage.getItem(`rating_submitted_${pendingRatingRideId}`);
          if (wasSubmitted) {
            localStorage.removeItem("pending_rating_passenger_ride_id");
            localStorage.removeItem(`rating_submitted_${pendingRatingRideId}`);
          }
        }}
      />

      <AnimatePresence>
        {showConfirm && selected && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-30 flex items-end justify-center bg-background/70 backdrop-blur-sm"
            onClick={() => setShowConfirm(false)}
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
              <div className="space-y-1">
                <Row label={t("pickup")} value={pickup} dot="bg-white" />
                <Row label={t("drop")} value={drop} dot="bg-[#5aa9ff]" />
              </div>
              <div className="mt-4 space-y-2 rounded-xl bg-surface-2 p-4 hairline">
                <Line label={t("baseFare")} value={`₹${selected.base.toFixed(2)}`} />
                <Line label={t("tax")} value={`₹${selected.tax.toFixed(2)}`} />
                <Line
                  label={`${t("perKm")} x ${selected.distanceKm.toFixed(1)}`}
                  value={`₹${(selected.perKm * selected.distanceKm).toFixed(2)}`}
                />
                <div className="my-2 h-px bg-border" />
                <Line label={t("total")} value={`₹${selected.total.toFixed(0)}`} bold />
              </div>
              <div className="mt-4 flex items-center gap-2">
                <Field
                  placeholder={t("promoCode")}
                  value={promo}
                  onChange={(event) => setPromo(event.target.value.toUpperCase())}
                  className="flex-1"
                />
                <Btn variant="outline" onClick={() => promo && toast.success(`${t("applied")} ${promo}`)}>
                  {t("apply")}
                </Btn>
              </div>
              <Btn className="mt-5 w-full" onClick={confirmRide} disabled={requesting}>
                {requesting ? t("confirming") : t("confirmRide")}
              </Btn>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Saved Places Modal */}
      <AnimatePresence>
        {showSavedPlaces && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[200] flex items-center justify-center bg-background/80 backdrop-blur-sm p-4"
            onClick={() => setShowSavedPlaces(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-md rounded-2xl glass p-5 max-h-[70vh] overflow-hidden"
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-display text-xl font-bold">{t("saved")}</h3>
                <button
                  onClick={() => setShowSavedPlaces(null)}
                  className="p-2 rounded-lg hover:bg-surface-2 transition-colors"
                >
                  ✕
                </button>
              </div>
              
              {savedPlaces.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  {t("noSavedPlaces")}
                </div>
              ) : (
                <div className="space-y-2 overflow-y-auto max-h-[50vh]">
                  {savedPlaces.map((place) => (
                    <button
                      key={place.id}
                      onClick={() => {
                        if (showSavedPlaces === "pickup") {
                          setPickup(place.address);
                          setPickupLL({ lat: place.lat, lng: place.lng });
                        } else {
                          setDrop(place.address);
                          setDropLL({ lat: place.lat, lng: place.lng });
                        }
                        setShowSavedPlaces(null);
                      }}
                      className="w-full text-left p-4 rounded-xl bg-surface-2 hover:bg-elevated transition-colors"
                    >
                      <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">{place.label}</div>
                      <div className="text-sm mt-1 truncate">{place.address}</div>
                    </button>
                  ))}
                </div>
              )}
              
              <Link
                to="/passenger/saved"
                className="block mt-4 text-center text-sm text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowSavedPlaces(null)}
              >
                {t("manageSavedPlaces")} →
              </Link>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Onboarding Modal for new users */}
      <OnboardingModal
        isOpen={showOnboarding}
        onClose={handleOnboardingClose}
        userType="passenger"
        isFirstTime={true}
      />
    </div>
  );
}

function Row({ label, value, dot }: { label: string; value: string; dot: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl bg-surface-2 p-3 hairline">
      <span className={`h-2.5 w-2.5 rounded-full ${dot} ring-2 ring-background`} />
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{label}</div>
        <div className="truncate text-sm font-medium">{value || "-"}</div>
      </div>
    </div>
  );
}

function Line({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className={bold ? "font-semibold" : "text-muted-foreground"}>{label}</span>
      <span className={bold ? "font-display text-lg font-bold" : "font-medium"}>{value}</span>
    </div>
  );
}

function BottomBar() {
  const items = [
    { to: "/passenger" as const, label: "Ride", icon: "🚗" },
    { to: "/passenger/history" as const, label: "History", icon: "🕘" },
    { to: "/passenger/saved" as const, label: "Saved", icon: "📍" },
    { to: "/passenger/settings" as const, label: "Account", icon: "⚙️" },
  ];

  return (
    <nav className="fixed inset-x-0 bottom-0 z-20 mx-auto w-full max-w-md px-4 pb-4">
      <div className="glass flex items-center justify-around rounded-2xl px-2 py-2">
        {items.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            activeOptions={{ exact: true }}
            activeProps={{ className: "bg-foreground text-background" }}
            className="lift flex flex-1 flex-col items-center gap-0.5 rounded-xl px-2 py-2 text-[11px] font-medium text-foreground hover:bg-elevated"
          >
            <span className="text-lg leading-none">{item.icon}</span>
            <span>{item.label}</span>
          </Link>
        ))}
      </div>
    </nav>
  );
}

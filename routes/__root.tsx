import { Outlet, createRootRoute, Link, useLocation } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/sonner";
import { useEffect, useState } from "react";
import { api, auth } from "@/lib/api";
import type { Ride } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTheme } from "@/hooks/useTheme";
import { ConnectionStatus } from "@/components/ConnectionStatus";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="font-display text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you’re looking for doesn’t exist.
        </p>
        <a
          href="/"
          className="lift press mt-6 inline-flex items-center justify-center rounded-lg bg-foreground px-4 py-2 text-sm font-semibold text-background"
        >
          Go home
        </a>
      </div>
    </div>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
});

function RootComponent() {
  const [notificationMsg, setNotificationMsg] = useState<any>(null);
  const [expiredSessionInfo, setExpiredSessionInfo] = useState<{ phone: string | null; role: "passenger" | "rider" | null } | null>(null);

  const ws = useWebSocket(auth.token ? "/ws/notifications/" : null, {
    onMessage: (msg) => {
      if (msg.type === "notification" || msg.type === "notification_snapshot") {
        setNotificationMsg(msg);
      }
    }
  });

  useEffect(() => {
    if (typeof window === "undefined") return;

    const currentPath = window.location.pathname;
    const hasToken = auth.token;
    const isExpired = auth.isTokenExpired();
    const protectedPaths = ["/passenger", "/driver", "/account", "/saved"];
    const isOnProtectedPage = protectedPaths.some(p => currentPath.startsWith(p));

    if (isOnProtectedPage && isExpired) {
      setExpiredSessionInfo(auth.getExpiredSessionInfo());
    }

    if (isOnProtectedPage && !hasToken) {
      window.location.href = "/";
    }

    const interval = setInterval(() => {
      const path = window.location.pathname;
      const protectedPath = protectedPaths.some((p) => path.startsWith(p));
      if (protectedPath && auth.isTokenExpired()) {
        setExpiredSessionInfo(auth.getExpiredSessionInfo());
      }
    }, 60_000);

    return () => clearInterval(interval);
  }, []);

  const handleExpiredContinue = async () => {
    const info = expiredSessionInfo;
    const role = info?.role || "passenger";
    const phone = (info?.phone || "").replace(/\D/g, "").slice(0, 10);
    try {
      await api.logout();
    } catch {
      // Token may already be expired on server; continue local logout.
    } finally {
      auth.clear();
      const query = `?mode=signin&role=${role}${phone ? `&phone=${encodeURIComponent(phone)}` : ""}`;
      window.location.href = `/auth/login${query}`;
    }
  };

  return (
    <>
      <ConnectionStatus ws={ws} />
      <ActiveRidePopup />
      {/* @ts-ignore */}
      <Outlet context={{ notificationMsg }} />
      {expiredSessionInfo && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-background/85 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl bg-surface-2 p-5 ring-1 ring-border">
            <div className="text-lg font-semibold">Session expired</div>
            <p className="mt-2 text-sm text-muted-foreground">
              Please log in again with the same number.
            </p>
            <button
              onClick={handleExpiredContinue}
              className="mt-4 w-full rounded-lg bg-foreground py-2 text-sm font-semibold text-background"
            >
              Continue
            </button>
          </div>
        </div>
      )}
      <ToasterWrapper />
    </>
  );
}

function ToasterWrapper() {
  const { theme } = useTheme();
  
  return (
    <Toaster 
      position="top-right" 
      theme={theme}
      toastOptions={{
        style: {
          background: theme === 'dark' ? 'rgba(26, 26, 26, 0.8)' : 'rgba(255, 255, 255, 0.95)',
          backdropFilter: 'blur(12px)',
          border: theme === 'dark' ? '1px solid rgba(255, 255, 255, 0.1)' : '1px solid rgba(0, 0, 0, 0.1)',
          color: theme === 'dark' ? '#fff' : '#1a1a1a',
          borderRadius: '16px',
        },
      }}
    />
  );
}

function ActiveRidePopup() {
  const location = useLocation();
  const [activeRide, setActiveRide] = useState<Ride | null>(null);
  const currentPath = location.pathname || "";
  const isOnRidePage = currentPath.includes("/passenger/ride/") || currentPath.includes("/driver/ride/");

  // Canonical active statuses (terminal states must not show popup)
  const activeStatuses = new Set([
    "requested",
    "searching_driver",
    "searching",
    "accepted",
    "driver_assigned",
    "driver_arriving",
    "driver_arrived",
    "otp_verified",
    "started",
    "reached_destination",
    "payment_required",
    // Note: payment_confirmed, completed, cancelled are terminal states
  ]);

  const CLEARED_KEY = "rides4u_active_ride_cleared";
  const lastClearedAtRef = { current: Number(typeof window !== 'undefined' ? localStorage.getItem(CLEARED_KEY) || 0 : 0) } as { current: number };
  const ignoreUntilRef = { current: 0 } as { current: number };

  useEffect(() => {
    if (typeof window === "undefined") return;

    let alive = true;
    let interval: NodeJS.Timeout | null = null;

    // If user is on ride page, don't show popup
    if (isOnRidePage) return;

    if (!auth.token || !auth.role) {
      setActiveRide(null);
      return;
    }

    const onCleared = () => {
      // Immediate cleanup when another part of the app announces terminal ride
      setActiveRide(null);
      lastClearedAtRef.current = Number(localStorage.getItem(CLEARED_KEY) || 0);
      ignoreUntilRef.current = Date.now() + 3000; // Ignore short-lived REST races
    };

    // Listen for storage events from other tabs
    const storageHandler = (e: StorageEvent) => {
      if (e.key === CLEARED_KEY) onCleared();
    };
    window.addEventListener("storage", storageHandler);

    // Listen for same-tab dispatches
    const customHandler = () => onCleared();
    window.addEventListener("rides4u:activeCleared", customHandler as EventListener);

    const checkActiveRide = async () => {
      try {
        // If we've been recently signalled to ignore REST (race prevention), skip
        if (Date.now() < ignoreUntilRef.current) return;

        const response = await api.history();
        if (!alive) return;

        // If a clear happened while we were fetching, drop results
        const latestCleared = Number(localStorage.getItem(CLEARED_KEY) || 0);
        if (latestCleared > lastClearedAtRef.current) {
          // Another component already cleared active ride - ignore this REST result
          lastClearedAtRef.current = latestCleared;
          setActiveRide(null);
          ignoreUntilRef.current = Date.now() + 3000;
          return;
        }

        const active = response.rides?.find((r: Ride) => {
          const status = String((r as any)?.status || "").toLowerCase();
          const createdAt = Date.parse(String((r as any)?.createdAt || ""));
          const fresh = Number.isFinite(createdAt) ? Date.now() - createdAt < 12 * 60 * 60 * 1000 : true;
          return fresh && activeStatuses.has(status);
        });

        // Only update state if active ride actually changed (prevents unnecessary re-renders)
        setActiveRide((current) => {
          if (active && current && active.id === current.id) {
            return current; // Same ride, don't update
          }
          return active || null;
        });
      } catch (err) {
        setActiveRide(null);
      }
    };

    checkActiveRide();
    // Poll every 60 seconds (was 15 seconds) to prevent page refresh feel
    // WebSocket updates handle real-time changes, polling is just a fallback
    interval = setInterval(checkActiveRide, 60000);

    return () => {
      alive = false;
      if (interval) clearInterval(interval);
      window.removeEventListener("storage", storageHandler);
      window.removeEventListener("rides4u:activeCleared", customHandler as EventListener);
    };
  }, [auth.token, auth.role, isOnRidePage]);

  const hasValidData = activeRide && 
    activeRide.id && 
    (activeRide.pickup?.address || activeRide.drop?.address);

  if (!hasValidData || isOnRidePage) return null;

  const rideUrl = auth.role === "passenger"
    ? `/passenger/ride/${activeRide.id}`
    : `/driver/ride/${activeRide.id}`;

  return (
    <div className="fixed top-4 right-4 z-50 max-w-xs">
      <Link
        to={rideUrl}
        className="block rounded-xl glass p-3 shadow-lg ring-1 ring-amber-500/30 hover:shadow-xl transition animate-in fade-in slide-in-from-top-2"
      >
        <div className="flex items-center gap-2 mb-2">
          <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
            Ride Active
          </span>
        </div>
        <div className="space-y-1 text-xs">
          <div className="flex items-start gap-2">
            <span className="text-muted-foreground">From:</span>
            <span className="truncate max-w-[180px]">{activeRide.pickup?.address || "Current location"}</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-muted-foreground">To:</span>
            <span className="truncate max-w-[180px]">{activeRide.drop?.address || "Destination"}</span>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <span className="text-sm font-display font-bold">
            ₹{activeRide.fare?.total?.toFixed?.(0) || "--"}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-amber-400">
            View →
          </span>
        </div>
      </Link>
    </div>
  );
}

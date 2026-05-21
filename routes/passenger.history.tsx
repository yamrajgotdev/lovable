import { createFileRoute, Link, useNavigate, useRouteContext } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Navbar } from "@/components/Navbar";
import { api, auth, type Ride } from "@/lib/api";

export const Route = createFileRoute("/passenger/history")({
  head: () => ({ meta: [{ title: "Ride history — RIDES4U" }] }),
  component: History,
});

function History() {
  const navigate = useNavigate();
  const { notificationMsg } = useRouteContext({ from: "/passenger/history" }) as any;
  const [rides, setRides] = useState<Ride[] | null>(null);
  useEffect(() => {
    if (typeof window !== "undefined" && (!auth.token || auth.role !== "passenger")) {
      navigate({ to: "/" });
      return;
    }
    api.history().then((r) => setRides(r.rides)).catch(() => setRides([]));
  }, [navigate]);

  return (
    <div className="min-h-screen pb-10">
      <Navbar to="/passenger" wsMsg={notificationMsg} />
      <main className="px-4 mt-2">
        <h1 className="font-display text-2xl font-bold">Ride history</h1>
        <div className="mt-4 space-y-3">
          {rides === null && (
            <>
              <div className="h-20 rounded-xl skeleton ring-1 ring-white/5" />
              <div className="h-20 rounded-xl skeleton ring-1 ring-white/5" />
              <div className="h-20 rounded-xl skeleton ring-1 ring-white/5" />
            </>
          )}
          {rides?.length === 0 && <div className="rounded-xl glass p-6 text-center text-sm text-muted-foreground">No rides yet</div>}
          {rides?.map((r) => (
            <Link
              key={r.id}
              to="/passenger/ride/$rideId"
              params={{ rideId: r.id }}
              className="lift block rounded-xl glass p-4"
            >
              <div className="flex items-center justify-between">
                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">{new Date(r.createdAt).toLocaleString()}</div>
                <div className="rounded-md bg-foreground px-2 py-0.5 text-[10px] font-bold uppercase text-background">{r.status}</div>
              </div>
              <div className="mt-2 truncate text-sm">{r.pickup.address} → {r.drop.address}</div>
              <div className="mt-2 flex items-center justify-between">
                <span className="text-xs capitalize text-muted-foreground">{r.vehicle}</span>
                <span className="font-display text-lg font-bold">₹{r.fare.total.toFixed(0)}</span>
              </div>
            </Link>
          ))}
        </div>
      </main>
    </div>
  );
}

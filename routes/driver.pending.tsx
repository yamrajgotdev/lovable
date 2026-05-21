import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Brand } from "@/components/Brand";
import { Btn } from "@/components/Field";
import { api, auth } from "@/lib/api";
import { toast } from "sonner";

export const Route = createFileRoute("/driver/pending")({
  head: () => ({ meta: [{ title: "Verification in progress — RIDES4U" }] }),
  component: DriverPending,
});

function DriverPending() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected">(
    auth.user?.verification_status ?? "pending",
  );
  const [reason, setReason] = useState<string | undefined>();
  const [checking, setChecking] = useState(false);

  // Guard: must be a logged-in rider
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!auth.token || auth.role !== "rider") {
      navigate({ to: "/" });
    }
  }, [navigate]);

  const check = async () => {
    setChecking(true);
    try {
      const res = await api.riderStatus();
      setStatus(res.status);
      setReason(res.reason);
      const u = auth.user;
      if (u) auth.setUser({ ...u, verification_status: res.status });
      if (res.status === "approved") {
        toast.success("You're approved! Welcome aboard.");
        navigate({ to: "/driver" });
      } else if (res.status === "rejected") {
        toast.error("Verification rejected.");
      }
    } catch (e) {
      toast.error((e as Error).message || "Could not refresh status");
    } finally {
      setChecking(false);
    }
  };

  // Auto-poll every 20s while pending
  useEffect(() => {
    if (status !== "pending") return;
    const id = setInterval(check, 20000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const logout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    auth.clear();
    navigate({ to: "/" });
  };

  return (
    <div className="min-h-screen px-6 py-6">
      <header className="flex items-center justify-between">
        <Brand />
        <button onClick={logout} className="text-sm text-muted-foreground hover:text-foreground">
          Sign out
        </button>
      </header>

      <main className="mx-auto mt-12 max-w-md text-center scale-in">
        <motion.div
          animate={{ scale: [1, 1.05, 1], rotate: [0, 2, -2, 0] }}
          transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
          className="mx-auto grid h-28 w-28 place-items-center rounded-full bg-foreground text-background shadow-pop"
        >
          <span className="text-5xl">
            {status === "approved" ? "✓" : status === "rejected" ? "✕" : "⏳"}
          </span>
        </motion.div>

        <h1 className="mt-6 font-display text-3xl font-bold">
          {status === "approved"
            ? "You're verified"
            : status === "rejected"
              ? "Verification rejected"
              : "Verification in progress"}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {status === "approved"
            ? "Tap below to start driving."
            : status === "rejected"
              ? reason ?? "Please contact support to re-submit your documents."
              : "Our admin team is reviewing your documents. This usually takes a few hours. We'll notify you the moment you're approved."}
        </p>

        <div className="glass mt-8 space-y-3 rounded-2xl p-5 text-left">
          <Row label="Name" value={auth.user?.name ?? "—"} />
          <Row label="Mobile" value={auth.user?.phone ? `+91 ${auth.user.phone}` : "—"} />
          <Row label="Status" value={status} pill />
        </div>

        {status === "pending" && (
          <Btn onClick={check} disabled={checking} className="mt-6 w-full">
            {checking ? "Refreshing…" : "Check status"}
          </Btn>
        )}
        {status === "approved" && (
          <Btn onClick={() => navigate({ to: "/driver" })} className="mt-6 w-full">
            Go to dashboard
          </Btn>
        )}
        {status === "rejected" && (
          <Btn onClick={() => navigate({ to: "/auth/rider-signup" })} className="mt-6 w-full">
            Re-submit documents
          </Btn>
        )}

        <p className="mt-4 text-xs text-muted-foreground">
          Auto-refreshing every 20 seconds.
        </p>
      </main>
    </div>
  );
}

function Row({ label, value, pill }: { label: string; value: string; pill?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs uppercase tracking-[0.2em] text-muted-foreground">{label}</span>
      {pill ? (
        <span className="rounded-full bg-foreground px-2.5 py-0.5 text-[11px] font-bold uppercase text-background">
          {value}
        </span>
      ) : (
        <span className="text-sm font-semibold">{value}</span>
      )}
    </div>
  );
}

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { Brand } from "@/components/Brand";
import { Btn, Field } from "@/components/Field";
import { api, auth, type EarningsResponse } from "@/lib/api";

export const Route = createFileRoute("/driver/earnings")({
  head: () => ({ meta: [{ title: "Earnings — RIDES4U Driver" }] }),
  component: Earnings,
});

function Earnings() {
  const navigate = useNavigate();
  const [range, setRange] = useState<"week" | "month">("week");
  const [data, setData] = useState<EarningsResponse | null>(null);
  const [withdrawAmt, setWithdrawAmt] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && (!auth.token || auth.role !== "rider")) {
      navigate({ to: "/" });
      return;
    }
    let alive = true;
    api.earnings(range)
      .then((r) => alive && setData(r))
      .catch(() => alive && setData({
        totals: { earnings: 0, rides: 0, onlineMinutes: 0, tips: 0, cashEarnings: 0, onlineEarnings: 0, walletBalance: 0 },
        daily: [], byVehicle: [], payouts: [],
      }));
    return () => { alive = false; };
  }, [range, navigate]);

  const max = useMemo(() => Math.max(1, ...(data?.daily ?? []).map((d) => d.earnings)), [data]);

  const withdraw = async () => {
    const amt = Number(withdrawAmt);
    if (!Number.isFinite(amt) || amt <= 0) return toast.error("Please enter a valid withdrawal amount.");
    setBusy(true);
    try {
      await api.withdraw(amt);
      toast.success(`Withdrawal request of ₹${amt} submitted! You'll receive it soon.`);
      setWithdrawAmt("");
      const r = await api.earnings(range);
      setData(r);
    } catch (e) {
      toast.error((e as Error).message || "Couldn't process withdrawal. Please try again later.");
    } finally { setBusy(false); }
  };

  return (
    <div className="min-h-screen pb-10">
      <header className="sticky top-0 z-20 flex items-center justify-between bg-background/70 px-5 py-4 backdrop-blur-md">
        <Brand to="/driver" />
        <div className="flex items-center gap-3 text-sm">
          <Link to="/driver/history" className="text-muted-foreground transition hover:text-foreground">
            History
          </Link>
          <span className="h-4 w-px bg-border" />
          <Link to="/driver" className="text-muted-foreground hover:text-foreground">← Home</Link>
        </div>
      </header>

      <main className="px-4">
        <div className="flex items-end justify-between">
          <div>
            <h1 className="font-display text-3xl font-bold">Earnings</h1>
            <p className="text-sm text-muted-foreground">Cash & UPI payments. Online payments go to Wallet.</p>
          </div>
          <div className="flex gap-1 rounded-lg bg-surface-2 p-1 hairline">
            {(["week", "month"] as const).map((r) => (
              <button
                key={r}
                onClick={() => setRange(r)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold capitalize transition ${range === r ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground"}`}
              >{r}</button>
            ))}
          </div>
        </div>

        <section className="mt-4 grid grid-cols-2 gap-2">
          <BigStat label="Total Earnings" value={`₹${(data?.totals.earnings ?? 0).toFixed(0)}`} accent />
          <BigStat label="Rides" value={`${data?.totals.rides ?? 0}`} />
          <BigStat label="Cash Collected" value={`₹${(data?.totals.cashEarnings ?? 0).toFixed(0)}`} />
          <BigStat label="Online (Wallet)" value={`₹${(data?.totals.onlineEarnings ?? 0).toFixed(0)}`} />
        </section>

        <div className="mt-3 rounded-xl bg-surface-2/50 p-3 hairline">
          <div className="text-[11px] text-muted-foreground">
            <span className="text-emerald-400 font-medium">Note:</span> Cash & UPI = You collected from passenger. 
            Online = Paid via Razorpay, available in Wallet (₹{(data?.totals.walletBalance ?? 0).toFixed(0)}).
          </div>
        </div>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Daily earnings</div>
          {data === null ? (
            <div className="mt-4 h-40 rounded-xl shimmer hairline" />
          ) : data.daily.length === 0 ? (
            <div className="mt-4 rounded-xl bg-surface-2 p-6 text-center text-sm text-muted-foreground hairline">No data yet</div>
          ) : (
            <div className="mt-4 flex h-40 items-end gap-2">
              {data.daily.map((d) => (
                <div key={d.date} className="group flex flex-1 flex-col items-center gap-1">
                  <motion.div
                    initial={{ height: 0 }}
                    animate={{ height: `${(d.earnings / max) * 100}%` }}
                    transition={{ duration: 0.5, ease: [0.2, 0.8, 0.2, 1] }}
                    className="w-full rounded-t-md bg-foreground/80 transition group-hover:bg-foreground"
                    title={`₹${d.earnings.toFixed(0)} · ${d.rides} rides`}
                  />
                  <span className="text-[10px] text-muted-foreground">{shortDate(d.date)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">By vehicle</div>
          <div className="mt-3 space-y-2">
            {(data?.byVehicle ?? []).length === 0 && <div className="text-sm text-muted-foreground">No rides this {range}</div>}
            {data?.byVehicle.map((v) => (
              <div key={v.vehicle} className="flex items-center justify-between rounded-xl bg-surface-2 p-3 hairline">
                <div className="capitalize">{v.vehicle}</div>
                <div className="flex items-center gap-4">
                  <span className="text-xs text-muted-foreground">{v.rides} rides</span>
                  <span className="font-display text-base font-bold">₹{v.earnings.toFixed(0)}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Withdraw to bank</div>
          <div className="mt-3 flex items-end gap-2">
            <Field
              label="Amount (₹)"
              inputMode="numeric"
              placeholder="0"
              value={withdrawAmt}
              onChange={(e) => setWithdrawAmt(e.target.value.replace(/\D/g, "").slice(0, 7))}
              className="flex-1"
            />
            <Btn onClick={withdraw} disabled={busy || !withdrawAmt}>{busy ? "…" : "Withdraw"}</Btn>
          </div>
          <div className="mt-3 text-[11px] text-muted-foreground">Hits your registered bank account in 1–2 hours via Razorpay payouts.</div>
        </section>

        <section className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Recent payouts</div>
          <div className="mt-3 space-y-2">
            {(data?.payouts ?? []).length === 0 && <div className="text-sm text-muted-foreground">No payouts yet</div>}
            {data?.payouts.map((p) => (
              <div key={p.id} className="flex items-center justify-between rounded-xl bg-surface-2 p-3 hairline">
                <div>
                  <div className="text-sm font-medium">₹{p.amount.toFixed(0)}</div>
                  <div className="text-[11px] text-muted-foreground">{new Date(p.date).toLocaleString()}</div>
                </div>
                <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase ${
                  p.status === "paid" ? "bg-foreground text-background"
                  : p.status === "failed" ? "bg-destructive text-destructive-foreground"
                  : "bg-elevated text-muted-foreground"
                }`}>{p.status}</span>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

function BigStat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`lift rounded-2xl p-4 ${accent ? "bg-foreground text-background" : "glass"}`}>
      <div className={`text-[10px] uppercase tracking-[0.2em] ${accent ? "text-background/70" : "text-muted-foreground"}`}>{label}</div>
      <div className="font-display text-2xl font-bold">{value}</div>
    </div>
  );
}

function fmtMin(m: number) {
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function shortDate(s: string) {
  const d = new Date(s);
  if (isNaN(d.getTime())) return s.slice(5);
  return d.toLocaleDateString(undefined, { weekday: "short" }).slice(0, 3);
}

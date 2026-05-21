import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Brand } from "@/components/Brand";
import { Btn, Field } from "@/components/Field";
import { api, auth } from "@/lib/api";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";

type Search = { phone?: string };

export const Route = createFileRoute("/auth/passenger-signup")({
  validateSearch: (s: Record<string, unknown>): Search => ({ phone: typeof s.phone === "string" ? s.phone : undefined }),
  head: () => ({
    meta: [
      { title: "Passenger signup — RIDES4U" },
      { name: "description", content: "Create your RIDES4U passenger account and book rides quickly." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/auth/passenger-signup" }],
  }),
  component: PassengerSignup,
});

function PassengerSignup() {
  const { phone: presetPhone } = Route.useSearch();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState(presetPhone ?? "");
  const [loading, setLoading] = useState(false);
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [showTerms, setShowTerms] = useState(false);

  const submit = async () => {
    if (name.trim().length < 2) return toast.error("Please enter your name");
    if (!/^\d{10}$/.test(phone)) return toast.error("Enter a valid 10-digit number");
    if (!agreeTerms) return toast.error("Please accept the Terms & Conditions");
    setLoading(true);
    try {
      const { token, user } = await api.passengerSignup(name.trim(), phone);
      auth.setToken(token);
      auth.setRole("passenger");
      auth.setUser(user);
      navigate({ to: "/passenger" });
    } catch (e) {
      toast.error((e as Error).message || "Sign up failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen px-6 py-6">
      <header className="flex items-center justify-between">
        <Brand />
        <span className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Passenger</span>
      </header>
      <main className="mx-auto mt-10 max-w-md scale-in">
        <h1 className="font-display text-3xl font-bold">Tell us about you</h1>
        <p className="mt-1 text-sm text-muted-foreground">Both fields are required.</p>
        <div className="glass mt-6 space-y-4 rounded-2xl p-5">
          <Field label="Full name" placeholder="e.g. Aarav Sharma" value={name} onChange={(e) => setName(e.target.value)} />
          <Field label="Mobile number" inputMode="tel" placeholder="10-digit mobile" value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 10))} />
          <label className="flex cursor-pointer items-start gap-3 rounded-xl bg-surface-2 p-3 hairline text-sm">
            <input
              type="checkbox"
              checked={agreeTerms}
              onChange={(e) => setAgreeTerms(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-foreground"
            />
            <span className="text-muted-foreground">
              I agree to the{" "}
              <button
                type="button"
                onClick={() => setShowTerms(true)}
                className="font-semibold text-foreground underline hover:text-foreground/80"
              >
                Terms & Conditions
              </button>
            </span>
          </label>

          <Btn onClick={submit} disabled={loading || !agreeTerms} className="w-full">
            {loading ? "Creating…" : "Create account"}
          </Btn>
        </div>
      </main>

      <AnimatePresence>
        {showTerms && (
          <TermsModal
            onClose={() => setShowTerms(false)}
            onAgree={() => {
              setAgreeTerms(true);
              setShowTerms(false);
            }}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function TermsModal({
  onClose,
  onAgree,
}: {
  onClose: () => void;
  onAgree: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-background/80 backdrop-blur-sm sm:items-center"
      onClick={onClose}
    >
      <motion.div
        initial={{ y: "100%" }}
        animate={{ y: 0 }}
        exit={{ y: "100%" }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-t-3xl bg-background p-6 pb-8 shadow-2xl sm:rounded-3xl"
      >
        <div className="mx-auto mb-4 h-1.5 w-12 rounded-full bg-foreground/20" />
        <h2 className="font-display text-2xl font-bold">Terms & Conditions</h2>
        <p className="mt-1 text-sm text-muted-foreground">For Passengers using RIDES4U</p>

        <div className="mt-4 max-h-[60vh] overflow-y-auto rounded-xl bg-surface-2 p-4 hairline text-sm space-y-4">
          <section>
            <h3 className="font-semibold">1. Service Usage</h3>
            <p className="text-muted-foreground mt-1">
              By using RIDES4U, you agree to use our platform responsibly. You must be at least 18 years old to book rides. All ride bookings are subject to driver availability.
            </p>
          </section>
          <section>
            <h3 className="font-semibold">2. Payments & Cancellations</h3>
            <p className="text-muted-foreground mt-1">
              Fares are calculated based on distance, time, and vehicle type. Cancellation fees may apply if you cancel after a driver has accepted your ride. Payments can be made via cash or online methods.
            </p>
          </section>
          <section>
            <h3 className="font-semibold">3. User Conduct</h3>
            <p className="text-muted-foreground mt-1">
              You agree to treat drivers with respect. Any form of harassment, violence, or damage to property will result in account suspension and possible legal action.
            </p>
          </section>
          <section>
            <h3 className="font-semibold">4. Safety</h3>
            <p className="text-muted-foreground mt-1">
              Always verify the vehicle and driver details before starting your ride. Share your ride status with trusted contacts. In case of emergency, contact local authorities immediately.
            </p>
          </section>
          <section>
            <h3 className="font-semibold">5. Liability</h3>
            <p className="text-muted-foreground mt-1">
              RIDES4U acts as a technology platform connecting passengers with drivers. We are not responsible for the actions of drivers or any incidents during the ride.
            </p>
          </section>

          <div className="mt-4 pt-4 border-t border-border">
            <p className="text-xs text-muted-foreground">
              By clicking &quot;I Agree&quot;, you confirm that you have read, understood, and agree to be bound by these Terms & Conditions.
            </p>
          </div>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          <Btn variant="outline" onClick={onClose}>
            Close
          </Btn>
          <Btn onClick={onAgree}>I Agree</Btn>
        </div>
      </motion.div>
    </motion.div>
  );
}

import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { Brand } from "@/components/Brand";
import { Btn, Field } from "@/components/Field";
import { api, auth, type Role } from "@/lib/api";
import {
  clearFirebaseOtpSession,
  firebaseConfirmOtp,
  firebaseSendOtp,
  isFirebaseConfigured,
  resetFirebaseRecaptcha,
  resendFirebaseOtp,
} from "@/lib/firebase_auth";
import { toast } from "sonner";
import { motion, AnimatePresence } from "framer-motion";
import { ApiError } from "@/lib/api";

type Mode = "signin" | "signup";
type Search = { role?: Role; mode?: Mode; phone?: string };

export const Route = createFileRoute("/auth/login")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    role: s.role === "rider" ? "rider" : "passenger",
    mode: s.mode === "signup" ? "signup" : "signin",
    phone: typeof s.phone === "string" ? s.phone.replace(/\D/g, "").slice(0, 10) : undefined,
  }),
  head: () => ({
    meta: [
      { title: "Sign in - RIDES4U" },
      { name: "description", content: "Sign in to RIDES4U as a passenger or driver." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/auth/login" }],
  }),
  component: Login,
});

// Helper to handle API errors with user-friendly messages
function handleApiError(error: any): string {
  if (!error) return "An unexpected error occurred";
  
  const message = error.message?.toLowerCase() || "";
  
  // User already exists / logged in
  if (message.includes("already") || message.includes("exists")) {
    return "You already have an account. Please sign in instead.";
  }
  
  // User not found
  if (message.includes("not found") || message.includes("does not exist")) {
    return "Account not found. Please create an account first.";
  }
  
  // Invalid credentials
  if (message.includes("invalid") || message.includes("incorrect")) {
    return "Invalid phone number or OTP. Please try again.";
  }
  
  // Session/Token expired
  if (message.includes("expired") || message.includes("session")) {
    return "Your session has expired. Please sign in again.";
  }
  
  // Unauthorized
  if (error.status === 401 || message.includes("unauthorized")) {
    return "Please sign in to continue.";
  }
  
  // Forbidden
  if (error.status === 403 || message.includes("forbidden")) {
    return "You don't have permission to do this.";
  }
  
  // Network errors
  if (message.includes("fetch") || message.includes("network") || message.includes("failed to fetch")) {
    return "Connection failed. Please check your internet and try again.";
  }
  
  // Rate limit errors
  if (message.includes("limit") || error.status === 429) {
    return "Too many requests. Please wait a few minutes and try again.";
  }
  
  // Server errors
  if (error.status >= 500) {
    return "Server is busy. Please try again in a moment.";
  }
  
  return error.message || "Something went wrong. Please try again.";
}

function Login() {
  const { role = "passenger", mode = "signin", phone: phoneFromSearch = "" } = Route.useSearch();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState(phoneFromSearch);
  const [otp, setOtp] = useState("");
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [loading, setLoading] = useState(false);
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [showTerms, setShowTerms] = useState(false);
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => {
      setCountdown((prev) => prev - 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  const finalizeLogin = async (token: string, user: any, isNew: boolean) => {
    // Handle mismatch between mode and user existence
    if (mode === "signup" && !isNew) {
      // User tried to signup but already has account → redirect to signin
      toast.error("Welcome back! You already have an account. Please sign in.");
      navigate({ to: "/auth/login", search: { role, mode: "signin" } });
      return;
    }
    if (mode === "signin" && isNew) {
      // User tried to signin but is new → redirect to signup
      toast.info("You're new here! Let's create an account for you.");
      navigate({ to: "/auth/login", search: { role, mode: "signup" } });
      return;
    }

    auth.setToken(token);
    auth.setRole(role);
    auth.setUser(user);

    // Set flag to show onboarding for new users
    if (isNew) {
      localStorage.setItem("show_onboarding", "true");
    }

    if (role === "rider") {
      if (isNew) navigate({ to: "/auth/rider-signup", search: { phone } });
      else navigate({ to: "/driver" });
      return;
    }

    if (isNew || mode === "signup") {
      if (mode === "signup" && name.trim()) {
        try {
          const res = await api.passengerSignup(name.trim(), phone);
          auth.setToken(res.token);
          auth.setUser(res.user);
        } catch {
          // account may already exist on backend
        }
        navigate({ to: "/passenger" });
      } else {
        navigate({ to: "/auth/passenger-signup", search: { phone } });
      }
      return;
    }

    navigate({ to: "/passenger" });
  };

  const sendOtp = async () => {
    if (mode === "signup" && role === "passenger" && name.trim().length < 2) {
      return toast.error("Please enter your full name to continue.");
    }
    if (!/^\d{10}$/.test(phone)) {
      return toast.error("Please enter a valid 10-digit phone number.");
    }

    setLoading(true);
    try {
      // Step 1: Check if phone exists in database
      const checkResult = await api.checkPhone(phone, role);
      
      // Handle different scenarios based on phone check
      if (checkResult.action === "signup" && mode === "signin") {
        // User trying to sign in but doesn't exist
        toast.error("We couldn't find your account. Let's create one for you!");
        navigate({ to: "/auth/login", search: { role, mode: "signup" } });
        setLoading(false);
        return;
      }
      
      if (checkResult.action === "login" && mode === "signup") {
        // User trying to signup but already exists
        toast.error("You already have an account! Please sign in instead.");
        navigate({ to: "/auth/login", search: { role, mode: "signin" } });
        setLoading(false);
        return;
      }
      
      // Handle rider pending approval
      if (checkResult.action === "pending") {
        toast.info("Your driver application is being reviewed. We'll notify you soon!");
        navigate({ to: "/driver/pending" });
        setLoading(false);
        return;
      }
      
      // Handle rider rejected
      if (checkResult.action === "rejected") {
        toast.error(`We're sorry, your application was rejected. Reason: ${checkResult.reason}`);
        setLoading(false);
        return;
      }
      
      // Handle rider needing to complete signup
      if (checkResult.action === "complete_signup") {
        toast.info("Almost there! Please complete your registration.");
        navigate({ to: "/auth/rider-signup", search: { phone } });
        setLoading(false);
        return;
      }

      // Step 2: Send OTP via Firebase
      if (!isFirebaseConfigured()) {
        throw new Error("Firebase OTP is not configured. Set VITE_FIREBASE_* variables.");
      }

      await firebaseSendOtp(phone, "recaptcha-container");
      toast.success("OTP sent to your phone! Please check your messages.");
      setCountdown(30);
      setStep("otp");
    } catch (e: any) {
      await resetFirebaseRecaptcha();
      const message = handleApiError(e);
      toast.error(message || "Something went wrong. Please try again.");
      console.error("Send OTP error:", e);
    } finally {
      setLoading(false);
    }
  };

  const verify = async () => {
    if (otp.length < 4) {
      return toast.error("Please enter the 4-digit OTP sent to your phone.");
    }

    setLoading(true);
    try {
      const { idToken, phoneNumber } = await firebaseConfirmOtp(otp);
      const { token, user, isNew } = await api.verifyFirebase(phoneNumber || phone, idToken, role);
      
      // Check rider verification status before finalizing
      if (role === "rider" && user.verification_status === "pending") {
        auth.setToken(token);
        auth.setRole(role);
        auth.setUser(user);
        toast.success("Account verified! We're reviewing your application.");
        navigate({ to: "/driver/pending" });
        return;
      }
      
      await finalizeLogin(token, user, isNew);
      clearFirebaseOtpSession();
    } catch (e: any) {
      const apiErr = e as ApiError;
      const body = (apiErr?.body ?? {}) as { code?: string };
      if (body?.code === "signup_required") {
        toast.info("Let's get you set up! Redirecting to signup...");
        navigate({ to: "/auth/login", search: { role, mode: "signup" } });
        return;
      }
      const message = handleApiError(e);
      toast.error(message || "Something went wrong. Please try again.");
      console.error("Verify OTP error:", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen px-6 py-6">
      <header className="flex items-center justify-between">
        <Brand />
        <button
          onClick={() => navigate({ to: "/" })}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          {"<- Back"}
        </button>
      </header>

      <main className="mx-auto mt-10 max-w-md scale-in">
        <div className="mb-6 text-center">
          <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            {mode === "signup" ? "Sign up as" : "Sign in as"}
          </div>
          <div className="mt-1 font-display text-3xl font-bold capitalize">{role}</div>
        </div>

        <div className="glass space-y-4 rounded-2xl p-5">
          {step === "phone" ? (
            <>
              {mode === "signup" && role === "passenger" && (
                <Field
                  label="Full name"
                  placeholder="e.g. Aarav Sharma"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              )}
              <Field
                label="Mobile number"
                inputMode="tel"
                placeholder="10-digit mobile"
                value={phone}
                onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 10))}
              />
              <Btn onClick={sendOtp} disabled={loading || (mode === "signup" && !agreeTerms)} className="w-full">
                {loading ? "Checking..." : "Send OTP"}
              </Btn>

              {mode === "signup" && (
                <label className="mt-3 flex cursor-pointer items-start gap-3 rounded-xl bg-surface-2 p-3 hairline text-sm">
                  <input
                    type="checkbox"
                    checked={agreeTerms}
                    onChange={(e) => setAgreeTerms(e.target.checked)}
                    className="mt-0.5 h-4 w-4 accent-foreground"
                  />
                  <span className="text-muted-foreground">
                    I agree to the{" "}
                    <button
                      onClick={() => setShowTerms(true)}
                      className="font-semibold text-foreground underline hover:text-foreground/80"
                    >
                      Terms & Conditions
                    </button>
                  </span>
                </label>
              )}
              {isFirebaseConfigured() && (
                <div
                  id="recaptcha-container"
                  className="overflow-hidden rounded-md border border-border p-2"
                />
              )}
              <button
                onClick={() =>
                  navigate({
                    to: "/auth/login",
                    search: { role, mode: mode === "signup" ? "signin" : "signup" },
                  })
                }
                className="block w-full text-center text-xs text-muted-foreground hover:text-foreground"
              >
                {mode === "signup" ? "Have an account? Sign in" : "New here? Create account"}
              </button>
            </>
          ) : (
            <>
              <Field
                label={`Enter OTP sent to +91 ${phone}`}
                inputMode="numeric"
                placeholder="...."
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              />
              <Btn onClick={verify} disabled={loading} className="w-full">
                {loading ? "Verifying..." : "Verify and continue"}
              </Btn>

              <button
                onClick={async () => {
                  if (countdown > 0) {
                    toast.info(`Please wait ${countdown} seconds before requesting a new OTP.`);
                    return;
                  }
                  setLoading(true);
                  try {
                    await resendFirebaseOtp(phone);
                    toast.success("New OTP sent! Please check your messages.");
                    setCountdown(30);
                  } catch (e: any) {
                    const message = handleApiError(e);
                    toast.error(message || "Something went wrong. Please try again.");
                    console.error("Resend OTP error:", e);
                  } finally {
                    setLoading(false);
                  }
                }}
                disabled={loading || countdown > 0}
                className="block w-full text-center text-sm text-muted-foreground hover:text-foreground underline disabled:opacity-50"
              >
                {countdown > 0 ? `Resend OTP in ${countdown}s` : "Resend OTP"}
              </button>

              <button
                onClick={() => {
                  clearFirebaseOtpSession();
                  setStep("phone");
                }}
                className="block w-full text-center text-xs text-muted-foreground hover:text-foreground"
              >
                Change number
              </button>
            </>
          )}
        </div>
      </main>

      <AnimatePresence>
        {showTerms && (
          <TermsModal
            role={role}
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
  role,
  onClose,
  onAgree,
}: {
  role: Role;
  onClose: () => void;
  onAgree: () => void;
}) {
  const isPassenger = role === "passenger";

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
        <h2 className="font-display text-2xl font-bold">
          Terms & Conditions
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          For {isPassenger ? "Passengers" : "Drivers"} using RIDES4U
        </p>

        <div className="mt-4 max-h-[60vh] overflow-y-auto rounded-xl bg-surface-2 p-4 hairline text-sm space-y-4">
          {isPassenger ? (
            <>
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
            </>
          ) : (
            <>
              <section>
                <h3 className="font-semibold">1. Driver Requirements</h3>
                <p className="text-muted-foreground mt-1">
                  You must possess a valid driving license, vehicle registration, and all required documents. Your vehicle must meet safety standards and pass our verification process.
                </p>
              </section>
              <section>
                <h3 className="font-semibold">2. Service Standards</h3>
                <p className="text-muted-foreground mt-1">
                  You agree to provide professional service to all passengers. This includes polite behavior, safe driving, maintaining vehicle cleanliness, and adhering to traffic laws.
                </p>
              </section>
              <section>
                <h3 className="font-semibold">3. Earnings & Payments</h3>
                <p className="text-muted-foreground mt-1">
                  Earnings are calculated based on completed rides. Platform commission will be deducted from each ride. Withdrawals can be requested to your registered bank account.
                </p>
              </section>
              <section>
                <h3 className="font-semibold">4. Document Authenticity</h3>
                <p className="text-muted-foreground mt-1">
                  All submitted documents must be genuine and valid. Providing fake or expired documents will result in permanent account termination and potential legal consequences.
                </p>
              </section>
              <section>
                <h3 className="font-semibold">5. Account Termination</h3>
                <p className="text-muted-foreground mt-1">
                  We reserve the right to suspend or terminate your account for violation of terms, poor ratings, or any fraudulent activity. You may appeal such decisions within 7 days.
                </p>
              </section>
              <section>
                <h3 className="font-semibold">6. Insurance & Liability</h3>
                <p className="text-muted-foreground mt-1">
                  You are responsible for maintaining valid vehicle insurance. RIDES4U is not liable for accidents, damages, or injuries occurring during rides.
                </p>
              </section>
            </>
          )}

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

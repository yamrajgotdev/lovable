import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useRef, useEffect } from "react";
import { Brand } from "@/components/Brand";
import { Btn, Field } from "@/components/Field";
import { api, auth } from "@/lib/api";
import { toast } from "sonner";
import { getFirebaseAuth, isFirebaseConfigured } from "@/lib/firebase";
import { signInWithPhoneNumber, PhoneAuthProvider, signInWithCredential, RecaptchaVerifier } from "firebase/auth";
import { resendFirebaseOtp } from "@/lib/firebase_auth";
import { motion, AnimatePresence } from "framer-motion";

type VehicleType = "bike" | "auto" | "erickshaw";
const VEHICLES: { id: VehicleType; label: string; emoji: string }[] = [
  { id: "bike", label: "Bike", emoji: "🏍️" },
  { id: "auto", label: "Auto", emoji: "🛺" },
  { id: "erickshaw", label: "E-Rickshaw", emoji: "⚡" },
];

type Search = { phone?: string };

type FormData = {
  name: string;
  phone: string;
  dl_number: string;
  pan_number: string;
  rc_number: string;
  plate: string;
  aadhaar: string;
  vehicle_type: VehicleType | "";
  agree: boolean;
  files: {
    dl_photo?: File;
    rc_photo?: File;
    aadhaar_photo?: File;
    pan_photo?: File;
  };
};

export const Route = createFileRoute("/auth/rider-signup")({
  validateSearch: (s: Record<string, unknown>): Search => ({ phone: typeof s.phone === "string" ? s.phone : undefined }),
  head: () => ({
    meta: [
      { title: "Driver signup — RIDES4U" },
      { name: "description", content: "Sign up as a RIDES4U driver and start earning with your vehicle." },
    ],
    links: [{ rel: "canonical", href: "https://rides4u.in/auth/rider-signup" }],
  }),
  component: RiderSignup,
});

function maskPhone(phone: string): string {
  if (phone.length !== 10) return phone;
  return phone.slice(0, 2) + "*****" + phone.slice(7);
}

function RiderSignup() {
  const { phone: presetPhone } = Route.useSearch();
  const navigate = useNavigate();
  const [step, setStep] = useState<"form" | "otp">("form");
  const [otp, setOtp] = useState("");
  const [form, setForm] = useState<FormData>({
    name: "",
    phone: presetPhone ?? "",
    dl_number: "",
    pan_number: "",
    rc_number: "",
    plate: "",
    aadhaar: "",
    vehicle_type: "",
    agree: false,
    files: {},
  });
  const [loading, setLoading] = useState(false);
  const [verificationId, setVerificationId] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const recaptchaRef = useRef<HTMLDivElement>(null);

  // Countdown timer for resend OTP
  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => {
      setCountdown((prev) => prev - 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown]);
  const recaptchaVerifierRef = useRef<RecaptchaVerifier | null>(null);

  // Initialize reCAPTCHA when in OTP step
  useEffect(() => {
    if (step === "otp" && recaptchaRef.current && !recaptchaVerifierRef.current) {
      const auth = getFirebaseAuth();
      if (auth) {
        recaptchaVerifierRef.current = new RecaptchaVerifier(auth, recaptchaRef.current, {
          size: "normal",
          callback: () => {
            // reCAPTCHA solved
          },
          "expired-callback": () => {
            toast.error("reCAPTCHA expired. Please try again.");
          },
        });
      }
    }
    return () => {
      if (recaptchaVerifierRef.current) {
        recaptchaVerifierRef.current.clear();
        recaptchaVerifierRef.current = null;
      }
    };
  }, [step]);

  const set = (k: keyof Omit<FormData, "files" | "agree" | "vehicle_type">) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  const validateForm = (): boolean => {
    const required: (keyof Omit<FormData, "files" | "agree" | "vehicle_type">)[] =
      ["name", "phone", "dl_number", "pan_number", "rc_number", "plate", "aadhaar"];
    for (const k of required) {
      if (!String(form[k]).trim()) {
        toast.error(`Please fill ${k.replace("_", " ")}`);
        return false;
      }
    }
    if (!form.vehicle_type) {
      toast.error("Please select a vehicle type");
      return false;
    }
    if (!/^\d{10}$/.test(form.phone)) {
      toast.error("Enter a valid 10-digit number");
      return false;
    }
    if (!/^\d{12}$/.test(form.aadhaar)) {
      toast.error("Aadhaar must be 12 digits");
      return false;
    }
    if (!/^[A-Z]{5}\d{4}[A-Z]$/.test(form.pan_number)) {
      toast.error("PAN must look like ABCDE1234F");
      return false;
    }
    if (!form.files.dl_photo || !form.files.rc_photo || !form.files.aadhaar_photo || !form.files.pan_photo) {
      toast.error("Please upload all four documents");
      return false;
    }
    if (!form.agree) {
      toast.error("Please accept the Terms & Conditions");
      return false;
    }
    return true;
  };

  const sendOtp = async () => {
    if (!validateForm()) return;

    if (!isFirebaseConfigured()) {
      toast.error("Firebase not configured. Check environment variables.");
      return;
    }

    setLoading(true);
    try {
      const auth = getFirebaseAuth();
      if (!auth) {
        toast.error("Firebase auth not initialized");
        return;
      }

      // Create invisible reCAPTCHA verifier for sending OTP
      const recaptchaVerifier = new RecaptchaVerifier(auth, "recaptcha-send-otp", {
        size: "invisible",
        callback: () => {
          // reCAPTCHA solved, will proceed with send
        },
      });

      const phoneWithCountry = `+91${form.phone}`;
      const confirmationResult = await signInWithPhoneNumber(auth, phoneWithCountry, recaptchaVerifier);

      setVerificationId(confirmationResult.verificationId);
      toast.success("The OTP has been sent through SMS.");
      setCountdown(30);
      setStep("otp");
    } catch (error: any) {
      console.error("Firebase sendOTP error:", error);
      toast.error(error.message || "Failed to send OTP. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const verifyOtpAndSignup = async () => {
    if (!otp || otp.length < 6) {
      return toast.error("Please enter the 6-digit OTP");
    }
    if (!verificationId) {
      return toast.error("OTP session expired. Please request again.");
    }

    setLoading(true);
    try {
      // Verify OTP with Firebase
      const credential = PhoneAuthProvider.credential(verificationId, otp);
      const firebaseAuth = getFirebaseAuth();
      if (!firebaseAuth) {
        throw new Error("Firebase auth not initialized");
      }

      const result = await signInWithCredential(firebaseAuth, credential);
      const idToken = await result.user.getIdToken();

      // Verify with backend using Firebase token
      await api.verifyFirebase(form.phone, idToken, "rider");

      // Submit rider signup with documents
      const fd = new FormData();
      fd.append("name", form.name);
      fd.append("phone", form.phone);
      fd.append("dl_number", form.dl_number);
      fd.append("pan_number", form.pan_number);
      fd.append("rc_number", form.rc_number);
      fd.append("plate", form.plate);
      fd.append("aadhaar", form.aadhaar);
      fd.append("vehicle_type", form.vehicle_type);
      if (form.files.dl_photo) fd.append("dl_photo", form.files.dl_photo);
      if (form.files.rc_photo) fd.append("rc_photo", form.files.rc_photo);
      if (form.files.aadhaar_photo) fd.append("aadhaar_photo", form.files.aadhaar_photo);
      if (form.files.pan_photo) fd.append("pan_photo", form.files.pan_photo);

      const { token, user } = await api.riderSignup(fd);
      auth.setToken(token);
      auth.setRole("rider");
      auth.setUser({ ...user, verification_status: user.verification_status ?? "pending" });
      toast.success("Account created successfully!");
      navigate({ to: "/driver/pending" });
    } catch (error: any) {
      console.error("Verification error:", error);
      toast.error(error.message || "Verification failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const resendOtp = async () => {
    if (countdown > 0) {
      toast.info(`You can resend OTP in ${countdown} sec`);
      return;
    }
    setLoading(true);
    try {
      await resendFirebaseOtp(form.phone);
      toast.success("The OTP has been resent through SMS.");
      setCountdown(30);
    } catch (error: any) {
      console.error("Resend OTP error:", error);
      toast.error(error.message || "Failed to resend OTP");
    } finally {
      setLoading(false);
    }
  };

  // Step 2: OTP Verification Screen
  if (step === "otp") {
    return (
      <div className="min-h-screen px-6 py-6">
        <header className="flex items-center justify-between">
          <Brand />
          <span className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Driver</span>
        </header>
        <main className="mx-auto mt-12 max-w-md pb-12 scale-in">
          <button
            onClick={() => setStep("form")}
            className="mb-4 text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            ← Back
          </button>

          <h1 className="font-display text-3xl font-bold">Enter OTP</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Enter the OTP sent to your number <span className="font-semibold text-foreground">{maskPhone(form.phone)}</span>
          </p>

          <div className="glass mt-6 space-y-4 rounded-2xl p-5">
            <Field
              label="OTP Code"
              inputMode="numeric"
              value={otp}
              maxLength={6}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="Enter 6-digit OTP"
            />

            {/* Invisible reCAPTCHA container for resend */}
            <div id="recaptcha-resend-otp" className="hidden"></div>

            <Btn onClick={verifyOtpAndSignup} disabled={loading || otp.length < 6} className="w-full">
              {loading ? "Verifying…" : "Verify & Create Account"}
            </Btn>

            <div className="text-center">
              <button
                onClick={resendOtp}
                disabled={loading || countdown > 0}
                className="text-sm text-muted-foreground hover:text-foreground underline disabled:opacity-50"
              >
                {countdown > 0 ? `Resend OTP in ${countdown}s` : "Resend OTP"}
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // Step 1: Form Screen
  return (
    <div className="min-h-screen px-6 py-6">
      <header className="flex items-center justify-between">
        <Brand />
        <span className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Driver</span>
      </header>
      <main className="mx-auto mt-8 max-w-md pb-12 scale-in">
        <h1 className="font-display text-3xl font-bold">Driver onboarding</h1>
        <p className="mt-1 text-sm text-muted-foreground">Verify once. Drive forever.</p>

        <div className="glass mt-6 space-y-4 rounded-2xl p-5">
          <Field label="Full name" value={form.name} onChange={set("name")} placeholder="As per ID" />
          <Field
            label="Mobile"
            inputMode="tel"
            value={form.phone}
            onChange={(e) =>
              setForm((f) => ({ ...f, phone: e.target.value.replace(/\D/g, "").slice(0, 10) }))
            }
            placeholder="10-digit mobile number"
          />

          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Vehicle type <span className="text-destructive">*</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {VEHICLES.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setForm((f) => ({ ...f, vehicle_type: v.id }))}
                  className={`lift press rounded-xl px-2 py-3 text-center text-sm font-semibold transition ${
                    form.vehicle_type === v.id
                      ? "bg-foreground text-background"
                      : "hairline bg-surface-2 hover:bg-elevated"
                  }`}
                >
                  <div className="text-xl">{v.emoji}</div>
                  <div>{v.label}</div>
                </button>
              ))}
            </div>
          </div>

          <Field
            label="Driving licence number *"
            value={form.dl_number}
            onChange={set("dl_number")}
            placeholder="DL-XXXXXXXXX"
          />
          <Field
            label="Aadhaar number *"
            inputMode="numeric"
            value={form.aadhaar}
            onChange={(e) =>
              setForm((f) => ({ ...f, aadhaar: e.target.value.replace(/\D/g, "").slice(0, 12) }))
            }
            placeholder="12-digit Aadhaar"
          />
          <Field
            label="PAN number *"
            value={form.pan_number}
            onChange={(e) =>
              setForm((f) => ({ ...f, pan_number: e.target.value.toUpperCase().slice(0, 10) }))
            }
            placeholder="ABCDE1234F"
          />
          <Field label="RC number *" value={form.rc_number} onChange={set("rc_number")} placeholder="Enter RC number" />
          <Field
            label="Number plate *"
            value={form.plate}
            onChange={(e) =>
              setForm((f) => ({ ...f, plate: e.target.value.toUpperCase() }))
            }
            placeholder="MH01AB1234"
          />
        </div>

        <div className="glass mt-4 space-y-3 rounded-2xl p-5">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Upload documents</div>
          <Upload
            label="Driving Licence"
            file={form.files.dl_photo}
            onChange={(file) => setForm((f) => ({ ...f, files: { ...f.files, dl_photo: file } }))}
          />
          <Upload
            label="PAN Card"
            file={form.files.pan_photo}
            onChange={(file) => setForm((f) => ({ ...f, files: { ...f.files, pan_photo: file } }))}
          />
          <Upload
            label="Aadhaar Card"
            file={form.files.aadhaar_photo}
            onChange={(file) => setForm((f) => ({ ...f, files: { ...f.files, aadhaar_photo: file } }))}
          />
          <Upload
            label="Registration Certificate (RC)"
            file={form.files.rc_photo}
            onChange={(file) => setForm((f) => ({ ...f, files: { ...f.files, rc_photo: file } }))}
          />
        </div>

        <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-xl glass p-4 text-sm">
          <input
            type="checkbox"
            checked={form.agree}
            onChange={(e) => setForm((f) => ({ ...f, agree: e.target.checked }))}
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
            </button>{" "}
            and confirm that all submitted documents are authentic.
          </span>
        </label>

        {/* Invisible reCAPTCHA containers */}
        <div id="recaptcha-send-otp" className="hidden"></div>
        <div id="recaptcha-resend-otp-form" className="hidden"></div>

        <Btn onClick={sendOtp} disabled={loading || !form.agree} className="mt-4 w-full">
          {loading ? "Sending OTP…" : "Send OTP"}
        </Btn>

        <button
          onClick={async () => {
            if (countdown > 0) {
              toast.info(`You can resend OTP in ${countdown} sec`);
              return;
            }
            if (!form.agree) {
              toast.error("Please accept the Terms & Conditions");
              return;
            }
            setLoading(true);
            try {
              await resendFirebaseOtp(form.phone);
              setVerificationId(null); // Will be set by resendFirebaseOtp internally
              toast.success("The OTP has been resent through SMS.");
              setCountdown(30);
            } catch (error: any) {
              toast.error(error.message || "Failed to resend OTP");
            } finally {
              setLoading(false);
            }
          }}
          disabled={loading || countdown > 0}
          className="mt-2 block w-full text-center text-sm text-muted-foreground hover:text-foreground underline disabled:opacity-50"
        >
          {countdown > 0 ? `Resend OTP in ${countdown}s` : "Resend OTP"}
        </button>

        <p className="mt-2 text-center text-xs text-muted-foreground">
          The OTP has been sent through SMS to verify your number.
        </p>
      </main>

      <AnimatePresence>
        {showTerms && (
          <TermsModal
            onClose={() => setShowTerms(false)}
            onAgree={() => {
              setForm((f) => ({ ...f, agree: true }));
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
        <p className="mt-1 text-sm text-muted-foreground">For Drivers using RIDES4U</p>

        <div className="mt-4 max-h-[60vh] overflow-y-auto rounded-xl bg-surface-2 p-4 hairline text-sm space-y-4">
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

function Upload({ label, file, onChange }: { label: string; file?: File; onChange: (f: File) => void }) {
  return (
    <label className="lift block cursor-pointer rounded-xl bg-surface-2 p-4 hairline transition hover:bg-elevated">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium">{label}</div>
          <div className="text-xs text-muted-foreground">{file ? file.name : "Tap to upload (jpg, png, pdf)"}</div>
        </div>
        <span className="rounded-md bg-foreground px-3 py-1.5 text-xs font-semibold text-background">
          {file ? "Replace" : "Upload"}
        </span>
      </div>
      <input
        type="file"
        accept="image/*,application/pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && onChange(e.target.files[0])}
      />
    </label>
  );
}

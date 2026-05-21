import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { auth } from "@/lib/api";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "RIDES4U — Hail a ride in seconds" },
      { name: "description", content: "Choose your language and start riding or earning with RIDES4U." },
    ],
  }),
  component: Landing,
});

function Landing() {
  const navigate = useNavigate();
  const [lang, setLang] = useState<"en" | "hi">(auth.language);

  useEffect(() => {
    // If already logged in, fast-forward to the right home.
    // Small delay to ensure localStorage is synced after login
    const checkAuth = setTimeout(() => {
      const token = auth.token;
      const role = auth.role;
      if (token && role === "passenger") navigate({ to: "/passenger" });
      else if (token && role === "rider") navigate({ to: "/driver" });
    }, 50);

    return () => clearTimeout(checkAuth);
  }, [navigate]);

  const choose = (l: "en" | "hi") => {
    setLang(l);
    auth.setLanguage(l);
    // Dispatch storage event to notify other components
    window.dispatchEvent(new StorageEvent("storage", { key: "rides4u_lang" }));
  };

  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="grid-bg pointer-events-none absolute inset-0 opacity-40" />
      <header className="relative z-10 flex items-center px-6 py-5">
        <span className="font-display text-2xl font-bold tracking-tight">RIDES4U</span>
      </header>

      <main className="relative z-10 mx-auto flex max-w-md flex-col items-center px-6 pt-6 pb-16">
        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="text-center font-display text-4xl font-bold leading-tight"
        >
          Move the city.
          <br />
          <span className="text-muted-foreground">With comfort.</span>
        </motion.h1>

        <section className="mt-10 w-full">
          <p className="mb-3 text-center text-xs uppercase tracking-[0.2em] text-muted-foreground">
            {lang === "en" ? "Choose your language" : "अपनी भाषा चुनें"}
          </p>
          <div className="grid grid-cols-2 gap-3">
            {(["en", "hi"] as const).map((l) => (
              <button
                key={l}
                onClick={() => choose(l)}
                className={`rounded-xl px-4 py-4 text-left transition border ${
                  lang === l ? "bg-foreground text-background border-foreground" : "bg-card text-foreground border-border hover:bg-muted"
                }`}
              >
                <div className="font-display text-lg font-semibold">
                  {l === "en" ? "English" : "हिन्दी"}
                </div>
                <div className={`text-xs ${lang === l ? "text-background/70" : "text-muted-foreground"}`}>
                  {l === "en" ? "Continue in English" : "हिन्दी में जारी रखें"}
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="mt-10 w-full">
          <p className="mb-3 text-center text-xs uppercase tracking-[0.2em] text-muted-foreground">
            {lang === "en" ? "I am a…" : "मैं हूँ…"}
          </p>
          <div className="flex flex-col gap-4">
            <RoleCard
              title={lang === "en" ? "Passenger" : "यात्री"}
              sub={lang === "en" ? "Book bikes, autos and e-rickshaws instantly." : "बाइक, ऑटो और ई-रिक्शा तुरंत बुक करें।"}
              cta={lang === "en" ? "Ride with us" : "हमारे साथ चलें"}
              signInLabel={lang === "en" ? "Sign in" : "साइन इन"}
              signUpLabel={lang === "en" ? "Sign up" : "साइन अप"}
              onSignIn={() => navigate({ to: "/auth/login", search: { role: "passenger", mode: "signin" } })}
              onSignUp={() => navigate({ to: "/auth/login", search: { role: "passenger", mode: "signup" } })}
            />
            <RoleCard
              title={lang === "en" ? "Rider" : "ड्राइवर"}
              sub={lang === "en" ? "Drive when you want. Get paid the same day." : "जब चाहें चलाएँ। उसी दिन भुगतान पाएँ।"}
              cta={lang === "en" ? "Start earning with us" : "हमारे साथ कमाई शुरू करें"}
              signInLabel={lang === "en" ? "Sign in" : "साइन इन"}
              signUpLabel={lang === "en" ? "Sign up" : "साइन अप"}
              dark
              onSignIn={() => navigate({ to: "/auth/login", search: { role: "rider", mode: "signin" } })}
              onSignUp={() => navigate({ to: "/auth/rider-signup" })}
            />
          </div>
        </section>
      </main>
    </div>
  );
}

function RoleCard({
  title,
  sub,
  cta,
  dark,
  signInLabel,
  signUpLabel,
  onSignIn,
  onSignUp,
}: {
  title: string;
  sub: string;
  cta: string;
  dark?: boolean;
  signInLabel: string;
  signUpLabel: string;
  onSignIn: () => void;
  onSignUp: () => void;
}) {
  return (
    <div
      className={`group relative overflow-hidden rounded-2xl p-5 transition ${
        dark ? "bg-foreground text-background" : "glass text-foreground"
      }`}
    >
      <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-foreground/5 blur-2xl transition-transform duration-500 group-hover:scale-150" />
      <div className="relative">
        <div className="font-display text-2xl font-bold">{title}</div>
        <div className={`mt-1 text-sm ${dark ? "text-background/70" : "text-muted-foreground"}`}>{sub}</div>
        <div className={`mt-3 text-[10px] uppercase tracking-[0.2em] ${dark ? "text-background/60" : "text-muted-foreground"}`}>
          {cta}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <button
            onClick={onSignIn}
            className={`lift press rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
              dark
                ? "bg-background/10 text-background hover:bg-background/20"
                : "hairline bg-surface-2 hover:bg-elevated"
            }`}
          >
            {signInLabel}
          </button>
          <button
            onClick={onSignUp}
            className={`lift press rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
              dark
                ? "bg-background text-foreground hover:bg-background/90"
                : "bg-foreground text-background hover:bg-foreground/90"
            }`}
          >
            {signUpLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

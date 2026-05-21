import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Brand } from "@/components/Brand";
import { Btn } from "@/components/Field";
import { ReportIssueButton } from "@/components/ReportIssueModal";
import { OverviewButton } from "@/components/OnboardingModal";
import { api, auth, type Language } from "@/lib/api";
import { MessageSquare, ChevronRight, Clock, CheckCircle, AlertCircle } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

type Ticket = {
  id: number;
  topic_display: string;
  description: string;
  status: string;
  status_display: string;
  priority: string;
  admin_response: string | null;
  created_at: string;
  ride_info: {
    id: number;
    pickup: string;
    drop: string;
    date: string;
  } | null;
};

export const Route = createFileRoute("/driver/settings")({
  head: () => ({ meta: [{ title: "Account — RIDES4U Driver" }] }),
  component: Settings,
});

function Settings() {
  const navigate = useNavigate();
  const [lang, setLang] = useState<Language>(auth.language);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [showTickets, setShowTickets] = useState(false);
  const user = auth.user;

  useEffect(() => {
    fetchTickets();
  }, []);

  // Listen for language changes from other components
  useEffect(() => {
    const handleStorage = () => {
      setLang(auth.language);
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const fetchTickets = async () => {
    try {
      setLoadingTickets(true);
      const response = await api.getMyTickets();
      if (response.success) {
        setTickets(response.tickets);
      }
    } catch (error) {
      console.error("Error fetching tickets:", error);
    } finally {
      setLoadingTickets(false);
    }
  };

  const updateLang = async (l: Language) => {
    setLang(l);
    auth.setLanguage(l);
    // Dispatch storage event to notify other components
    window.dispatchEvent(new StorageEvent("storage", { key: "rides4u_lang" }));
    try { await api.setLanguage(l); toast.success("Language updated"); }
    catch { /* still saved locally */ }
  };

  const logout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    auth.clear();
    navigate({ to: "/" });
  };

  return (
    <div className="min-h-screen pb-10">
      <header className="sticky top-0 z-20 flex items-center justify-between bg-background/70 px-5 py-4 backdrop-blur-md">
        <Brand to="/driver" />
        <Link to="/driver" className="text-sm text-muted-foreground hover:text-foreground">← Home</Link>
      </header>
      <main className="px-4">
        <h1 className="font-display text-2xl font-bold">Account</h1>
        {user && (
          <div className="mt-4 glass rounded-2xl p-5">
            <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Signed in as</div>
            <div className="font-display text-xl font-bold">{user.name}</div>
            <div className="text-sm text-muted-foreground">+91 {user.phone}</div>
            {user.verification_status && (
              <div className="mt-2">
                <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  user.verification_status === "approved"
                    ? "bg-emerald-500/20 text-emerald-400"
                    : user.verification_status === "rejected"
                    ? "bg-destructive/20 text-destructive"
                    : "bg-amber-500/20 text-amber-400"
                }`}>
                  {user.verification_status === "approved" ? "✓ Verified" 
                    : user.verification_status === "rejected" ? "✗ Rejected"
                    : "⏳ Pending Verification"}
                </span>
              </div>
            )}
          </div>
        )}

        <div className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Change language</div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            {(["en", "hi"] as const).map((l) => (
              <button key={l} onClick={() => updateLang(l)}
                className={`rounded-xl p-3 text-sm font-semibold transition ${lang === l ? "bg-foreground text-background" : "bg-surface-2 border border-border hover:bg-elevated"}`}>
                {l === "en" ? "English" : "हिन्दी"}
              </button>
            ))}
          </div>
        </div>

        {/* Support & Help Section */}
        <div className="mt-4 glass rounded-2xl p-5">
          <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">Help & Support</div>
          <div className="mt-3 flex flex-col gap-2">
            <OverviewButton userType="driver" />
            <ReportIssueButton userType="driver" />
            <button
              onClick={() => setShowTickets(!showTickets)}
              className="flex items-center justify-between px-4 py-3 rounded-xl bg-card border border-border hover:border-primary/40 hover:bg-accent transition-all duration-200"
            >
              <div className="flex items-center gap-3">
                <MessageSquare className="w-5 h-5 text-primary" />
                <span className="font-medium">My Support Tickets</span>
                {tickets.length > 0 && (
                  <span className="px-2 py-0.5 rounded-full bg-primary/20 text-primary text-xs font-medium">
                    {tickets.length}
                  </span>
                )}
              </div>
              <ChevronRight className={`w-5 h-5 text-muted-foreground transition-transform ${showTickets ? "rotate-90" : ""}`} />
            </button>
          </div>

          {/* Tickets List */}
          <AnimatePresence>
            {showTickets && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="mt-3 pt-3 border-t border-border">
                  {loadingTickets ? (
                    <div className="text-center py-4 text-muted-foreground">Loading...</div>
                  ) : tickets.length === 0 ? (
                    <div className="text-center py-4 text-muted-foreground text-sm">
                      No support tickets yet
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-[300px] overflow-y-auto">
                      {tickets.map((ticket) => (
                        <div
                          key={ticket.id}
                          className="p-3 rounded-xl bg-card border border-border"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1 min-w-0">
                              <p className="font-medium text-sm truncate">{ticket.topic_display}</p>
                              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                {ticket.description}
                              </p>
                              {ticket.ride_info && (
                                <p className="text-xs text-muted-foreground mt-1">
                                  Ride #{ticket.ride_info.id}
                                </p>
                              )}
                            </div>
                            <StatusBadge status={ticket.status} />
                          </div>
                          {ticket.admin_response && (
                            <div className="mt-2 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                              <p className="text-xs text-emerald-400 font-medium">Response:</p>
                              <p className="text-xs text-muted-foreground mt-0.5">{ticket.admin_response}</p>
                            </div>
                          )}
                          <p className="text-xs text-muted-foreground mt-2">
                            {new Date(ticket.created_at).toLocaleDateString()}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="mt-4 grid gap-2">
          <Link to="/driver/history" className="rounded-xl bg-card border border-border p-4 hover:bg-muted transition-colors">Ride history →</Link>
          <Link to="/driver/earnings" className="rounded-xl bg-card border border-border p-4 hover:bg-muted transition-colors">Earnings & Wallet →</Link>
        </div>

        <Btn variant="danger" className="mt-5 w-full" onClick={logout}>Logout</Btn>
      </main>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const configs: Record<string, { icon: React.ReactNode; className: string; label: string }> = {
    pending: { icon: <Clock className="w-3 h-3" />, className: "bg-amber-500/15 text-amber-500 border-amber-500/30", label: "Pending" },
    open: { icon: <Clock className="w-3 h-3" />, className: "bg-amber-500/15 text-amber-500 border-amber-500/30", label: "Open" },
    in_progress: { icon: <AlertCircle className="w-3 h-3" />, className: "bg-blue-500/15 text-blue-400 border-blue-500/30", label: "In Progress" },
    resolved: { icon: <CheckCircle className="w-3 h-3" />, className: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30", label: "Resolved" },
    closed: { icon: <CheckCircle className="w-3 h-3" />, className: "bg-muted text-muted-foreground border-border", label: "Closed" },
  };
  
  const config = configs[status] || configs.pending;
  
  return (
    <span className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium border ${config.className}`}>
      {config.icon}
      {config.label}
    </span>
  );
}

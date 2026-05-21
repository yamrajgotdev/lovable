import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Btn } from "@/components/Field";
import { api } from "@/lib/api";
import { X, AlertTriangle, ChevronDown, MessageSquare, Car, FileText } from "lucide-react";

interface ReportIssueModalProps {
  isOpen: boolean;
  onClose: () => void;
  userType: "driver" | "passenger";
}

type Topic = {
  value: string;
  label: string;
  requires_ride: boolean;
};

type Ride = {
  id: number;
  pickup_address: string;
  drop_address: string;
  date: string;
  completed_at?: string;
  fare: number;
  status: string;
  driver_name?: string;
  passenger_name?: string;
};

export function ReportIssueModal({ isOpen, onClose, userType }: ReportIssueModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [rides, setRides] = useState<Ride[]>([]);
  const [selectedTopic, setSelectedTopic] = useState<string>("");
  const [selectedRide, setSelectedRide] = useState<number | null>(null);
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [fetchingRides, setFetchingRides] = useState(false);

  // Fetch topics on open
  useEffect(() => {
    if (isOpen) {
      fetchTopics();
      setStep(1);
      setSelectedTopic("");
      setSelectedRide(null);
      setDescription("");
    }
  }, [isOpen, userType]);

  // Fetch rides when topic requires it
  const selectedTopicData = topics.find((t) => t.value === selectedTopic);
  const requiresRide = selectedTopicData?.requires_ride ?? false;

  useEffect(() => {
    if (isOpen && requiresRide && rides.length === 0) {
      fetchRides();
    }
  }, [isOpen, requiresRide]);

  const fetchTopics = async () => {
    try {
      const response = await api.getTicketTopics(userType);
      if (response.success) {
        setTopics(response.topics);
      }
    } catch (error) {
      console.error("Error fetching topics:", error);
      toast.error("Failed to load topics");
    }
  };

  const fetchRides = async () => {
    try {
      setFetchingRides(true);
      const response = await api.getUserRidesForTicket(userType);
      if (response.success) {
        setRides(response.rides);
      }
    } catch (error) {
      console.error("Error fetching rides:", error);
      toast.error("Failed to load rides");
    } finally {
      setFetchingRides(false);
    }
  };

  const handleTopicSelect = (topicValue: string) => {
    setSelectedTopic(topicValue);
    const topic = topics.find((t) => t.value === topicValue);
    if (topic?.requires_ride) {
      setStep(2);
    } else {
      setStep(3);
    }
  };

  const handleRideSelect = (rideId: number) => {
    setSelectedRide(rideId);
    setStep(3);
  };

  const handleSubmit = async () => {
    if (!description.trim()) {
      toast.error("Please describe your issue");
      return;
    }

    if (requiresRide && !selectedRide) {
      toast.error("Please select a ride");
      return;
    }

    try {
      setLoading(true);
      const response = await api.createSupportTicket({
        topic: selectedTopic,
        description: description.trim(),
        user_type: userType,
        ride_id: requiresRide ? selectedRide : null,
      });

      if (response.success) {
        toast.success("Issue reported successfully! We'll get back to you soon.");
        onClose();
      } else {
        toast.error(response.message || "Failed to submit");
      }
    } catch (error: any) {
      console.error("Error submitting ticket:", error);
      toast.error(error?.message || "Failed to submit issue");
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    if (step === 3) {
      if (requiresRide) {
        setStep(2);
      } else {
        setStep(1);
      }
    } else if (step === 2) {
      setStep(1);
      setSelectedTopic("");
      setSelectedRide(null);
    }
  };

  const formatRideDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop - solid dark */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/40 z-[9999]"
          />

          {/* Modal - isolate creates new stacking context */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className="fixed inset-4 sm:inset-auto sm:top-1/2 sm:left-1/2 sm:-translate-x-1/2 sm:-translate-y-1/2 sm:w-full sm:max-w-lg sm:max-h-[85vh] bg-background rounded-3xl z-[2147483647] isolate overflow-hidden flex flex-col border border-border shadow-2xl"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-4 border-b border-border bg-background">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-amber-500" />
                </div>
                <h2 className="font-semibold text-lg">Report an Issue</h2>
              </div>
              <button
                onClick={onClose}
                className="w-9 h-9 rounded-xl bg-secondary border border-border hover:border-rose-400/50 hover:bg-rose-500/10 hover:text-rose-400 flex items-center justify-center transition-all duration-200 active:scale-95"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Progress Indicator */}
            <div className="flex items-center gap-2 px-4 py-3 bg-secondary/50">
              <div className={`h-1.5 flex-1 rounded-full ${step >= 1 ? "bg-primary" : "bg-border"}`} />
              <div className={`h-1.5 flex-1 rounded-full ${step >= 2 ? "bg-primary" : "bg-border"}`} />
              <div className={`h-1.5 flex-1 rounded-full ${step >= 3 ? "bg-primary" : "bg-border"}`} />
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4">
              {/* Step 1: Select Topic */}
              {step === 1 && (
                <div className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    What issue are you facing? Select a topic below.
                  </p>

                  <div className="grid gap-2">
                    {topics.map((topic) => (
                      <button
                        key={topic.value}
                        onClick={() => handleTopicSelect(topic.value)}
                        className={`p-4 rounded-xl text-left transition-all duration-200 flex items-center gap-3 border-2 active:scale-[0.98] ${
                          selectedTopic === topic.value
                            ? "bg-muted border-primary shadow-sm"
                            : "bg-card border-border hover:border-primary/40 hover:bg-muted hover:shadow-sm"
                        }`}
                      >
                        <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 border ${
                          selectedTopic === topic.value ? "bg-primary/20 border-primary/30" : "bg-background border-border"
                        }`}>
                          {getTopicIcon(topic.value)}
                        </div>
                        <div className="flex-1">
                          <p className="font-medium">{topic.label}</p>
                          {topic.requires_ride && (
                            <p className="text-xs text-muted-foreground">Requires ride selection</p>
                          )}
                        </div>
                        <ChevronDown className="w-5 h-5 rotate-[-90deg] text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Step 2: Select Ride */}
              {step === 2 && (
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <button onClick={goBack} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-secondary border border-border hover:border-primary/40 hover:bg-muted transition-all duration-200 text-sm font-medium">
                      <span>←</span> Back
                    </button>
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Select the ride related to this issue:
                  </p>

                  {fetchingRides ? (
                    <div className="text-center py-8 px-4 rounded-xl bg-card border border-border">
                      <div className="w-14 h-14 rounded-2xl bg-muted border border-border flex items-center justify-center mx-auto mb-3">
                        <div className="animate-spin w-7 h-7 border-2 border-primary border-t-transparent rounded-full" />
                      </div>
                      <p className="text-sm font-medium">Loading your rides...</p>
                      <p className="text-xs text-muted-foreground mt-1">Please wait a moment</p>
                    </div>
                  ) : rides.length === 0 ? (
                    <div className="text-center py-8 px-4 rounded-xl bg-card border border-border">
                      <div className="w-14 h-14 rounded-2xl bg-muted border border-border flex items-center justify-center mx-auto mb-3">
                        <Car className="w-7 h-7 text-muted-foreground" />
                      </div>
                      <p className="text-sm font-medium">No recent rides found</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        You can still report this issue without selecting a ride
                      </p>
                      <button
                        className="mt-4 px-4 py-2 rounded-xl bg-muted border border-border hover:border-primary/40 hover:bg-accent transition-all duration-200 text-sm font-medium"
                        onClick={() => setStep(3)}
                      >
                        Continue without ride
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-[400px] overflow-y-auto">
                      {rides.map((ride) => (
                        <button
                          key={ride.id}
                          onClick={() => handleRideSelect(ride.id)}
                          className={`w-full p-3 rounded-xl text-left transition-all duration-200 border-2 active:scale-[0.98] ${
                            selectedRide === ride.id
                              ? "bg-muted border-primary shadow-sm"
                              : "bg-card border-border hover:border-primary/40 hover:bg-muted hover:shadow-sm"
                          }`}
                        >
                          <div className="flex items-start gap-3">
                            <div className="w-10 h-10 rounded-xl bg-background border border-border flex items-center justify-center flex-shrink-0">
                              <Car className="w-5 h-5 text-primary" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate">{ride.pickup_address}</p>
                              <p className="text-sm text-muted-foreground truncate">→ {ride.drop_address}</p>
                              <div className="flex items-center gap-2 mt-1">
                                <span className="text-xs text-muted-foreground">
                                  {formatRideDate(ride.date)}
                                </span>
                                <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400">
                                  ₹{ride.fare.toFixed(0)}
                                </span>
                              </div>
                              {userType === "passenger" && ride.driver_name && (
                                <p className="text-xs text-muted-foreground mt-1">
                                  Driver: {ride.driver_name}
                                </p>
                              )}
                              {userType === "driver" && ride.passenger_name && (
                                <p className="text-xs text-muted-foreground mt-1">
                                  Passenger: {ride.passenger_name}
                                </p>
                              )}
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}

                  {rides.length > 0 && (
                    <Btn variant="outline" className="w-full" onClick={() => setStep(3)}>
                      Continue without selecting ride
                    </Btn>
                  )}
                </div>
              )}

              {/* Step 3: Description */}
              {step === 3 && (
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <button onClick={goBack} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-secondary border border-border hover:border-primary/40 hover:bg-muted transition-all duration-200 text-sm font-medium">
                      <span>←</span> Back
                    </button>
                  </div>

                  {/* Summary */}
                  <div className="p-4 rounded-xl bg-card border border-border space-y-3 shadow-sm">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center">
                        <FileText className="w-4 h-4 text-primary" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <span className="text-xs text-muted-foreground uppercase tracking-wide">Topic</span>
                        <p className="text-sm font-medium truncate">{selectedTopicData?.label}</p>
                      </div>
                    </div>
                    {selectedRide && (
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
                          <Car className="w-4 h-4 text-emerald-500" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <span className="text-xs text-muted-foreground uppercase tracking-wide">Ride</span>
                          <p className="text-sm font-medium truncate">
                            #{selectedRide} - {rides.find((r) => r.id === selectedRide)?.pickup_address}
                          </p>
                        </div>
                      </div>
                    )}
                  </div>

                  <p className="text-sm text-muted-foreground">
                    Please describe your issue in detail:
                  </p>

                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Describe what happened... Be as specific as possible so we can help you better."
                    className="w-full h-32 p-4 rounded-xl bg-background border border-border resize-none focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-none text-sm transition-all duration-200"
                  />

                  <p className="text-xs text-muted-foreground">
                    Your ticket will be reviewed by our support team within 24 hours.
                  </p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="p-4 border-t border-border/50">
              {step === 3 && (
                <Btn
                  onClick={handleSubmit}
                  disabled={loading || !description.trim()}
                  className="w-full"
                >
                  {loading ? "Submitting..." : "Submit Report"}
                </Btn>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

function getTopicIcon(topicValue: string) {
  const icons: Record<string, React.ReactNode> = {
    report_passenger: <AlertTriangle className="w-5 h-5 text-rose-400" />,
    report_driver: <AlertTriangle className="w-5 h-5 text-rose-400" />,
    passenger_abuse: <AlertTriangle className="w-5 h-5 text-rose-400" />,
    driver_abuse: <AlertTriangle className="w-5 h-5 text-rose-400" />,
    missing_payment: <span className="text-xl">💰</span>,
    money_not_received: <span className="text-xl">💸</span>,
    overcharged: <span className="text-xl">💵</span>,
    glitch: <span className="text-xl">🐛</span>,
    ride_issue: <Car className="w-5 h-5 text-primary" />,
    app_not_working: <span className="text-xl">📱</span>,
    account_issue: <span className="text-xl">👤</span>,
    other: <MessageSquare className="w-5 h-5 text-muted-foreground" />,
  };
  return icons[topicValue] || <MessageSquare className="w-5 h-5 text-muted-foreground" />;
}

// Floating button component for report issue
export function ReportIssueButton({ userType }: { userType: "driver" | "passenger" }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-card border border-border hover:border-primary/50 hover:bg-muted active:scale-[0.98] transition-all duration-200 text-sm font-medium shadow-sm hover:shadow-md"
      >
        <AlertTriangle className="w-4 h-4 text-amber-500" />
        <span className="text-foreground">Report Issue</span>
      </button>

      <ReportIssueModal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        userType={userType}
      />
    </>
  );
}

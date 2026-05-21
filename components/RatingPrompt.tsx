import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { toast } from "sonner";

export function RatingPrompt({
  open,
  rideId,
  title,
  onClose,
}: {
  open: boolean;
  rideId: string | null;
  title: string;
  onClose: () => void;
}) {
  const [rating, setRating] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const normalizedRideId = String(rideId || "").trim();
    if (!normalizedRideId || !/^\d+$/.test(normalizedRideId)) {
      toast.error("This rating request expired. Please open the completed ride and try again.");
      onClose();
      return;
    }
    if (rating < 1) {
      toast.error("Please select a rating");
      return;
    }
    setBusy(true);
    try {
      // Backend ONLY accepts 'completed' status for rating submission
      // We must verify the ride is in 'completed' status before submitting
      let confirmed = false;
      let rideStatus = "";
      
      for (let attempt = 0; attempt < 3; attempt++) {
        // First try active ride endpoint
        try {
          const res = await api.ride(normalizedRideId);
          rideStatus = String(res.ride?.status || "").toLowerCase();
          // Backend requires EXACTLY 'completed' status
          if (rideStatus === "completed") {
            confirmed = true;
            break;
          }
          // If not completed, check history (completed rides may have moved to history)
          // Fall through to history check below
        } catch (err) {
          // Ride not found in active rides, it might be in history
          // Fall through to history check below
        }
        
        // Check history - completed rides are moved here
        try {
          const historyRes = await api.history();
          const rideInHistory = historyRes.rides?.find((r: any) => String(r.id) === normalizedRideId);
          if (rideInHistory) {
            rideStatus = String(rideInHistory.status || "").toLowerCase();
            if (rideStatus === "completed") {
              confirmed = true;
              break;
            }
          }
        } catch (historyErr) {
          // ignore history fetch errors
        }
        
        // Backoff before retry
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }

      if (!confirmed) {
        toast.error(`Ride status is "${rideStatus}". Rating is only allowed when ride is "completed". Please wait a moment and try again.`);
        return;
      }

      await api.submitRideRating(normalizedRideId, rating, feedback);
      toast.success("Thanks for your feedback");

      // Mark rating as submitted and clear pending rating from localStorage
      if (typeof window !== "undefined") {
        localStorage.removeItem("pending_rating_passenger_ride_id");
        localStorage.removeItem("pending_rating_driver_ride_id");
        localStorage.setItem(`rating_submitted_${normalizedRideId}`, "true");
      }

      onClose();
    } catch (e: any) {
      toast.error(e?.message || "Rating could not be submitted");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-end justify-center bg-background/70 backdrop-blur-sm"
          onClick={onClose}
        >
          <motion.div
            initial={{ y: 40 }}
            animate={{ y: 0 }}
            exit={{ y: 40 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md rounded-t-3xl bg-background p-5 ring-1 ring-border"
          >
            <div className="text-sm text-muted-foreground">{title}</div>
            <div className="mt-3 flex gap-2">
              {[1, 2, 3, 4, 5].map((v) => (
                <button key={v} className="text-2xl" onClick={() => setRating(v)}>
                  {v <= rating ? "★" : "☆"}
                </button>
              ))}
            </div>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="Optional feedback"
              className="mt-3 w-full rounded-lg bg-surface-2 p-2 text-sm outline-none ring-1 ring-border"
              rows={3}
            />
            <button
              onClick={submit}
              disabled={busy}
              className="mt-3 w-full rounded-lg bg-foreground py-2 text-sm font-semibold text-background"
            >
              {busy ? "Submitting..." : "Submit Rating"}
            </button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

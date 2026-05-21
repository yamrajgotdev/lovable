import { useState } from "react";
import { api } from "@/lib/api";
import { useTheme } from "@/hooks/useTheme";
import { AlertTriangle, Phone, MessageSquare, X } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface SOSContact {
  id: string;
  name: string;
  relationship: string;
  phone: string;
  isEmergency: boolean;
}

interface SOSButtonProps {
  rideId?: string;
  role?: "passenger" | "rider";
  emergencyContacts?: SOSContact[];
}

export function SOSButton({ rideId, role = "passenger", emergencyContacts = [] }: SOSButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [sosMessage, setSOSMessage] = useState("");
  const { theme } = useTheme();

  const handleSendSOS = async (contactId?: string) => {
    if (!sosMessage.trim()) {
      toast.error("Please describe the emergency");
      return;
    }

    try {
      setSending(true);
      // Send SOS alert to backend
      const response = await api.sendSOSAlert?.({
        ride_id: rideId,
        message: sosMessage,
        contact_id: contactId,
        latitude: (window as any).currentLat,
        longitude: (window as any).currentLng,
      }) ?? { success: false };

      if (response.success) {
        toast.success("SOS alert sent! Help is on the way.");
        setIsOpen(false);
        setSOSMessage("");
      } else {
        toast.error("Failed to send SOS alert. Please try again.");
      }
    } catch (error) {
      console.error("SOS error:", error);
      toast.error("Error sending SOS. Please call emergency services directly.");
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      {/* SOS Button */}
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-20 right-6 z-40 flex h-16 w-16 items-center justify-center rounded-full bg-destructive text-destructive-foreground shadow-2xl hover:opacity-90 transition-opacity animate-pulse ring-2 ring-destructive/30"
        aria-label="Emergency SOS"
        title="Tap for emergency help"
      >
        <AlertTriangle className="w-7 h-7" />
      </button>

      {/* SOS Modal */}
      <Dialog open={isOpen} onOpenChange={setIsOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-destructive flex items-center gap-2">
              <AlertTriangle className="w-5 h-5" />
              Emergency SOS
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Quick Call Buttons */}
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => window.location.href = "tel:112"}
                className="flex items-center justify-center gap-2 rounded-lg bg-red-500 text-white py-3 font-semibold hover:bg-red-600 transition-colors"
              >
                <Phone className="w-5 h-5" />
                Emergency
              </button>
              <button
                onClick={() => window.location.href = "tel:100"}
                className="flex items-center justify-center gap-2 rounded-lg bg-red-500 text-white py-3 font-semibold hover:bg-red-600 transition-colors"
              >
                <Phone className="w-5 h-5" />
                Police
              </button>
            </div>

            {/* Message Input */}
            <div>
              <label className="text-sm font-semibold mb-2 block">
                What's the emergency? (shared with admin & contacts)
              </label>
              <textarea
                value={sosMessage}
                onChange={(e) => setSOSMessage(e.target.value)}
                placeholder="Describe the situation..."
                maxLength={500}
                className="w-full h-24 rounded-lg border border-border bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-destructive"
              />
              <p className="text-xs text-muted-foreground mt-1">
                {sosMessage.length} / 500 characters
              </p>
            </div>

            {/* Emergency Contacts */}
            {emergencyContacts.length > 0 && (
              <div>
                <p className="text-sm font-semibold mb-2">Alert your contacts:</p>
                <div className="space-y-2">
                  {emergencyContacts.map((contact) => (
                    <button
                      key={contact.id}
                      onClick={() => handleSendSOS(contact.id)}
                      disabled={sending}
                      className="w-full flex items-center justify-between rounded-lg border border-border p-3 hover:bg-surface-2 transition-colors disabled:opacity-50"
                    >
                      <div className="text-left">
                        <p className="font-medium text-sm">{contact.name}</p>
                        <p className="text-xs text-muted-foreground">{contact.phone}</p>
                      </div>
                      {contact.isEmergency && (
                        <span className="text-xs bg-destructive/20 text-destructive px-2 py-1 rounded">
                          Emergency
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Admin Alert */}
            <button
              onClick={() => handleSendSOS()}
              disabled={sending || !sosMessage.trim()}
              className="w-full rounded-lg bg-destructive text-destructive-foreground py-3 font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {sending ? "Sending..." : "Alert Admin & Support"}
            </button>

            <p className="text-xs text-muted-foreground text-center">
              Your location will be shared with emergency responders and the admin.
            </p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

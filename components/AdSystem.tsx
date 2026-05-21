import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { useTheme } from "@/hooks/useTheme";
import { X } from "lucide-react";

export interface AdData {
  id: string;
  title: string;
  description: string;
  imageUrl?: string;
  actionUrl?: string;
  actionText?: string;
  backgroundColor?: string;
  textColor?: string;
}

interface AdSystemProps {
  rideId?: string;
  position?: "top" | "bottom" | "modal";
  onClose?: () => void;
}

export function AdSystem({ rideId, position = "top", onClose }: AdSystemProps) {
  const [ads, setAds] = useState<AdData[]>([]);
  const [currentAdIndex, setCurrentAdIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const { theme } = useTheme();

  useEffect(() => {
    loadAds();
  }, [rideId]);

  const loadAds = async () => {
    try {
      setLoading(true);
      // Fetch ads from API - this endpoint can be added to your backend
      // For now, we'll allow admins to set this up through their dashboard
      const response = await api.getActiveAds();
      if (response && response.ads && response.ads.length > 0) {
        setAds(response.ads);
        setDismissed(false);
      }
    } catch (error) {
      console.error("Failed to load ads:", error);
      setAds([]);
    } finally {
      setLoading(false);
    }
  };

  if (dismissed || ads.length === 0 || loading) {
    return null;
  }

  const currentAd = ads[currentAdIndex];

  const handleDismiss = () => {
    setDismissed(true);
    onClose?.();
  };

  const handleNextAd = () => {
    setCurrentAdIndex((prev) => (prev + 1) % ads.length);
  };

  const handlePrevAd = () => {
    setCurrentAdIndex((prev) => (prev - 1 + ads.length) % ads.length);
  };

  const handleAction = () => {
    if (currentAd.actionUrl) {
      window.open(currentAd.actionUrl, "_blank");
    }
  };

  if (position === "modal") {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
        <div className="relative max-w-md w-full rounded-2xl overflow-hidden bg-card shadow-2xl">
          <button
            onClick={handleDismiss}
            className="absolute top-3 right-3 z-10 rounded-lg bg-background/80 p-2 hover:bg-background transition-colors"
          >
            <X className="w-5 h-5" />
          </button>

          {currentAd.imageUrl && (
            <img
              src={currentAd.imageUrl}
              alt={currentAd.title}
              className="w-full h-48 object-cover"
            />
          )}

          <div
            className="p-4"
            style={{
              backgroundColor: currentAd.backgroundColor,
              color: currentAd.textColor,
            }}
          >
            <h3 className="font-bold text-lg mb-2">{currentAd.title}</h3>
            <p className="text-sm mb-4 opacity-90">{currentAd.description}</p>

            {currentAd.actionText && (
              <button
                onClick={handleAction}
                className="w-full rounded-lg bg-primary text-primary-foreground py-2 font-semibold hover:opacity-90 transition-opacity"
              >
                {currentAd.actionText}
              </button>
            )}
          </div>

          {ads.length > 1 && (
            <div className="flex items-center justify-between px-4 py-2 bg-surface border-t border-border">
              <button
                onClick={handlePrevAd}
                className="text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors"
              >
                ← Prev
              </button>
              <span className="text-xs text-muted-foreground">
                {currentAdIndex + 1} / {ads.length}
              </span>
              <button
                onClick={handleNextAd}
                className="text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors"
              >
                Next →
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`${position === "top" ? "sticky top-0 z-30" : "sticky bottom-0 z-30"} bg-card border-b border-border shadow-sm`}
    >
      <div className="flex items-center justify-between p-3 gap-3 max-w-full">
        {currentAd.imageUrl && (
          <img
            src={currentAd.imageUrl}
            alt={currentAd.title}
            className="h-12 w-12 rounded object-cover"
          />
        )}

        <div className="flex-1 min-w-0">
          <h4 className="font-semibold text-sm truncate">{currentAd.title}</h4>
          <p className="text-xs text-muted-foreground truncate line-clamp-1">
            {currentAd.description}
          </p>
        </div>

        {currentAd.actionText && (
          <button
            onClick={handleAction}
            className="flex-shrink-0 px-3 py-1 rounded text-xs font-semibold bg-primary text-primary-foreground hover:opacity-90 transition-opacity whitespace-nowrap"
          >
            {currentAd.actionText}
          </button>
        )}

        <button
          onClick={handleDismiss}
          className="flex-shrink-0 p-1 rounded hover:bg-surface-2 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

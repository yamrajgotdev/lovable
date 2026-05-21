import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { useTheme } from "@/hooks/useTheme";
import { Gift, MapPin, Clock, X } from "lucide-react";

export interface RewardData {
  id: string;
  title: string;
  description: string;
  icon: string;
  progress: number;
  maxProgress: number;
  isUnlocked: boolean;
  shopLocation?: { lat: number; lng: number; name: string };
  shopStatus?: "open" | "closed";
  expiryDate?: string;
}

interface RewardSystemProps {
  userId?: string;
  onNavigateToShop?: (location: { lat: number; lng: number; name: string }) => void;
}

export function RewardSystem({ userId, onNavigateToShop }: RewardSystemProps) {
  const [rewards, setRewards] = useState<RewardData[]>([]);
  const [selectedReward, setSelectedReward] = useState<RewardData | null>(null);
  const [loading, setLoading] = useState(false);
  const { theme } = useTheme();

  useEffect(() => {
    loadRewards();
  }, [userId]);

  const loadRewards = async () => {
    try {
      setLoading(true);
      // Fetch reward data from API
      const response = await api.getUserRewards?.() ?? { rewards: [] };
      if (response.rewards) {
        setRewards(response.rewards);
      }
    } catch (error) {
      console.error("Failed to load rewards:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleRedeemReward = async (rewardId: string) => {
    try {
      // Call API to redeem reward
      const response = await api.redeemReward?.(rewardId) ?? { success: false };
      if (response.success) {
        loadRewards();
        setSelectedReward(null);
      }
    } catch (error) {
      console.error("Failed to redeem reward:", error);
    }
  };

  const unlockedRewards = rewards.filter((r) => r.isUnlocked);
  const availableRewards = rewards.filter((r) => !r.isUnlocked);

  return (
    <>
      {/* Rewards Overview */}
      {availableRewards.length > 0 && (
        <div className="space-y-3">
          {availableRewards.map((reward) => (
            <motion.div
              key={reward.id}
              className="rounded-xl bg-gradient-to-r from-primary/10 to-accent/10 border border-border p-3 cursor-pointer hover:border-primary/50 transition-colors"
              onClick={() => setSelectedReward(reward)}
              whileHover={{ scale: 1.02 }}
            >
              <div className="flex items-center gap-3">
                <div className="text-2xl">{reward.icon}</div>
                <div className="flex-1">
                  <h4 className="font-semibold text-sm">{reward.title}</h4>
                  <p className="text-xs text-muted-foreground">{reward.description}</p>
                  {/* Progress bar */}
                  <div className="mt-2 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${(reward.progress / reward.maxProgress) * 100}%` }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {reward.progress} / {reward.maxProgress} rides
                  </p>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {/* Unlocked Rewards */}
      {unlockedRewards.length > 0 && (
        <div className="mt-4">
          <h3 className="text-sm font-semibold mb-2 text-foreground">Your Rewards</h3>
          <div className="grid grid-cols-2 gap-2">
            {unlockedRewards.map((reward) => (
              <motion.button
                key={reward.id}
                onClick={() => setSelectedReward(reward)}
                className="rounded-lg bg-primary/10 border border-primary/20 p-3 text-center hover:bg-primary/20 transition-colors"
                whileHover={{ scale: 1.05 }}
              >
                <div className="text-2xl mb-1">{reward.icon}</div>
                <p className="text-xs font-semibold text-foreground">{reward.title}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">Ready to claim</p>
              </motion.button>
            ))}
          </div>
        </div>
      )}

      {/* Reward Details Modal */}
      {selectedReward && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-4">
          <motion.div
            className="w-full max-w-sm rounded-2xl bg-card p-6 shadow-2xl"
            initial={{ y: 100, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
          >
            <button
              onClick={() => setSelectedReward(null)}
              className="absolute top-4 right-4 p-2 hover:bg-surface-2 rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="text-center mb-4">
              <div className="text-6xl mb-2">{selectedReward.icon}</div>
              <h3 className="text-2xl font-bold">{selectedReward.title}</h3>
            </div>

            <div className="space-y-3 mb-4">
              <p className="text-center text-muted-foreground">{selectedReward.description}</p>

              {selectedReward.isUnlocked ? (
                <>
                  {selectedReward.shopLocation && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-sm">
                        <MapPin className="w-4 h-4 text-primary" />
                        <span className="font-medium">{selectedReward.shopLocation.name}</span>
                      </div>
                      {selectedReward.shopStatus && (
                        <div className="flex items-center gap-2 text-sm">
                          <Clock className="w-4 h-4" />
                          <span
                            className={`font-medium ${
                              selectedReward.shopStatus === "open"
                                ? "text-emerald-500"
                                : "text-destructive"
                            }`}
                          >
                            {selectedReward.shopStatus === "open"
                              ? "Open now"
                              : "Closed"}
                          </span>
                        </div>
                      )}
                      {selectedReward.expiryDate && (
                        <p className="text-xs text-muted-foreground">
                          Expires: {new Date(selectedReward.expiryDate).toLocaleDateString()}
                        </p>
                      )}
                    </div>
                  )}

                  <button
                    onClick={() => {
                      if (selectedReward.shopLocation) {
                        onNavigateToShop?.(selectedReward.shopLocation);
                        setSelectedReward(null);
                      }
                    }}
                    className="w-full rounded-lg bg-primary text-primary-foreground py-3 font-semibold hover:opacity-90 transition-opacity"
                  >
                    Navigate to Shop
                  </button>
                </>
              ) : (
                <div className="text-center py-4">
                  <p className="text-sm font-semibold mb-2">
                    {selectedReward.maxProgress - selectedReward.progress} rides to unlock
                  </p>
                  <div className="h-2 bg-surface-2 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{
                        width: `${
                          (selectedReward.progress / selectedReward.maxProgress) * 100
                        }%`,
                      }}
                    />
                  </div>
                </div>
              )}
            </div>

            <button
              onClick={() => setSelectedReward(null)}
              className="w-full rounded-lg bg-surface-2 py-2 font-medium hover:bg-surface transition-colors"
            >
              Close
            </button>
          </motion.div>
        </div>
      )}
    </>
  );
}

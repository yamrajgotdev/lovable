import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { Navbar } from "@/components/Navbar";
import { Btn } from "@/components/Field";
import { api, auth } from "@/lib/api";
import { useTranslation } from "@/hooks/useTranslation";

export const Route = createFileRoute("/admin/pricing")({
  head: () => ({ meta: [{ title: "Pricing Management - Admin" }] }),
  component: AdminPricing,
});

type FareRule = {
  id: number;
  vehicle_type: string;
  vehicle_type_display: string;
  base_fare: number;
  per_km: number;
  per_minute: number;
  surge_multiplier: number;
  tax_percentage: number;
  minimum_fare: number;
  cancellation_fee: number;
  is_active: boolean;
  updated_at: string;
};

function AdminPricing() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [fareRules, setFareRules] = useState<FareRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<Record<number, Partial<FareRule>>>({});

  // Check admin access
  useEffect(() => {
    const checkAdmin = () => {
      if (!auth.token) {
        navigate({ to: "/" });
        return;
      }
      // Admin access check - adjust based on your auth system
      // For now, allow access if user is logged in
    };
    checkAdmin();
  }, [navigate]);

  // Fetch fare rules
  const fetchFareRules = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.getFareRules();
      if (response.success) {
        setFareRules(response.fare_rules);
      }
    } catch (error) {
      toast.error("Failed to load fare rules");
      console.error(error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchFareRules();
  }, [fetchFareRules]);

  // Handle field change
  const handleChange = (ruleId: number, field: keyof FareRule, value: number | boolean) => {
    setEditing((prev) => ({
      ...prev,
      [ruleId]: {
        ...prev[ruleId],
        [field]: value,
      },
    }));
  };

  // Save single rule
  const saveRule = async (ruleId: number) => {
    const changes = editing[ruleId];
    if (!changes || Object.keys(changes).length === 0) {
      toast.info("No changes to save");
      return;
    }

    try {
      setSaving(true);
      const response = await api.updateFareRule(ruleId, changes);
      if (response.success) {
        toast.success(`${response.fare_rule.vehicle_type_display} pricing updated`);
        setFareRules((prev) =>
          prev.map((rule) => (rule.id === ruleId ? response.fare_rule : rule))
        );
        setEditing((prev) => {
          const newEditing = { ...prev };
          delete newEditing[ruleId];
          return newEditing;
        });
      }
    } catch (error) {
      toast.error("Failed to update fare rule");
      console.error(error);
    } finally {
      setSaving(false);
    }
  };

  // Save all changes
  const saveAllChanges = async () => {
    const rulesToUpdate = Object.entries(editing)
      .filter(([_, changes]) => Object.keys(changes).length > 0)
      .map(([id, changes]) => ({
        id: Number(id),
        ...changes,
      }));

    if (rulesToUpdate.length === 0) {
      toast.info("No changes to save");
      return;
    }

    try {
      setSaving(true);
      const response = await api.bulkUpdateFareRules(rulesToUpdate);
      if (response.success) {
        toast.success(`Updated ${response.updated.length} fare rules`);
        await fetchFareRules();
        setEditing({});
      } else {
        toast.error("Some updates failed");
        if (response.errors) {
          response.errors.forEach((err) => {
            toast.error(err.error);
          });
        }
      }
    } catch (error) {
      toast.error("Failed to save changes");
      console.error(error);
    } finally {
      setSaving(false);
    }
  };

  // Get value for field (edited or original)
  const getValue = (rule: FareRule, field: keyof FareRule): number | boolean => {
    if (editing[rule.id]?.[field] !== undefined) {
      return editing[rule.id][field] as number | boolean;
    }
    return rule[field] as number | boolean;
  };

  // Check if rule has changes
  const hasChanges = (ruleId: number): boolean => {
    return editing[ruleId] && Object.keys(editing[ruleId]).length > 0;
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl mb-4 animate-pulse">⏳</div>
          <p className="text-muted-foreground">Loading pricing rules...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen pb-20">
      <Navbar to="/" />

      <div className="px-4 pt-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Pricing Management</h1>
            <p className="text-sm text-muted-foreground">
              Manage fare rates for all vehicle types
            </p>
          </div>
          <div className="flex gap-2">
            <Link
              to="/driver"
              className="px-4 py-2 rounded-xl bg-surface-2 text-sm font-medium hover:bg-surface-2/80"
            >
              Back to Driver
            </Link>
          </div>
        </div>

        {/* Bulk Actions */}
        {Object.keys(editing).length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-4 p-4 rounded-xl bg-primary/10 border border-primary/20"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">
                {Object.keys(editing).filter((id) => hasChanges(Number(id))).length} rule(s) modified
              </span>
              <div className="flex gap-2">
                <Btn
                  variant="secondary"
                  onClick={() => setEditing({})}
                  disabled={saving}
                >
                  Cancel
                </Btn>
                <Btn onClick={saveAllChanges} disabled={saving}>
                  {saving ? "Saving..." : "Save All Changes"}
                </Btn>
              </div>
            </div>
          </motion.div>
        )}

        {/* Fare Rules Grid */}
        <div className="grid gap-4">
          {fareRules.map((rule) => (
            <motion.div
              key={rule.id}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className={`rounded-2xl glass p-4 hairline ${
                hasChanges(rule.id) ? "border-primary/50 bg-primary/5" : ""
              }`}
            >
              {/* Vehicle Header */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-xl bg-primary/20 flex items-center justify-center text-2xl">
                    {getVehicleIcon(rule.vehicle_type)}
                  </div>
                  <div>
                    <h3 className="font-bold text-lg">{rule.vehicle_type_display}</h3>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        getValue(rule, "is_active")
                          ? "bg-emerald-500/20 text-emerald-400"
                          : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {getValue(rule, "is_active") ? "Active" : "Inactive"}
                    </span>
                  </div>
                </div>
                {hasChanges(rule.id) && (
                  <Btn size="sm" onClick={() => saveRule(rule.id)} disabled={saving}>
                    Save
                  </Btn>
                )}
              </div>

              {/* Pricing Fields */}
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <PriceField
                  label="Base Fare (₹)"
                  value={getValue(rule, "base_fare") as number}
                  onChange={(v) => handleChange(rule.id, "base_fare", v)}
                />
                <PriceField
                  label="Per KM (₹)"
                  value={getValue(rule, "per_km") as number}
                  onChange={(v) => handleChange(rule.id, "per_km", v)}
                />
                <PriceField
                  label="Per Minute (₹)"
                  value={getValue(rule, "per_minute") as number}
                  onChange={(v) => handleChange(rule.id, "per_minute", v)}
                />
                <PriceField
                  label="Surge Multiplier"
                  value={getValue(rule, "surge_multiplier") as number}
                  step={0.1}
                  onChange={(v) => handleChange(rule.id, "surge_multiplier", v)}
                />
                <PriceField
                  label="Tax (%)"
                  value={getValue(rule, "tax_percentage") as number}
                  step={0.5}
                  onChange={(v) => handleChange(rule.id, "tax_percentage", v)}
                />
                <PriceField
                  label="Minimum Fare (₹)"
                  value={getValue(rule, "minimum_fare") as number}
                  onChange={(v) => handleChange(rule.id, "minimum_fare", v)}
                />
                <PriceField
                  label="Cancellation Fee (₹)"
                  value={getValue(rule, "cancellation_fee") as number}
                  onChange={(v) => handleChange(rule.id, "cancellation_fee", v)}
                />
              </div>

              {/* Active Toggle */}
              <div className="mt-4 pt-4 border-t border-border/50 flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  Last updated: {new Date(rule.updated_at).toLocaleString()}
                </span>
                <label className="flex items-center gap-2 cursor-pointer">
                  <span className="text-sm">Active</span>
                  <input
                    type="checkbox"
                    checked={getValue(rule, "is_active") as boolean}
                    onChange={(e) => handleChange(rule.id, "is_active", e.target.checked)}
                    className="w-5 h-5 rounded border-border bg-surface-2 text-primary focus:ring-primary"
                  />
                </label>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Empty State */}
        {fareRules.length === 0 && !loading && (
          <div className="text-center py-12">
            <div className="text-4xl mb-4">📋</div>
            <p className="text-muted-foreground">No fare rules found</p>
          </div>
        )}

        {/* Info Card */}
        <div className="mt-6 p-4 rounded-xl bg-surface-2/50 border border-border/50">
          <h4 className="font-medium mb-2">How Pricing Works</h4>
          <ul className="text-sm text-muted-foreground space-y-1">
            <li>• Base Fare: Fixed amount charged at the start of every ride</li>
            <li>• Per KM: Rate charged for each kilometer traveled</li>
            <li>• Per Minute: Rate charged for ride duration (traffic, stops)</li>
            <li>• Surge Multiplier: Applied during high demand (min: 1.0)</li>
            <li>• Tax %: GST/tax percentage added to the fare (e.g., 5% = ₹5 tax on ₹100)</li>
            <li>• Minimum Fare: Lowest possible fare for any ride</li>
            <li>• Cancellation Fee: Charged when passenger cancels after driver arrives</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

// Price Input Field Component
function PriceField({
  label,
  value,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <label className="text-xs text-muted-foreground mb-1 block">{label}</label>
      <input
        type="number"
        value={value}
        step={step}
        min={0}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full px-3 py-2 rounded-xl bg-surface-2 border border-border/50 text-sm focus:border-primary focus:outline-none"
      />
    </div>
  );
}

// Get emoji icon for vehicle type
function getVehicleIcon(vehicleType: string): string {
  const icons: Record<string, string> = {
    bike: "🏍️",
    auto: "🛺",
    erickshaw: "🔋",
    mini: "🚗",
    sedan: "🚙",
    suv: "🚐",
  };
  return icons[vehicleType] || "🚗";
}

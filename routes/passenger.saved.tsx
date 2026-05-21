import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Brand } from "@/components/Brand";
import { Btn, Field } from "@/components/Field";
import { api, auth } from "@/lib/api";
import { useTranslation } from "@/hooks/useTranslation";

type Place = { id: string; label: string; address: string; lat: number; lng: number };

export const Route = createFileRoute("/passenger/saved")({
  head: () => ({ meta: [{ title: "Saved places — RIDES4U" }] }),
  component: Saved,
});

function Saved() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [places, setPlaces] = useState<Place[] | null>(null);
  const [label, setLabel] = useState("");
  const [address, setAddress] = useState("");
  const [selectedPlace, setSelectedPlace] = useState<{ address: string; lat: number; lng: number } | null>(null);

  const refresh = () => api.savedPlaces().then((r) => setPlaces(r.places)).catch(() => setPlaces([]));

  useEffect(() => {
    if (typeof window !== "undefined" && (!auth.token || auth.role !== "passenger")) {
      navigate({ to: "/" });
      return;
    }
    refresh();
  }, [navigate]);

  const add = async () => {
    if (!label.trim() || !selectedPlace) return toast.error(t("labelAndAddressRequired"));
    try {
      await api.addSavedPlace({ label, address: selectedPlace.address, lat: selectedPlace.lat, lng: selectedPlace.lng });
      setLabel(""); setAddress(""); setSelectedPlace(null);
      refresh();
    } catch (e) {
      toast.error((e as Error).message || t("couldNotSave"));
    }
  };

  const remove = async (id: string) => {
    try { await api.deleteSavedPlace(id); refresh(); }
    catch (e) { toast.error((e as Error).message || t("deleteFailed")); }
  };

  return (
    <div className="min-h-screen pb-10">
      <header className="sticky top-0 z-20 flex items-center justify-between bg-background/70 px-5 py-4 backdrop-blur-md">
        <Brand to="/passenger" />
        <Link to="/passenger" className="text-sm text-muted-foreground hover:text-foreground">← {t("home")}</Link>
      </header>
      <main className="px-4">
        <h1 className="font-display text-2xl font-bold">{t("saved")}</h1>

        <div className="mt-4 glass rounded-2xl p-4">
          <Field 
            label={t("label")} 
            placeholder={t("labelPlaceholder")} 
            value={label} 
            onChange={(e) => setLabel(e.target.value)} 
          />
          <div className="h-3" />
          <Field 
            label={t("address")} 
            placeholder={t("searchAddress")} 
            value={address} 
            onChange={(e) => setAddress(e.target.value)}
            onSelect={(place: any) => {
              setAddress(place.address);
              setSelectedPlace(place);
            }}
          />
          <Btn onClick={add} className="mt-3 w-full" disabled={!selectedPlace || !label.trim()}>
            {t("savePlace")}
          </Btn>
        </div>

        <div className="mt-4 space-y-2">
          {places === null && <div className="h-16 rounded-xl shimmer hairline" />}
          {places?.length === 0 && <div className="rounded-xl glass p-6 text-center text-sm text-muted-foreground">{t("noSavedPlaces")}</div>}
          {places?.map((p) => (
            <div key={p.id} className="lift flex items-start justify-between rounded-xl glass p-4">
              <div className="min-w-0">
                <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground">{p.label}</div>
                <div className="truncate text-sm">{p.address}</div>
              </div>
              <button onClick={() => remove(p.id)} className="text-xs text-destructive hover:underline">{t("remove")}</button>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

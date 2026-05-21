import * as React from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";

// Dropdown component that renders via portal
function Dropdown({
  suggestions,
  loading,
  onSelect,
  onClose,
  inputRect,
}: {
  suggestions: { description: string; lat: number; lng: number }[];
  loading: boolean;
  onSelect: (s: { description: string; lat: number; lng: number }) => void;
  onClose: () => void;
  inputRect: DOMRect | null;
}) {
  if (!inputRect) return null;

  const style: React.CSSProperties = {
    position: "fixed",
    top: inputRect.bottom + 4,
    left: inputRect.left,
    width: inputRect.width,
    maxHeight: 400,
    zIndex: 9999,
  };

  return createPortal(
    <>
      <div className="fixed inset-0 z-[9998]" onClick={onClose} />
      <motion.div
        initial={{ opacity: 0, y: -4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        style={style}
        className="overflow-y-auto rounded-xl border border-border bg-background p-1 shadow-2xl"
      >
        {loading && suggestions.length === 0 && (
          <div className="p-3 text-center text-xs text-muted-foreground">Searching...</div>
        )}
        {suggestions.map((s, i) => (
          <button
            key={i}
            onClick={() => onSelect(s)}
            className="flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition hover:bg-elevated"
          >
            <span className="mt-0.5 text-base">📍</span>
            <span className="line-clamp-2 leading-relaxed">{s.description}</span>
          </button>
        ))}
      </motion.div>
    </>,
    document.body
  );
}

export const Field = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement> & { 
    label?: string; 
    hint?: string; 
    error?: string;
    onSelect?: (p: { address: string; lat: number; lng: number }) => void;
  }
>(function Field({ label, hint, error, className = "", id, onSelect, value, onChange, ...props }, ref) {
  const inputId = id ?? React.useId();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [suggestions, setSuggestions] = React.useState<{ description: string; lat: number; lng: number }[]>([]);
  const [show, setShow] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [inputRect, setInputRect] = React.useState<DOMRect | null>(null);

  React.useImperativeHandle(ref, () => inputRef.current!);

  const updateRect = () => {
    if (inputRef.current) {
      setInputRect(inputRef.current.getBoundingClientRect());
    }
  };

  const fetchSuggestions = React.useMemo(() => {
    let timer: any;
    return (q: string) => {
      if (timer) clearTimeout(timer);
      if (!q || q.length < 3) {
        setSuggestions([]);
        return;
      }
      timer = setTimeout(async () => {
        setLoading(true);
        try {
          const res = await api.autocomplete(q);
          // filter suggestions to 80km radius around Mathura/Vrindavan
          const centerLat = 27.5692, centerLng = 77.6843;
          const distance = (lat1: number, lon1: number, lat2: number, lon2: number) => {
            const R = 6371;
            const dLat = (lat2 - lat1) * Math.PI / 180;
            const dLon = (lon2 - lon1) * Math.PI / 180;
            const a =
              Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) *
              Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
            const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
            return R * c;
          };
          const filtered = res.suggestions.filter((s) => {
            if (!s.lat || !s.lng) return true; // Show it if no coordinates, don't hide it
            const dist = distance(centerLat, centerLng, s.lat, s.lng);
            return dist <= 80;
          });
          setSuggestions(filtered);
        } catch {
          setSuggestions([]);
        } finally {
          setLoading(false);
        }
      }, 300);
    };
  }, []);

  const handleSelect = (s: { description: string; lat: number; lng: number }) => {
    if (onSelect) onSelect({ address: s.description, lat: s.lat, lng: s.lng });
    setSuggestions([]);
    setShow(false);
  };

  return (
    <div className="relative w-full">
      <label htmlFor={inputId} className="block">
        {label && <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>}
        <input
          ref={inputRef}
          id={inputId}
          value={value}
          autoComplete="off"
          onChange={(e) => {
              if (onChange) onChange(e);
              if (onSelect) {
                fetchSuggestions(e.target.value);
                setShow(true);
                updateRect();
              } else {
                setShow(false);
              }
            }}
          onFocus={() => {
            setShow(true);
            updateRect();
          }}
          className={`w-full rounded-lg bg-surface-2 px-3.5 py-3 text-[15px] text-foreground placeholder:text-muted-foreground/70 hairline ring-focus transition focus:bg-elevated ${error ? "border-destructive" : ""} ${className}`}
          {...props}
        />
        {hint && !error && <span className="mt-1 block text-xs text-muted-foreground">{hint}</span>}
        {error && <span className="mt-1 block text-xs text-destructive">{error}</span>}
      </label>

      <AnimatePresence>
        {show && onSelect && (suggestions.length > 0 || loading) && (
          <Dropdown 
            suggestions={suggestions} 
            loading={loading} 
            onSelect={handleSelect} 
            onClose={() => setShow(false)}
            inputRect={inputRect}
          />
        )}
      </AnimatePresence>
    </div>
  );
});

export function Btn({
  children,
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "outline" | "danger" }) {
  const styles = {
    primary: "bg-foreground text-background hover:bg-foreground/90",
    ghost: "bg-transparent text-foreground hover:bg-accent",
    outline: "bg-transparent text-foreground hairline hover:bg-accent",
    danger: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
  }[variant];
  return (
    <button
      {...props}
      className={`lift press inline-flex items-center justify-center gap-2 rounded-lg px-4 py-3 text-sm font-semibold ring-focus transition disabled:opacity-50 disabled:hover:transform-none ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

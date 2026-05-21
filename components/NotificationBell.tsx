import { useState, useEffect, useCallback, useRef } from "react";
import { api, auth } from "@/lib/api";
import { toast } from "sonner";

export function NotificationBell({ wsMsg }: { wsMsg: any }) {
  const [items, setItems] = useState<Array<{ id: number; message: string; is_read: boolean; timestamp: string }>>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const seenKeysRef = useRef<Set<string>>(new Set());

  // Request notification permission on mount
  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  const showBrowserNotification = useCallback((message: string) => {
    if (
      typeof document !== "undefined" &&
      !document.hidden
    ) {
      return;
    }
    if ("Notification" in window && Notification.permission === "granted") {
      const criticalTypes = ["driver arrived", "ride confirmed", "payment success", "driver_arrived", "ride_confirmed", "payment_success"];
      const lowerMsg = message.toLowerCase();
      const isCritical = criticalTypes.some(t => lowerMsg.includes(t)) || 
                         lowerMsg.includes("arrived") || 
                         lowerMsg.includes("confirmed") || 
                         lowerMsg.includes("success");

      if (isCritical) {
        new Notification("RIDES4U", {
          body: message,
          icon: "/favicon.ico",
        });
      }
    }
  }, []);

  const notificationKey = useCallback((n: any) => {
    const id = Number(n?.notification_id || 0);
    const seq = Number(n?.sequence_id || 0);
    if (id) return `id:${id}`;
    if (seq) return `seq:${seq}`;
    return `msg:${String(n?.type || "")}:${String(n?.message || "")}:${String(n?.timestamp || "")}`;
  }, []);

  useEffect(() => {
    if (!auth.token) return;
    let alive = true;
    const fetchItems = async () => {
      try {
        const res = await api.notifications();
        if (!alive) return;
        setItems(res.notifications || []);
        seenKeysRef.current = new Set((res.notifications || []).map((n: any) => notificationKey(n)));
        setUnread(res.unread_count);
      } catch {}
    };
    fetchItems();
    const interval = setInterval(fetchItems, 30000);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [auth.token]);

  useEffect(() => {
    if (!wsMsg) return;
    
    const msg = wsMsg;
    if (msg.type === "notification_snapshot" && Array.isArray(msg.notifications)) {
      const fresh = msg.notifications.filter((n: any) => {
        const key = notificationKey(n);
        if (seenKeysRef.current.has(key)) return false;
        seenKeysRef.current.add(key);
        return true;
      });
      if (fresh.length === 0) return;
      setItems((prev) => {
        const seen = new Set(prev.map((p) => p.id));
        const merged = [...prev];
        for (const n of fresh) {
          const id = Number(n.notification_id || Date.now());
          if (seen.has(id)) continue;
          merged.unshift({ id, message: String(n.message || ""), is_read: false, timestamp: String(n.timestamp || new Date().toISOString()) });
          seen.add(id);
        }
        return merged.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
      });
      setUnread((c) => c + fresh.length);
    } else if (msg.type === "notification" && msg.notification) {
      const n = msg.notification;
      const message = String(n.message || "");
      const key = notificationKey(n);
      if (seenKeysRef.current.has(key)) return;
      seenKeysRef.current.add(key);
      
      setItems((prev) => {
        const id = Number(n.notification_id || 0);
        if (id && prev.some((p) => p.id === id)) return prev;
        return [
          { id: id || Date.now(), message, is_read: false, timestamp: String(n.timestamp || new Date().toISOString()) },
          ...prev,
        ];
      });
      setUnread((c) => c + 1);
      toast.info(message, {
        duration: 8000,
        className: "ring-2 ring-emerald-400/60 shadow-[0_0_30px_rgba(16,185,129,0.35)]",
      });
      showBrowserNotification(message);
    }
  }, [wsMsg, showBrowserNotification, notificationKey]);

  if (!auth.token) return null;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 glass hairline lift press transition-all hover:bg-white/10"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-white px-1 text-[10px] font-bold text-black ring-2 ring-background">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      
      {open && (
        <>
          <div className="fixed inset-0 z-[100]" onClick={() => setOpen(false)} />
          <div className="absolute right-0 sm:left-0 sm:right-auto mt-3 z-[101] w-[min(20rem,calc(100vw-1rem))] origin-top-right sm:origin-top-left rounded-2xl glass p-1 shadow-2xl ring-1 ring-white/10 scale-in">
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
              <span className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Notifications</span>
              <button
                className="text-[10px] font-medium text-white/40 hover:text-white transition-colors"
                onClick={async () => {
                  await api.markNotificationsRead();
                  setUnread(0);
                  setItems((prev) => prev.map((x) => ({ ...x, is_read: true })));
                }}
              >
                Mark all read
              </button>
            </div>
            <div className="max-h-[350px] overflow-y-auto py-1 custom-scrollbar">
              {items.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-muted-foreground">
                  No notifications yet
                </div>
              ) : (
                items.slice(0, 10).map((n) => (
                  <div 
                    key={n.id} 
                    className={`group relative mx-1 my-0.5 rounded-xl px-4 py-3 text-sm transition-all hover:bg-white/5 ${
                      n.is_read ? "opacity-60" : "bg-white/[0.02]"
                    }`}
                  >
                    {!n.is_read && (
                      <span className="absolute left-1.5 top-5 h-1.5 w-1.5 rounded-full bg-white" />
                    )}
                    <div className="line-clamp-2 leading-snug">{n.message}</div>
                    <div className="mt-1 text-[10px] text-muted-foreground">
                      {new Date(n.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

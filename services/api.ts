/**
 * Centralized API client for the Django backend (canonical file).
 * All UI code should import from `src/services/api.ts` which
 * proxies existing `src/lib/api.ts` imports for compatibility.
 */

const RAW_API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://127.0.0.1:8000/api";
export const API_BASE: string = RAW_API_BASE.replace(/\/api\/v1$/i, "/api");

/** ws(s):// origin for live updates. Override with VITE_WS_BASE_URL. */
export const WS_BASE: string =
  (import.meta.env.VITE_WS_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  API_BASE.replace(/^http/i, "ws").replace(/\/api(?:\/v\d+)?\/?$/i, "");

export function wsUrl(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  const token = typeof window !== "undefined" ? localStorage.getItem("rides4u_token") : null;
  return `${WS_BASE}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

const TOKEN_KEY = "rides4u_token";
const USER_KEY = "rides4u_user";
const ROLE_KEY = "rides4u_role";
const LANG_KEY = "rides4u_lang";

export type Role = "passenger" | "rider";
export type Language = "en" | "hi";

export const auth = {
  get token() {
    return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
  },
  setToken(t: string | null) {
    if (typeof window === "undefined") return;
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  },
  get user() {
    if (typeof window === "undefined") return null;
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  },
  setUser(u: unknown | null) {
    if (typeof window === "undefined") return;
    if (u) localStorage.setItem(USER_KEY, JSON.stringify(u));
    else localStorage.removeItem(USER_KEY);
  },
  get role(): Role | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(ROLE_KEY) as Role | null;
  },
  setRole(r: Role | null) {
    if (typeof window === "undefined") return;
    if (r) localStorage.setItem(ROLE_KEY, r);
    else localStorage.removeItem(ROLE_KEY);
  },
  get language(): Language {
    if (typeof window === "undefined") return "en";
    return (localStorage.getItem(LANG_KEY) as Language) ?? "en";
  },
  setLanguage(l: Language) {
    if (typeof window === "undefined") return;
    localStorage.setItem(LANG_KEY, l);
  },
  clear() {
    if (typeof window === "undefined") return;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ROLE_KEY);
  },
};

export type AppUser = {
  id: string;
  name: string;
  phone: string;
  role: Role;
  language?: Language;
  rating?: number;
  verification_status?: "pending" | "approved" | "rejected";
};

export type Vehicle = "bike" | "auto" | "erickshaw";

export type LatLng = { lat: number; lng: number };

class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown; form?: FormData } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (auth.token) headers.set("Authorization", `Token ${auth.token}`);
  let body: BodyInit | undefined = init.body as BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  } else if (init.form) {
    body = init.form;
  }
  // Ensure path ends with / to satisfy Django's APPEND_SLASH middleware
  // For query strings, add / before the ?
  let normalizedPath = path;
  if (path.includes("?")) {
    const [base, query] = path.split("?");
    normalizedPath = base.endsWith("/") ? path : `${base}/?${query}`;
  } else {
    normalizedPath = path.endsWith("/") ? path : `${path}/`;
  }
  const res = await fetch(`${API_BASE}${normalizedPath}`, { ...init, headers, body });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    throw new ApiError(res.status, data, (data as { detail?: string })?.detail ?? res.statusText);
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

// Exported API surface (matches previous UI expectations)
export const api = {
  sendOtp: async (phone: string) => {
    const res = await request<{ success: boolean; message?: string }>("/auth/send-otp", { method: "POST", json: { phone } });
    if (!res.success) throw new Error(res.message || "Failed to send OTP");
    return { ok: true };
  },
  verifyOtp: async (phone: string, otp: string, role: Role) => {
    const res = await request<{ success: boolean; token: string; user: AppUser; new_user?: boolean; message?: string }>("/auth/verify-otp", {
      method: "POST",
      json: { phone, otp, role },
    });
    if (!res.success) throw new Error(res.message || "Verification failed");
    return { token: res.token, user: res.user, isNew: !!res.new_user };
  },
  passengerSignup: async (name: string, phone: string) => {
    const res = await request<{ token: string; user: AppUser }>("/auth/passenger/signup", {
      method: "POST",
      json: { name, phone },
    });
    return res;
  },
  riderSignup: async (data: Record<string, unknown>) => {
    const res = await request<any>("/v1/drivers/register", { method: "POST", json: data });
    return res;
  },
  logout: () => request<{ ok: true }>("/auth/logout", { method: "POST" }),

  me: () => request<{ user: AppUser }>("/me"),
  setLanguage: (language: Language) => request<{ user: AppUser }>("/me/language", { method: "PATCH", json: { language } }),

  // Places
  savedPlaces: () => request<{ places: { id: string; label: string; address: string; lat: number; lng: number }[] }>("/places/saved"),
  addSavedPlace: (p: { label: string; address: string; lat: number; lng: number }) => request<{ place: { id: string } }>("/places/saved", { method: "POST", json: p }),
  deleteSavedPlace: (id: string) => request<{ ok: true }>(`/places/saved/${id}`, { method: "DELETE" }),

  // Rides
  quote: async (pickup: LatLng & { address: string }, drop: LatLng & { address: string }) => {
    const res = await request<{ quotes: unknown[] }>("/rides/quote", { method: "POST", json: { pickup, drop } });
    return res;
  },
  request: async (payload: { pickup: LatLng & { address: string }; drop: LatLng & { address: string }; vehicle: Vehicle; promo?: string }) => {
    const res = await request<{ ride: unknown }>("/rides/request", { method: "POST", json: payload });
    return res;
  },
  ride: async (id: string) => request<{ ride: unknown }>(`/rides/${id}`),
  cancelRide: async (id: string) => {
    const res = await request<{ success: boolean }>(`/rides/${id}/cancel`, { method: "POST" });
    return { ok: res.success };
  },
  history: () => request<{ rides: unknown[] }>("/rides/history"),

  // Driver
  setOnline: async (online: boolean) => {
    const res = await request<{ success: boolean }>("/driver/online", { method: "POST", json: { online } });
    return { ok: res.success };
  },
  driverStats: async () => request<any>("/driver/stats"),
  driverIncoming: () => request<{ ride: unknown | null }>("/driver/incoming"),
  acceptRide: async (id: string) => request<{ ride: unknown }>(`/driver/rides/${id}/accept`, { method: "POST" }),
  arrivedPickup: async (id: string) => request<{ ride: unknown }>(`/driver/rides/${id}/arrived`, { method: "POST" }),
  startRide: async (id: string, code: string) => request<{ ride: unknown }>(`/driver/rides/${id}/start`, { method: "POST", json: { code } }),
  completeRide: async (id: string) => request<{ ride: unknown }>(`/driver/rides/${id}/complete`, { method: "POST" }),
  collectPayment: async (id: string, method: "cash" | "online", code: string) => request<{ ride: unknown }>(`/driver/rides/${id}/collect`, { method: "POST", json: { method, code } }),

  // Payments
  razorpayOrder: (rideId: string) => request<{ qr: string; orderId: string; amount: number }>("/payments/razorpay/order", { method: "POST", json: { rideId } }),
  cashCode: (rideId: string) => request<{ code: string }>("/payments/cash/code", { method: "POST", json: { rideId } }),

  // Maps
  autocomplete: async (q: string) => request<{ suggestions: unknown[] }>(`/maps/autocomplete?q=${encodeURIComponent(q)}`),
  reverse: async (lat: number, lng: number) => request<{ address: string }>(`/maps/reverse?lat=${lat}&lng=${lng}`),
  directions: (o: LatLng, d: LatLng) => request<{ polyline: string; distanceKm: number; durationMin: number }>(`/maps/directions?o=${o.lat},${o.lng}&d=${d.lat},${d.lng}`),

  // Nearby drivers
  nearbyDrivers: async (lat: number, lng: number, vehicle?: Vehicle) => request<{ drivers: unknown[] }>(`/drivers/nearby?lat=${lat}&lng=${lng}${vehicle ? `&vehicle=${vehicle}` : ""}`),

  // Earnings
  earnings: (range: "week" | "month" = "week") => request<any>(`/driver/earnings?range=${range}`),
  withdraw: (amount: number) => request<{ ok: true; payoutId: string }>("/driver/wallet/withdraw", { method: "POST", json: { amount } }),

  // Driver verification
  riderStatus: () => request<{ status: string }>(`/driver/verification/status`),
};

export { ApiError };

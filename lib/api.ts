/**
 * Centralized API client for the Django backend.
 *
 * Set `VITE_API_BASE_URL` in your project environment, e.g. https://api.rides4u.in
 * All endpoints used by the UI are documented below so you can implement
 * matching Django views/URLs 1:1.
 *
 * ── Endpoint contract ─────────────────────────────────────────────────────
 *  POST  /auth/send-otp         { phone }                       -> { ok }
 *  POST  /auth/verify-otp       { phone, otp, role }            -> { token, user, isNew }
 *  POST  /auth/verify-firebase  { phone, idToken, role }        -> { token, user, isNew }
 *  POST  /auth/passenger/signup { name, phone }                 -> { token, user }
 *  POST  /auth/rider/signup     multipart:                      -> { token, user }
 *        name, phone, dl_number, rc_number, plate, aadhaar,
 *        dl_photo (file), aadhaar_photo (file), rc_photo (file)
 *  POST  /auth/logout                                           -> { ok }
 *
 *  GET   /me                                                    -> { user }
 *  PATCH /me/language           { language }                    -> { user }
 *
 *  GET   /places/saved                                          -> { places: [...] }
 *  POST  /places/saved          { label, address, lat, lng }    -> { place }
 *  DELETE /places/saved/:id                                     -> { ok }
 *
 *  POST  /rides/quote           { pickup, drop, vehicle? }      -> { quotes: [{vehicle, base, tax, perKm, eta, total}] }
 *  POST  /rides/request         { pickup, drop, vehicle, promo? } -> { ride, fare }
 *  GET   /rides/:id                                             -> { ride }
 *  POST  /rides/:id/cancel                                      -> { ok }
 *  GET   /rides/history                                         -> { rides: [...] }
 *
 *  // Driver
 *  POST  /driver/online         { online: boolean }             -> { ok }
 *  GET   /driver/stats                                          -> { earningsToday, totalRides, walletBalance, rating }
 *  GET   /driver/incoming                                       -> { ride | null }
 *  POST  /driver/rides/:id/accept                               -> { ride }
 *  POST  /driver/rides/:id/arrived                              -> { ride }
 *  POST  /driver/rides/:id/start { code }                       -> { ride }   // passenger gives code
 *  POST  /driver/rides/:id/complete                             -> { ride }
 *  POST  /driver/rides/:id/collect { method, code }             -> { ride }   // cash collected / online verified
 *
 *  // Payments
 *  POST  /payments/razorpay/order { rideId }                    -> { qr, orderId, amount }
 *  POST  /payments/cash/code      { rideId }                    -> { code }
 *
 *  // Ola Maps proxy (recommended — keeps API key on Django)
 *  GET   /maps/autocomplete?q=...                               -> { suggestions: [...] }
 *  GET   /maps/reverse?lat=..&lng=..                            -> { address }
 *  GET   /maps/directions?o=lat,lng&d=lat,lng                   -> { polyline, distanceKm, durationMin }
 *  GET   /drivers/nearby?lat=..&lng=..&vehicle=..               -> { drivers: [{id,lat,lng,vehicle,heading?}] }
 *
 *  // Earnings
 *  GET   /driver/earnings?range=week|month                      -> { totals, daily, byVehicle, payouts }
 *  POST  /driver/wallet/withdraw { amount }                     -> { ok, payoutId }
 *
 *  // WebSockets (Django Channels) — pass token as ?token=
 *  WS    /ws/rides/:id/                  events:
 *          { type:"ride_update", ride }
 *          { type:"driver_location", lat, lng, heading? }
 *  WS    /ws/driver/                     events:
 *          { type:"incoming", ride } | { type:"stats", stats }
 *  WS    /ws/drivers/nearby?lat=..&lng=..&vehicle=..   events:
 *          { type:"drivers", drivers:[{id,lat,lng,vehicle}] }
 * ─────────────────────────────────────────────────────────────────────────
 */

const ENV_API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "");
const DEFAULT_API_BASE =
  typeof window !== "undefined"
    ? `${window.location.origin}/api`
    : "http://127.0.0.1:8000/api";
const RAW_API_BASE: string = ENV_API_BASE ?? DEFAULT_API_BASE;

// UI endpoints are defined under /api/. If env is set to /api/v1, normalize it
// so frontend requests keep hitting the compatibility routes.
export const API_BASE: string = RAW_API_BASE.replace(/\/api\/v1$/i, "/api");

/** ws(s):// origin for live updates. Override with VITE_WS_BASE_URL. */
export const WS_BASE: string =
  (import.meta.env.VITE_WS_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  API_BASE.replace(/^http/i, "ws").replace(/\/api(?:\/v\d+)?\/?$/i, "");

/** Build a fully-qualified WS URL with auth token attached as ?token=. */
export function wsUrl(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  const token = typeof window !== "undefined" ? localStorage.getItem("rides4u_token") : null;
  return `${WS_BASE}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

const TOKEN_KEY = "rides4u_token";
const USER_KEY = "rides4u_user";
const ROLE_KEY = "rides4u_role";
const LANG_KEY = "rides4u_lang";
const TOKEN_EXPIRY_KEY = "rides4u_token_expiry";
const TOKEN_IP_KEY = "rides4u_token_ip";

// Token validity period: 15 days in milliseconds
const TOKEN_VALIDITY_DAYS = 15;
const TOKEN_VALIDITY_MS = TOKEN_VALIDITY_DAYS * 24 * 60 * 60 * 1000;

export type Role = "passenger" | "rider";
export type Language = "en" | "hi";

export const auth = {
  get token() {
    if (typeof window === "undefined") return null;
    
    const token = localStorage.getItem(TOKEN_KEY);
    const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
    
    if (!token) return null;
    
    // If expired, keep local data so UI can show re-login prompt.
    if (expiry) {
      const expiryTime = parseInt(expiry, 10);
      if (Date.now() > expiryTime) {
        return token;
      }
    }

    return token;
  },
  setToken(t: string | null) {
    if (typeof window === "undefined") return;
    if (t) {
      localStorage.setItem(TOKEN_KEY, t);
      // Set expiry to 15 days from now
      const expiryTime = Date.now() + TOKEN_VALIDITY_MS;
      localStorage.setItem(TOKEN_EXPIRY_KEY, expiryTime.toString());
      // Store current IP (we'll get this from a simple IP service or session)
      auth.updateTokenIP();
    } else {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(TOKEN_EXPIRY_KEY);
      localStorage.removeItem(TOKEN_IP_KEY);
    }
  },
  getCurrentIP(): string | null {
    // We'll use session-based tracking - IP is validated server-side
    return localStorage.getItem(TOKEN_IP_KEY);
  },
  getRawToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem(TOKEN_KEY);
  },
  updateTokenIP() {
    // Mark that we have a valid session
    localStorage.setItem(TOKEN_IP_KEY, "valid");
  },
  isTokenExpired(): boolean {
    if (typeof window === "undefined") return true;
    const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
    if (!expiry) return true;
    return Date.now() > parseInt(expiry, 10);
  },
  getExpiredSessionInfo(): { phone: string | null; role: Role | null } {
    if (typeof window === "undefined") return { phone: null, role: null };
    const rawUser = localStorage.getItem(USER_KEY);
    const rawRole = localStorage.getItem(ROLE_KEY) as Role | null;
    let phone: string | null = null;
    if (rawUser) {
      try {
        const parsed = JSON.parse(rawUser);
        phone = parsed?.phone ?? null;
      } catch {
        phone = null;
      }
    }
    return { phone, role: rawRole };
  },
  getTokenExpiryDate(): Date | null {
    if (typeof window === "undefined") return null;
    const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
    if (!expiry) return null;
    return new Date(parseInt(expiry, 10));
  },
  get user(): AppUser | null {
    if (typeof window === "undefined") return null;
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AppUser) : null;
  },
  setUser(u: AppUser | null) {
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
    localStorage.removeItem(TOKEN_EXPIRY_KEY);
    localStorage.removeItem(TOKEN_IP_KEY);
  },
};

export type AppUser = {
  id: string;
  name: string;
  phone: string;
  role: Role;
  language?: Language;
  rating?: number;
  /** For drivers — backend should set this. UI gates dashboard access on "approved". */
  verification_status?: "pending" | "approved" | "rejected";
  /** For drivers — vehicle type they registered with */
  vehicle_type?: Vehicle;
};

export type Vehicle = "bike" | "auto" | "erickshaw";

export type Quote = {
  vehicle: Vehicle;
  base: number;
  tax: number;
  perKm: number;
  distanceKm: number;
  eta: number;
  total: number;
  fareBeforeDiscount?: number;
  discount?: number;
  promoCode?: string | null;
  promoMessage?: string | null;
  polyline?: string;
};

export type LatLng = { lat: number; lng: number; heading?: number };

export type Ride = {
  id: string;
  status:
    | "requested"
    | "searching"
    | "accepted"
    | "driver_arriving"
    | "driver_arrived"
    | "otp_verified"
    | "started"
    | "reached_destination"
    | "payment_required"
    | "payment_confirmed"
    | "completed"
    | "cancelled";
  pickup: { address: string } & LatLng;
  drop: { address: string } & LatLng;
  vehicle: Vehicle;
  fare: {
    base: number;
    tax: number;
    perKm: number;
    total: number;
    beforeDiscount?: number;
    discount?: number;
    promoCode?: string | null;
  };
  distanceKm?: number;
  code: string; // 4-digit code passenger shares with driver
  driver?: { name: string; phone: string; plate: string; rating?: number; location?: LatLng };
  passenger?: { name: string; phone: string };
  paymentMethod?: "cash" | "online" | null;
  paymentStatus?: "pending" | "paid" | "failed" | null;
  paymentMessage?: string | null;
  polyline?: string; // Some views might return this
  driverToPickupPolyline?: string; // Some views might return this
  expected_route_polyline?: string;
  driver_to_pickup_polyline?: string;
  expected_route_steps?: any[];
  driver_to_pickup_steps?: any[];
  // Pre-calculated route data (saved at booking to avoid recalculation)
  route_duration_minutes?: number;
  route_steps?: any[];
  driver_to_pickup_distance_km?: number;
  driver_to_pickup_duration_minutes?: number;
  createdAt: string;
};

// Extended ride type for driver incoming request popup
export type IncomingRide = {
  id: string;
  pickup: { address: string; lat: number; lng: number };
  drop: { address: string; lat: number; lng: number };
  vehicle: Vehicle;
  fare: {
    total: number;
    base: number;
    perKm: number;
    perMinute?: number;
    distanceKm?: number;
    distanceFare?: number;
    timeFare?: number;
    subtotal?: number;
    tax: number;
    discount: number;
  };
  distances: {
    driverToPickupKm: number;
    pickupToDropKm: number;
  };
  status: string;
  otp: string;
};

export type ErrorClassification = "RETRYABLE" | "FATAL" | "USER_ACTION_REQUIRED";

class ApiError extends Error {
  status: number;
  body: unknown;
  classification: ErrorClassification;
  code?: string;

  constructor(status: number, body: unknown, message: string, code?: string) {
    super(message);
    this.status = status;
    this.body = body;
    this.classification = classifyError(status, body);
    this.code = code;
  }
}

function classifyError(status: number, body: unknown): ErrorClassification {
  // Network errors / timeouts are retryable
  if (status === 0 || status === 502 || status === 503 || status === 504) {
    return "RETRYABLE";
  }
  // Rate limiting is retryable (with backoff)
  if (status === 429) {
    return "RETRYABLE";
  }
  // Auth errors require user action
  if (status === 401 || status === 403) {
    return "USER_ACTION_REQUIRED";
  }
  // Validation errors require user action
  if (status === 400 || status === 422) {
    return "USER_ACTION_REQUIRED";
  }
  // Conflict / duplicate might be retryable in some cases
  if (status === 409) {
    return "USER_ACTION_REQUIRED";
  }
  // 404s are generally fatal (resource gone)
  if (status === 404) {
    return "FATAL";
  }
  // Server errors are retryable (might be transient)
  if (status >= 500) {
    return "RETRYABLE";
  }
  return "FATAL";
}

// Error classification enforcement helpers
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: { maxRetries?: number; delayMs?: number; onError?: (err: ApiError, attempt: number) => void } = {}
): Promise<T> {
  const { maxRetries = 3, delayMs = 1000, onError } = options;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      const apiErr = err as ApiError;

      // Don't retry if not retryable
      if (apiErr.classification !== "RETRYABLE") {
        throw err;
      }

      // Last attempt - throw
      if (attempt >= maxRetries) {
        throw err;
      }

      onError?.(apiErr, attempt);

      // Exponential backoff: 1s, 2s, 4s
      const backoff = delayMs * Math.pow(2, attempt);
      await new Promise(r => setTimeout(r, backoff));
    }
  }

  throw new Error("Retry exhausted");
}

export function handleClassifiedError(
  err: ApiError,
  handlers: {
    onRetryable?: (err: ApiError) => void;
    onUserAction?: (err: ApiError) => void;
    onFatal?: (err: ApiError) => void;
  }
) {
  switch (err.classification) {
    case "RETRYABLE":
      handlers.onRetryable?.(err);
      break;
    case "USER_ACTION_REQUIRED":
      handlers.onUserAction?.(err);
      break;
    case "FATAL":
      handlers.onFatal?.(err);
      break;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown; form?: FormData } = {},
): Promise<T> {
  const headers = new Headers(init.headers);

  // Get current token (expiry is handled by UI + request guard)
  const currentToken = auth.token;
  if (currentToken) headers.set("Authorization", `Token ${currentToken}`);
  if (currentToken && auth.isTokenExpired() && !path.startsWith("/auth/")) {
    throw new ApiError(401, null, "Session expired. Please login again.");
  }

  let body: BodyInit | undefined = init.body as BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  } else if (init.form) {
    body = init.form; // browser sets multipart boundary
  }
  const normalizedPath = path.includes("?")
    ? (() => {
        const [base, query] = path.split("?");
        return `${base.endsWith("/") ? base : `${base}/`}?${query}`;
      })()
    : path.endsWith("/")
      ? path
      : `${path}/`;
  const requestUrl = `${API_BASE}${normalizedPath}`;
  const requestInit: RequestInit = { ...init, headers, body, signal: init.signal };
  let res: Response;
  try {
    res = await fetch(requestUrl, requestInit);
  } catch (err) {
    // One safe retry for transient network/proxy blips. No infinite retries.
    const isAbort = err instanceof DOMException && err.name === "AbortError";
    if (isAbort) {
      throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, 600));
    try {
      res = await fetch(requestUrl, requestInit);
    } catch {
      throw new ApiError(0, null, "Unable to reach server. Please check your connection and try again.");
    }
  }
  
  // Handle 401 Unauthorized - Token expired or invalid
  if (res.status === 401) {
    // If session is locally expired, let root UI handle the expiry modal.
    if (!auth.isTokenExpired()) {
      auth.clear();
      if (typeof window !== "undefined") {
        const currentPath = window.location.pathname;
        if (currentPath !== "/" && !currentPath.startsWith("/login")) {
          setTimeout(() => {
            window.location.href = "/";
          }, 100);
        }
      }
    }
    throw new ApiError(
      401,
      null,
      "Session expired. Please login again.",
    );
  }
  
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const body = (data ?? {}) as { detail?: string; message?: string; error?: string; code?: string };
    // User-friendly error messages based on context
    let userMessage = body.message ?? body.detail ?? body.error ?? res.statusText;

    // Transform technical errors into user-friendly messages
    if (res.status === 401) {
      userMessage = "Your session has expired. Please log in again.";
    } else if (res.status === 403 && !body.message && !body.detail && !body.error) {
      userMessage = "You don't have permission to do that.";
    } else if (res.status === 404 && !body.message && !body.detail && !body.error) {
      userMessage = "We couldn't find what you're looking for.";
    } else if (res.status === 409) {
      userMessage = "This action was already completed. No need to do it again!";
    } else if (res.status === 429) {
      userMessage = "Too many requests. Please wait a moment and try again.";
    } else if (res.status >= 500) {
      userMessage = "Something went wrong on our end. Please try again in a moment.";
    } else if (body.code === "PAYMENT_FAILED") {
      userMessage = "Payment could not be processed. Please try again.";
    } else if (body.code === "VALIDATION_ERROR") {
      userMessage = "Please check your input and try again.";
    } else if (body.code === "RIDE_CANCELLED") {
      userMessage = "This ride has been cancelled.";
    } else if (body.code === "DRIVER_UNAVAILABLE") {
      userMessage = "The driver is no longer available. We'll find you another one.";
    }

    throw new ApiError(
      res.status,
      data,
      userMessage,
      body.code,
    );
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try { return JSON.parse(t); } catch { return t; }
}

function normalizeUser(raw: any, fallbackRole: Role): AppUser {
  const role =
    raw?.role === "rider" || raw?.role === "passenger"
      ? raw.role
      : raw?.is_driver
        ? "rider"
        : fallbackRole;
  return {
    id: String(raw?.id ?? ""),
    name: String(raw?.name ?? ""),
    phone: String(raw?.phone ?? raw?.phone_number ?? ""),
    role,
    language: raw?.language,
    rating: raw?.rating,
    verification_status: raw?.verification_status,
  };
}

export function normalizeRide(raw: any): Ride {
  if (!raw) return null as any;
  const rawStatus = String(raw.status || "");
  const lowered = rawStatus.toLowerCase();
  const normalizedStatus = ({
    requested: "requested",
    searching: "searching",
    searching_driver: "searching",
    accepted: "accepted",
    driver_assigned: "accepted",
    driver_arriving: "driver_arriving",
    arrived: "driver_arrived",
    driver_arrived: "driver_arrived",
    otp_verified: "otp_verified",
    in_progress: "started",
    started: "started",
    reached_destination: "reached_destination",
    payment_required: "payment_required",
    payment_confirmed: "payment_confirmed",
    ride_finished: "completed",
    completed: "completed",
    cancelled: "cancelled",
  } as Record<string, Ride["status"]>)[lowered] || (lowered as Ride["status"]);
  
  // DEBUG: Log all status transformations
  console.log(`[normalizeRide] raw.status: "${raw.status}" → normalized: "${normalizedStatus}"`);
  return {
    ...raw,
    id: String(raw.id),
    status: normalizedStatus,
    pickup: raw.pickup || {
      address: raw.pickup_address || "",
      lat: Number(raw.pickup_lat || 0),
      lng: Number(raw.pickup_lng || 0),
    },
    drop: raw.drop || {
      address: raw.drop_address || "",
      lat: Number(raw.drop_lat || 0),
      lng: Number(raw.drop_lng || 0),
    },
    fare: raw.fare || {
      total: Number(raw.final_fare || raw.estimated_fare || 0),
      base: Number(raw.base_fare || 0),
      tax: 0,
      perKm: 0,
    },
    distanceKm: Number(raw.distance_km ?? raw.distanceKm ?? raw.fare?.distanceKm ?? 0),
    driver: raw.driver ? {
      name: raw.driver.name,
      phone: raw.driver.phone,
      plate: raw.driver.plate || raw.driver.vehicle_number || "",
      rating: raw.driver.rating,
      // Handle both formats: driver.location and driver.current_lat/current_lng
      location: raw.driver.location || (raw.driver.current_lat ? {
        lat: Number(raw.driver.current_lat),
        lng: Number(raw.driver.current_lng),
      } : (raw.driver.lat ? { lat: Number(raw.driver.lat), lng: Number(raw.driver.lng) } : undefined)),
    } : (raw.driver_details ? {
      name: raw.driver_details.name,
      phone: raw.driver_details.phone,
      plate: raw.driver_details.plate || raw.driver_details.vehicle_plate || "",
      rating: raw.driver_details.rating,
      location: raw.driver_details.location || (raw.driver_details.current_lat ? {
        lat: Number(raw.driver_details.current_lat),
        lng: Number(raw.driver_details.current_lng),
      } : undefined),
    } : undefined),
    expected_route_polyline: raw.expected_route_polyline || raw.polyline,
    driver_to_pickup_polyline: raw.driver_to_pickup_polyline || raw.driverToPickupPolyline,
    code: (raw.otp || raw.code || "").slice(0, 4), // Map backend 'otp' to frontend 'code', show only first 4 digits
    paymentMethod: raw.paymentMethod || raw.payment_method || null,
    paymentStatus: raw.paymentStatus || raw.payment_status || null,
    paymentMessage: raw.paymentMessage || raw.payment_message || null,
    // Pre-calculated route data (saved at booking to avoid recalculation)
    route_duration_minutes: raw.route_duration_minutes,
    route_steps: raw.route_steps,
    driver_to_pickup_distance_km: raw.driver_to_pickup_distance_km,
    driver_to_pickup_duration_minutes: raw.driver_to_pickup_duration_minutes,
  };
}

export const api = {
  // ─── Auth ─────────────────────────────────────────────
  sendOtp: async (phone: string) => {
    const res = await request<any>("/auth/send-otp", { method: "POST", json: { phone } });
    if (res?.success === false) throw new Error(res?.message || "Could not send OTP");
    return { ok: true as const };
  },
  verifyOtp: async (phone: string, otp: string, role: Role) => {
    const res = await request<any>("/auth/verify-otp", {
      method: "POST",
      json: { phone, otp, role },
    });
    if (res?.success === false) throw new Error(res?.message || "OTP verification failed");
    return {
      token: String(res?.token ?? ""),
      user: normalizeUser(res?.user ?? {}, role),
      isNew: Boolean(res?.isNew ?? res?.new_user ?? false),
    };
  },
  verifyFirebase: async (phone: string, idToken: string, role: Role) => {
    const res = await request<any>("/auth/verify-firebase", {
      method: "POST",
      json: { phone, idToken, role },
    });
    if (res?.success === false) throw new Error(res?.message || "Firebase verification failed");
    return {
      token: String(res?.token ?? ""),
      user: normalizeUser(res?.user ?? {}, role),
      isNew: Boolean(res?.isNew ?? res?.new_user ?? false),
    };
  },
  checkPhone: async (phone: string, role: Role) => {
    const res = await request<any>("/auth/check-phone", {
      method: "POST",
      json: { phone, role },
    });
    return {
      exists: Boolean(res?.exists),
      action: String(res?.action ?? ""),
      message: String(res?.message ?? ""),
      user: res?.user ? normalizeUser(res.user, role) : null,
      redirect: String(res?.redirect ?? ""),
      reason: String(res?.reason ?? ""),
    };
  },
  passengerSignup: async (name: string, phone: string) => {
    const res = await request<any>("/auth/passenger/signup", {
      method: "POST",
      json: { name, phone },
    });
    return {
      token: String(res?.token ?? ""),
      user: normalizeUser(res?.user ?? {}, "passenger"),
    };
  },
  riderSignup: async (form: FormData) => {
    const res = await request<any>("/auth/rider/signup", { method: "POST", form });
    return {
      token: String(res?.token ?? ""),
      user: normalizeUser(res?.user ?? {}, "rider"),
    };
  },
  logout: async () => {
    const res = await request<any>("/auth/logout", { method: "POST" });
    if (res?.success === false) throw new Error(res?.message || "Logout failed");
    return { ok: true as const };
  },

  me: () => request<{ user: AppUser }>("/me"),
  setLanguage: (language: Language) =>
    request<{ user: AppUser }>("/me/language", { method: "PATCH", json: { language } }),

  // ─── Places ───────────────────────────────────────────
  savedPlaces: () => request<{ places: { id: string; label: string; address: string; lat: number; lng: number }[] }>("/places/saved"),
  addSavedPlace: (p: { label: string; address: string; lat: number; lng: number }) =>
    request<{ place: { id: string } }>("/places/saved", { method: "POST", json: p }),
  deleteSavedPlace: (id: string) => request<{ ok: true }>(`/places/saved/${id}`, { method: "DELETE" }),

  // ─── Rides ────────────────────────────────────────────
  quote: (pickup: LatLng & { address: string }, drop: LatLng & { address: string }) =>
    request<{ quotes: Quote[] }>("/rides/quote", { method: "POST", json: { pickup, drop } }),
  request: async (payload: { pickup: LatLng & { address: string }; drop: LatLng & { address: string }; vehicle: Vehicle; promo?: string }) => {
    const res = await request<{ ride: any }>("/rides/request", { method: "POST", json: payload });
    return { ride: normalizeRide(res.ride) };
  },
  ride: async (id: string, signal?: AbortSignal) => {
    const res = await request<{ ride: any }>(`/rides/${id}`, { signal });
    return { ride: normalizeRide(res.ride) };
  },
  cancelRide: (id: string) => request<{ ok: true }>(`/rides/${id}/cancel`, { method: "POST" }),
  history: () => request<{ rides: Ride[] }>("/rides/history"),

  // ─── Driver ───────────────────────────────────────────
  setOnline: (
    online: boolean,
    location?: LatLng & { heading?: number },
  ) =>
    request<{ ok?: true; success?: boolean; is_online?: boolean }>("/driver/online", {
      method: "POST",
      json: online
        ? {
            online,
            location_permission_granted: true,
            current_lat: location?.lat,
            current_lng: location?.lng,
            heading: location?.heading,
          }
        : { online },
    }),
  driverStats: () => request<{ earningsToday: number; totalRides: number; walletBalance: number; rating: number }>("/driver/stats"),
  driverStatus: () => request<{ success: boolean; is_online: boolean; is_approved?: boolean }>("/driver/status"),
  driverIncoming: () => request<{ ride: IncomingRide | null }>("/driver/incoming"),
  driverActiveRide: async () => {
    const res = await request<{ ride: any | null }>("/driver/active-ride");
    return { ride: res.ride ? normalizeRide(res.ride) : null };
  },
  passengerActiveRide: async () => {
    const res = await request<{ ride: any | null }>("/passenger/active-ride");
    return { ride: res.ride ? normalizeRide(res.ride) : null };
  },
acceptRide: async (id: string) => {
    const res = await request<{ ride: any }>(`/rides/${id}/accept/`, { method: "POST" });
    return { ride: normalizeRide(res.ride) };
  },
  driverRejectRide: (id: string) => request<{ ok: boolean }>(`/rides/${id}/reject/`, { method: "POST" }),
  rejectRide: (id: string) => request<{ ok: boolean }>(`/rides/${id}/reject/`, { method: "POST" }),
  arrivedPickup: async (id: string) => {
    const res = await request<{ ride: any }>(`/rides/${id}/arrive/`, { method: "POST" });
    return { ride: normalizeRide(res.ride) };
  },
  startRide: async (id: string, code: string) => {
    const res = await request<{ ride: any }>(`/rides/${id}/start/`, { method: "POST", json: { code } });
    return { ride: normalizeRide(res.ride) };
  },
  reachedDestination: async (id: string) => {
    const res = await request<{ ride: any }>(`/rides/${id}/reached-destination/`, { method: "POST" });
    return { ride: normalizeRide(res.ride) };
  },
  completeRide: async (id: string) => {
    const res = await request<{ ride: any }>(`/rides/${id}/complete/`, { method: "POST" });
    return { ride: normalizeRide(res.ride) };
  },
  driverCancelRide: (id: string) => request<{ ok: true }>(`/rides/${id}/cancel/`, { method: "POST", json: { cancelled_by: "driver" } }),
  collectPayment: (id: string, method: "cash" | "online", code: string) =>
    request<{ ride: Ride }>(`/rides/${id}/confirm-cash/`, { method: "POST", json: { method, code } }),
  createPaymentOrder: (rideId: string) =>
    request<{ qr: string; orderId: string; amount: number; keyId: string }>("/payments/razorpay/order", { method: "POST", json: { rideId } }),
  checkPaymentStatus: (rideId: string) =>
    request<{ status: "pending" | "paid" | "failed"; amount?: number }>(`/payments/status?rideId=${rideId}`),
  updateDriverLocation: (
    lat: number,
    lng: number,
    heading?: number,
    speed?: number,
    accuracy?: number,
  ) =>
    request<{
      success?: boolean;
      message?: string;
      location?: { lat: number; lng: number; heading?: number; speed?: number };
    }>("/drivers/location", {
      method: "POST",
      json: { lat, lng, heading, speed, accuracy },
    }),

  // ─── Payments ─────────────────────────────────────────
  razorpayOrder: (rideId: string) =>
    request<{ qr: string; orderId: string; amount: number }>("/payments/razorpay/order", { method: "POST", json: { rideId } }),
  cashCode: (rideId: string) => request<{ code: string }>("/payments/cash/code", { method: "POST", json: { rideId } }),

  // NEW: Payment confirmation and online payment endpoints
  confirmCashCollection: (rideId: string) =>
    request<{ success: boolean; message: string; payment_status: string; amount: number }>("/payments/confirm-cash-collection/", { method: "POST", json: { ride_id: rideId } }),
  initiateOnlinePayment: (rideId: string) =>
    request<{ success: boolean; razorpay_order_id: string; razorpay_key_id: string; amount: number; qr_data: string }>("/payments/initiate-online/", { method: "POST", json: { ride_id: rideId } }),
  verifyOnlinePayment: (rideId: string, razorpayPaymentId?: string, razorpaySignature?: string) =>
    request<{ success: boolean; message: string; payment_status: string; amount: number }>("/payments/verify-online/", { method: "POST", json: { ride_id: rideId, razorpay_payment_id: razorpayPaymentId, razorpay_signature: razorpaySignature } }),
  getPaymentStatus: (rideId: string) =>
    request<{ ride_id: string; payment_status: string; payment_method: string | null; amount: number; payment_processed: boolean }>(`/payments/status/${rideId}/`),
  setPaymentMethod: (rideId: string, paymentMethod: "cash" | "online") =>
    request<{ success: boolean; message: string; ride_id: string; payment_method: string }>("/payments/set-method/", { method: "POST", json: { ride_id: rideId, payment_method: paymentMethod } }),

  // ─── Maps (proxied through Django so the Ola key stays server-side) ──
  autocomplete: (q: string) =>
    request<{ suggestions: { description: string; place_id: string; lat: number; lng: number }[] }>(
      `/maps/autocomplete/?q=${encodeURIComponent(q)}`,
    ),
  reverse: (lat: number, lng: number) => request<{ address: string }>(`/maps/reverse?lat=${lat}&lng=${lng}`),
  directions: (o: LatLng, d: LatLng) =>
    request<{ polyline: string; distanceKm: number; durationMin: number }>(
      `/maps/directions?o=${o.lat},${o.lng}&d=${d.lat},${d.lng}`,
    ),

  // ─── Nearby drivers ───────────────────────────────────
  nearbyDrivers: (lat: number, lng: number, vehicle?: Vehicle) =>
    request<{ drivers: NearbyDriver[] }>(
      `/drivers/nearby?lat=${lat}&lng=${lng}${vehicle ? `&vehicle=${vehicle}` : ""}`,
    ),

  // ─── Earnings ─────────────────────────────────────────
  earnings: (range: "week" | "month" = "week") =>
    request<EarningsResponse>(`/driver/earnings?range=${range}`),
  withdraw: (amount: number) =>
    request<{ ok: true; payoutId: string }>("/driver/wallet/withdraw", { method: "POST", json: { amount } }),

  // ─── Driver verification ──────────────────────────────
  /** Polled / refreshed on the /driver/pending screen until status flips. */
  riderStatus: () =>
    request<{ status: "pending" | "approved" | "rejected"; reason?: string }>("/driver/verification/status"),

  // ============================================================
  // CHAT SYSTEM - API methods
  // ADDED: Chat REST API methods (WebSocket fallback)
  // ============================================================

  /** GET /rides/:id/chat/messages/ - Get chat history for a ride */
  getChatMessages: async (rideId: string) => {
    const res = await request<{
      success: boolean;
      messages: Array<{
        id: number;
        ride_id: number;
        sender_role: "RIDER" | "PASSENGER";
        sender_name: string;
        message_text: string;
        message_type: "TEXT" | "QUICK";
        timestamp: string;
        is_read: boolean;
        is_mine?: boolean;
      }>;
      ride_status: string;
      can_send: boolean;
      read_only: boolean;
      unread_count: number;
    }>(`/rides/${rideId}/chat/messages/`);
    return {
      messages: res.messages || [],
      rideStatus: res.ride_status,
      canSend: res.can_send,
      readOnly: res.read_only,
      unreadCount: res.unread_count,
    };
  },

  /** POST /rides/:id/chat/send/ - Send a chat message via REST */
  sendChatMessage: async (rideId: string, message: string, messageType: "TEXT" | "QUICK" = "TEXT") => {
    return await request<{
      success: boolean;
      message: {
        id: number;
        ride_id: number;
        sender_role: "RIDER" | "PASSENGER";
        sender_name: string;
        message_text: string;
        message_type: "TEXT" | "QUICK";
        timestamp: string;
        is_read: boolean;
        is_mine?: boolean;
      };
    }>(`/rides/${rideId}/chat/send/`, {
      method: "POST",
      json: { message, message_type: messageType },
    });
  },

  /** POST /rides/:id/chat/mark-read/ - Mark messages as read */
  markChatMessagesRead: async (rideId: string, messageIds: number[]) => {
    return await request<{
      success: boolean;
      marked_count: number;
    }>(`/rides/${rideId}/chat/mark-read/`, {
      method: "POST",
      json: { message_ids: messageIds },
    });
  },

  notifications: async () => {
    return await request<{
      notifications: Array<{
        id: number;
        type: string;
        message: string;
        is_read: boolean;
        timestamp: string;
      }>;
      unread_count: number;
    }>("/rides/notifications/");
  },

  markNotificationsRead: async (notificationId?: number) => {
    return await request<{ success: boolean }>("/rides/notifications/mark-read/", {
      method: "POST",
      json: notificationId ? { notification_id: notificationId } : {},
    });
  },

  submitRideRating: async (rideId: string, rating: number, feedback?: string) => {
    return await request<{ success: boolean; message: string }>(`/rides/${rideId}/rate/`, {
      method: "POST",
      json: { rating, feedback: feedback || "" },
    });
  },

  // ─── Admin Pricing Management ─────────────────────────
  getFareRules: () =>
    request<{
      success: boolean;
      fare_rules: Array<{
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
      }>;
    }>("/rides/admin/fare-rules/"),

  updateFareRule: (ruleId: number, data: {
    base_fare?: number;
    per_km?: number;
    per_minute?: number;
    surge_multiplier?: number;
    tax_percentage?: number;
    minimum_fare?: number;
    cancellation_fee?: number;
    is_active?: boolean;
  }) =>
    request<{
      success: boolean;
      message: string;
      fare_rule: {
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
    }>(`/rides/admin/fare-rules/${ruleId}/`, { method: "PUT", json: data }),

  bulkUpdateFareRules: (fareRules: Array<{
    id: number;
    base_fare?: number;
    per_km?: number;
    per_minute?: number;
    surge_multiplier?: number;
    tax_percentage?: number;
    minimum_fare?: number;
    cancellation_fee?: number;
    is_active?: boolean;
  }>) =>
    request<{
      success: boolean;
      message: string;
      updated: Array<{ id: number; vehicle_type: string }>;
      errors: Array<{ id?: number; error: string }> | null;
    }>("/rides/admin/fare-rules/bulk-update/", { method: "POST", json: { fare_rules: fareRules } }),

  // ─── Support Tickets ─────────────────────────
  getTicketTopics: (userType: "driver" | "passenger") =>
    request<{
      success: boolean;
      topics: Array<{ value: string; label: string; requires_ride: boolean }>;
    }>(`/rides/support/topics/?user_type=${userType}`),

  getUserRidesForTicket: (userType: "driver" | "passenger") =>
    request<{
      success: boolean;
      rides: Array<{
        id: number;
        pickup_address: string;
        drop_address: string;
        date: string;
        completed_at?: string;
        fare: number;
        status: string;
        driver_name?: string;
        passenger_name?: string;
      }>;
    }>(`/rides/support/user-rides/?user_type=${userType}`),

  createSupportTicket: (data: {
    topic: string;
    description: string;
    user_type: "driver" | "passenger";
    ride_id?: number | null;
  }) =>
    request<{
      success: boolean;
      message: string;
      ticket: {
        id: number;
        topic: string;
        topic_display: string;
        description: string;
        status: string;
        status_display: string;
        priority: string;
        ride_id: number | null;
        created_at: string;
      };
    }>("/rides/support/tickets/create/", { method: "POST", json: data }),

  getMyTickets: () =>
    request<{
      success: boolean;
      tickets: Array<{
        id: number;
        topic: string;
        topic_display: string;
        description: string;
        status: string;
        status_display: string;
        priority: string;
        ride_id: number | null;
        created_at: string;
      }>;
    }>("/rides/support/my-tickets/"),

  // ─── Ad System ─────────────────────────
  getActiveAds: () =>
    request<{
      ads: Array<{
        id: string;
        title: string;
        description: string;
        imageUrl?: string;
        actionUrl?: string;
        actionText?: string;
        backgroundColor?: string;
        textColor?: string;
      }>;
    }>("/ads/active/").catch(() => ({ ads: [] })),

  recordAdImpression: (adId: string) =>
    request<{ success: boolean }>("/ads/impression/", { method: "POST", json: { ad_id: adId } }).catch(() => ({ success: false })),

  recordAdClick: (adId: string) =>
    request<{ success: boolean }>("/ads/click/", { method: "POST", json: { ad_id: adId } }).catch(() => ({ success: false })),

  // ─── Rewards & Loyalty ──────────────────
  getUserRewards: () =>
    request<{
      rewards: Array<{
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
      }>;
    }>("/rewards/my-rewards/").catch(() => ({ rewards: [] })),

  redeemReward: (rewardId: string) =>
    request<{ success: boolean; message: string }>("/rewards/redeem/", {
      method: "POST",
      json: { reward_id: rewardId },
    }).catch(() => ({ success: false, message: "Failed to redeem" })),

  // ─── Emergency SOS ──────────────────────
  sendSOSAlert: (data: {
    ride_id?: string;
    message: string;
    contact_id?: string;
    latitude?: number;
    longitude?: number;
  }) =>
    request<{ success: boolean; message: string }>("/emergency/sos/", {
      method: "POST",
      json: data,
    }).catch(() => ({ success: false, message: "Failed to send SOS" })),

  getEmergencyContacts: () =>
    request<{
      contacts: Array<{
        id: string;
        name: string;
        relationship: string;
        phone: string;
        isEmergency: boolean;
      }>;
    }>("/emergency/contacts/").catch(() => ({ contacts: [] })),

  // ─── Coupon System ──────────────────────
  getAvailableCoupons: () =>
    request<{
      coupons: Array<{
        id: string;
        code: string;
        title: string;
        description: string;
        discount: number;
        maxUses?: number;
        usesRemaining?: number;
        expiryDate: string;
        isApplied?: boolean;
        applicableOn?: string;
      }>;
    }>("/coupons/available/").catch(() => ({ coupons: [] })),

  applyCoupon: (code: string) =>
    request<{ success: boolean; discount: number; message: string }>("/coupons/apply/", {
      method: "POST",
      json: { code },
    }).catch(() => ({ success: false, discount: 0, message: "Coupon not found" })),

  removeCoupon: () =>
    request<{ success: boolean }>("/coupons/remove/", { method: "POST", json: {} }).catch(() => ({
      success: false,
    })),
};

export type NearbyDriver = { id: string; lat: number; lng: number; vehicle: Vehicle; heading?: number };

export type EarningsResponse = {
  totals: { 
    earnings: number; 
    rides: number; 
    onlineMinutes: number; 
    tips: number;
    cashEarnings: number;
    onlineEarnings: number;
    walletBalance: number;
  };
  daily: { date: string; earnings: number; rides: number }[];
  byVehicle: { vehicle: Vehicle; earnings: number; rides: number }[];
  payouts: { id: string; amount: number; status: "pending" | "paid" | "failed"; date: string }[];
};

export { ApiError };

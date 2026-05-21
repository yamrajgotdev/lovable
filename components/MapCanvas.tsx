import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSupercluster from "use-supercluster";

export type MapPoint = { 
  lat: number; 
  lng: number; 
  label?: string; 
  heading?: number;
  vehicle?: "bike" | "auto" | "erickshaw";
};
export type AmbientDriver = {
  id: string;
  lat: number;
  lng: number;
  vehicle?: "bike" | "auto" | "erickshaw";
  heading?: number;
  status?: string;
  eta?: number;
};

const OLA_KEY =
  (import.meta as any).env?.VITE_OLA_MAPS_API_KEY ||
  "BROFZ4A8bKg1j9Y3RBSuoMH66AK9OhBkJlRZFrKb";

const DEFAULT_CENTER: [number, number] = [77.6843, 27.5692];

function vehicleColor(v?: "bike" | "auto" | "erickshaw") {
  // Premium dark marker palette
  if (v === "bike") return "#111111";
  if (v === "erickshaw") return "#1f2937";
  return "#374151";
}

// Haversine distance calculation (returns km)
function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371; // Earth's radius in km
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLng / 2) * Math.sin(dLng / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

// Find closest point on a polyline segment
function closestPointOnSegment(p: [number, number], a: [number, number], b: [number, number]): [number, number] {
  const x = p[0], y = p[1];
  const x1 = a[0], y1 = a[1];
  const x2 = b[0], y2 = b[1];
  const dx = x2 - x1, dy = y2 - y1;
  if (dx === 0 && dy === 0) return a;
  const t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy);
  if (t < 0) return a;
  if (t > 1) return b;
  return [x1 + t * dx, y1 + t * dy];
}

function snapToPolyline(p: [number, number], poly: [number, number][]): [number, number] {
  if (!poly || poly.length < 2) return p;
  let minDist = Infinity;
  let closest: [number, number] = p;
  
  for (let i = 0; i < poly.length - 1; i++) {
    const cp = closestPointOnSegment(p, poly[i], poly[i+1]);
    const d = Math.pow(cp[0] - p[0], 2) + Math.pow(cp[1] - p[1], 2);
    if (d < minDist) {
      minDist = d;
      closest = cp;
    }
  }
  return closest;
}

function decodePolyline(encoded: string): [number, number][] {
  if (!encoded) return [];
  const poly: [number, number][] = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    let b = 0;
    let shift = 0;
    let result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += result & 1 ? ~(result >> 1) : result >> 1;

    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lng += result & 1 ? ~(result >> 1) : result >> 1;

    poly.push([lng / 1e5, lat / 1e5]);
  }
  return poly;
}

function approxDistanceMeters(a: [number, number], b: [number, number]): number {
  const dx = (a[0] - b[0]) * 111320 * Math.cos(((a[1] + b[1]) / 2) * (Math.PI / 180));
  const dy = (a[1] - b[1]) * 110540;
  return Math.sqrt(dx * dx + dy * dy);
}

function toFeature(coords: [number, number][]) {
  return {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: {},
  };
}

// Validate coordinates to prevent map glitches to Africa (0,0)
function isValidCoordinate(lat: number | null | undefined, lng: number | null | undefined): boolean {
  if (lat === null || lat === undefined || lng === null || lng === undefined) return false;
  if (Number.isNaN(lat) || Number.isNaN(lng)) return false;
  if (lat === 0 && lng === 0) return false; // Reject 0,0 (Africa)
  if (lat < -90 || lat > 90) return false; // Invalid latitude
  if (lng < -180 || lng > 180) return false; // Invalid longitude
  return true;
}

function removeLine(map: any, sourceId: string, layerId: string) {
  try {
    if (map?.getLayer?.(layerId)) map.removeLayer(layerId);
  } catch {}
  try {
    if (map?.getSource?.(sourceId)) map.removeSource(sourceId);
  } catch {}
}

function upsertLine(
  map: any,
  sourceId: string,
  layerId: string,
  coords: [number, number][],
  paint: Record<string, unknown>,
) {
  if (!map) return;
  if (!coords || coords.length < 2) {
    removeLine(map, sourceId, layerId);
    return;
  }

  const data = toFeature(coords);
  const src = map.getSource?.(sourceId);
  if (src?.setData) {
    src.setData(data);
    return;
  }

  removeLine(map, sourceId, layerId);
  map.addSource(sourceId, { type: "geojson", data });
  map.addLayer({
    id: layerId,
    type: "line",
    source: sourceId,
    layout: { "line-join": "round", "line-cap": "round" },
    paint,
  });
}

function pinMarkerEl(
  color: string,
  size = 28,
  variant: "pickup" | "drop" | "driver" | "nearby" | "cluster" = "nearby",
  vehicle?: "bike" | "auto" | "erickshaw",
  point?: any
) {
  if (variant === "cluster") {
    const root = document.createElement("div");
    const count = point?.properties?.point_count || 0;
    const markerSize = Math.max(30, Math.min(50, 20 + count * 2));
    root.style.width = `${markerSize}px`;
    root.style.height = `${markerSize}px`;
    root.style.borderRadius = "50%";
    root.style.backgroundColor = "#374151";
    root.style.color = "white";
    root.style.display = "flex";
    root.style.alignItems = "center";
    root.style.justifyContent = "center";
    root.style.fontWeight = "bold";
    root.style.fontSize = "14px";
    root.style.border = "2px solid white";
    root.style.boxShadow = "0 2px 5px rgba(0,0,0,0.3)";
    root.innerText = count.toString();
    return root;
  }

  // Vehicle markers (driver/nearby) - show different icons based on vehicle type
  if (variant === "driver" || variant === "nearby") {
    const root = document.createElement("div");
    const iconContainer = document.createElement("div");

    // Set exact dimensions for proper centering
    // The map library positions the center of this element at the coordinate
    const markerSize = Math.round(size * 1.3);
    root.style.width = `${markerSize}px`;
    root.style.height = `${markerSize}px`;
    root.style.position = "relative";
    root.style.display = "grid";
    root.style.placeItems = "center";

    // Add pulsing animation for live drivers (main driver only, not nearby)
    if (variant === "driver") {
      root.style.animation = "pulse 2s ease-in-out infinite";
    }

    iconContainer.style.width = "100%";
    iconContainer.style.height = "100%";
    iconContainer.style.display = "grid";
    iconContainer.style.placeItems = "center";
    iconContainer.style.transformOrigin = "center center";
    iconContainer.style.transition = "transform 0.3s ease-out";
    iconContainer.dataset.headingTarget = "true";
    
    // Color based on vehicle type - WHITE for bike with dark border for visibility
    const vehicleColors = {
      bike: "#ffffff",
      auto: "#374151",
      erickshaw: "#1f2937",
    };
    iconContainer.style.color = vehicle ? vehicleColors[vehicle] : (variant === "driver" ? "#ffffff" : "#64748b");
    iconContainer.style.filter = "drop-shadow(0 3px 5px rgba(0,0,0,0.4))";
    
    // Add dark stroke/outline for white bike icon visibility
    if (vehicle === "bike" || (!vehicle && variant === "driver")) {
      iconContainer.style.webkitTextStroke = "1px #111111";
      iconContainer.style.textStroke = "1px #111111";
    }
    
    // Top-down simplified icons for better rotation on map
    const icons = {
      // Top-down Bike - white with dark outline
      bike: `<svg viewBox="0 0 24 24" style="width:85%;height:85%;">
        <rect x="11" y="2" width="2" height="20" rx="1" fill="white" stroke="#111111" stroke-width="0.5" />
        <rect x="6" y="6" width="12" height="2" rx="1" fill="white" stroke="#111111" stroke-width="0.5" />
        <rect x="9" y="10" width="6" height="6" rx="2" fill="white" stroke="#111111" stroke-width="0.5" opacity="0.9" />
      </svg>`,

      // Top-down Auto Rickshaw
      auto: `<svg viewBox="0 0 24 24" fill="currentColor" style="width:90%;height:90%;">
        <path d="M7 6c0-1.1.9-2 2-2h6c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H9c-1.1 0-2-.9-2-2V6z" />
        <rect x="8" y="7" width="8" height="10" rx="1" fill="#fff" opacity="0.3" />
        <rect x="6" y="15" width="2" height="4" rx="0.5" />
        <rect x="16" y="15" width="2" height="4" rx="0.5" />
        <rect x="11" y="3" width="2" height="3" rx="0.5" />
      </svg>`,

      // Top-down E-rickshaw (simplified)
      erickshaw: `<svg viewBox="0 0 24 24" fill="currentColor" style="width:90%;height:90%;">
        <rect x="6" y="5" width="12" height="14" rx="2" />
        <rect x="7" y="6" width="10" height="12" rx="1" fill="#fff" opacity="0.3" />
        <rect x="11" y="2" width="2" height="4" rx="1" />
        <rect x="5" y="14" width="2" height="5" rx="1" />
        <rect x="17" y="14" width="2" height="5" rx="1" />
      </svg>`,
    };
    
    iconContainer.innerHTML = icons[vehicle || "bike"];

    root.appendChild(iconContainer);

    // Add status indicator dot
    if (point?.status) {
      const statusColor = (point.status === 'AVAILABLE' || point.status === 'ONLINE') ? '#10b981' : 
                          (point.status === 'ENROUTE' || point.status === 'DISPATCHED' || point.status === 'IN_RIDE') ? '#f59e0b' : '#6b7280';
      const statusDot = document.createElement("div");
      statusDot.style.position = "absolute";
      statusDot.style.bottom = "-2px";
      statusDot.style.right = "-2px";
      statusDot.style.width = "10px";
      statusDot.style.height = "10px";
      statusDot.style.borderRadius = "50%";
      statusDot.style.backgroundColor = statusColor;
      statusDot.style.border = "2px solid white";
      statusDot.style.zIndex = "2";
      root.appendChild(statusDot);
    }

    // Add hover tooltip for ETA
    if (variant === "nearby" && point?.eta) {
      const tooltip = document.createElement("div");
      tooltip.innerText = `ETA: ${point.eta} min`;
      tooltip.style.position = "absolute";
      tooltip.style.bottom = "100%";
      tooltip.style.left = "50%";
      tooltip.style.transform = "translateX(-50%)";
      tooltip.style.padding = "4px 8px";
      tooltip.style.backgroundColor = "rgba(0,0,0,0.8)";
      tooltip.style.color = "white";
      tooltip.style.borderRadius = "6px";
      tooltip.style.fontSize = "12px";
      tooltip.style.fontWeight = "600";
      tooltip.style.whiteSpace = "nowrap";
      tooltip.style.opacity = "0";
      tooltip.style.pointerEvents = "none";
      tooltip.style.transition = "opacity 0.2s ease-in-out";
      tooltip.style.marginBottom = "8px";
      tooltip.style.zIndex = "1000";
      tooltip.style.boxShadow = "0 4px 6px rgba(0,0,0,0.1)";

      // Add small triangle below tooltip
      const arrow = document.createElement("div");
      arrow.style.position = "absolute";
      arrow.style.top = "100%";
      arrow.style.left = "50%";
      arrow.style.transform = "translateX(-50%)";
      arrow.style.borderLeft = "5px solid transparent";
      arrow.style.borderRight = "5px solid transparent";
      arrow.style.borderTop = "5px solid rgba(0,0,0,0.8)";
      tooltip.appendChild(arrow);

      root.appendChild(tooltip);

      root.addEventListener("mouseenter", () => {
        tooltip.style.opacity = "1";
      });
      root.addEventListener("mouseleave", () => {
        tooltip.style.opacity = "0";
      });
    }

    return root;
  }

  // Location pins for pickup and drop - matte colors with enhanced shadow
  const root = document.createElement("div");

  // Matte colors - muted, less saturated
  const matteColors = {
    pickup: "#b91c1c",    // Darker muted red
    drop: "#1d4ed8",      // Darker muted blue
  };

  const pinColor = variant === "pickup" ? matteColors.pickup : variant === "drop" ? matteColors.drop : color;
  const pinSize = Math.round(size * 1.1);

  const pinHeight = Math.round(pinSize * 1.35);
  root.style.width = `${pinSize}px`;
  root.style.height = `${pinHeight}px`;
  // Position at bottom of container so the tip is at the coordinate point
  // The map library's offset option handles the positioning relative to the coordinate
  root.style.position = "relative";
  root.style.transformOrigin = "bottom center";
  // Enhanced multi-layer shadow for depth
  root.style.filter = "drop-shadow(0 2px 3px rgba(0,0,0,0.3)) drop-shadow(0 4px 8px rgba(0,0,0,0.25))";
  
  // SVG pin marker with matte colors
  root.innerHTML = `
    <svg width="100%" height="100%" viewBox="0 0 36 48" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M18 0C8.06 0 0 8.06 0 18c0 13.5 18 30 18 30s18-16.5 18-30C36 8.06 27.94 0 18 0z" fill="${pinColor}"/>
      <circle cx="18" cy="18" r="7" fill="#f8fafc"/>
      <circle cx="18" cy="18" r="4" fill="${pinColor}"/>
    </svg>
  `;
  
  return root;
}

function applyMarkerHeading(marker: any, point: MapPoint | AmbientDriver | null | undefined) {
  const heading = "heading" in (point ?? {}) ? Number((point as AmbientDriver).heading ?? 0) : 0;
  const rootElement = marker?.getElement?.();
  if (!rootElement) return;
  // Find the icon container with heading target data attribute
  const targetElement = rootElement.querySelector?.('[data-heading-target="true"]') || rootElement;
  if (!targetElement) return;
  (targetElement as HTMLElement).style.transform = `rotate(${Number.isFinite(heading) ? heading : 0}deg)`;
}

export type RouteInfo = {
  polyline: string;
  color?: string;
  width?: number;
  opacity?: number;
  label?: string;
};

export function MapCanvas({
  driver,
  pickup,
  drop,
  nearby,
  showDriverLeg = false,
  polyline,
  polylines,
  driverToPickupPolyline,
  className = "",
  theme = "dark",
}: {
  driver?: MapPoint | null;
  pickup?: MapPoint | null;
  drop?: MapPoint | null;
  nearby?: AmbientDriver[];
  showDriverLeg?: boolean;
  polyline?: string;  // Single polyline for backward compatibility
  polylines?: RouteInfo[];  // Multiple routes support
  driverToPickupPolyline?: string;
  className?: string;
  theme?: "dark" | "light";
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const olaMapsRef = useRef<any>(null);
  const markersRef = useRef<Map<string, any>>(new Map());
  const markerAnimRef = useRef<Map<string, number>>(new Map());
  const lastMainPolylineRef = useRef<string>("");
  const lastDriverLegPolylineRef = useRef<string>("");
  const [mapReadyTick, setMapReadyTick] = useState(0);
  
  const [bounds, setBounds] = useState<[number, number, number, number] | null>(null);
  const [zoom, setZoom] = useState(13);

  // Setup supercluster points
  const points = useMemo(() => {
    return (nearby || [])
      .filter(d => d.status && d.status !== "OFFLINE" && d.status !== "TEMP_OFFLINE")
      .map(d => {
        // Compute ETA
        let eta = 0;
        if (pickup) {
          const dist = haversine(pickup.lat, pickup.lng, d.lat, d.lng);
          eta = Math.max(1, Math.round(dist * 2)); // roughly 30km/h -> 2 min per km
        }
        return {
          type: "Feature",
          properties: { cluster: false, driverId: d.id, ...d, eta },
          geometry: { type: "Point", coordinates: [d.lng, d.lat] }
        };
      });
  }, [nearby, pickup]);

  const { clusters, supercluster } = useSupercluster({
    points: points as any,
    bounds: bounds || undefined,
    zoom,
    options: { radius: 60, maxZoom: 15 }
  });

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;

    (async () => {
      try {
        const { OlaMaps } = await import("olamaps-web-sdk");
        if (cancelled || mapRef.current) return;

        const olaMaps = new OlaMaps({ apiKey: OLA_KEY });
        olaMapsRef.current = olaMaps;

        // Pass style as URL string, as OlaMaps SDK expects.
        // Use light or dark style based on theme prop.
        const styleName = theme === "light" ? "default-light-standard" : "default-dark-standard";
        const STYLE_URL_TO_USE = `https://api.olamaps.io/tiles/vector/v1/styles/${styleName}/style.json?api_key=${OLA_KEY}`;

        const map = await olaMaps.init({
          style: STYLE_URL_TO_USE,
          container: containerRef.current!,
          center: DEFAULT_CENTER,
          zoom: 13,
          attributionControl: false,
        });

        // Patch 3D buildings and other layers after map loads
        map.on('style.load', () => {
          console.log("[MapCanvas] Style loaded, patching layers...");
          
          // Remove layers that cause validation errors if they exist
          const layersToRemove = ['3d_model_data'];
          layersToRemove.forEach(layerId => {
            if (map.getLayer(layerId)) {
              map.removeLayer(layerId);
              console.log(`[MapCanvas] Removed invalid layer: ${layerId}`);
            }
          });
        });
        
        // Handle missing images gracefully to suppress warnings
        map.on('styleimagemissing', (e: any) => {
          // Provide a 1x1 transparent pixel as fallback for all missing images
          // This suppresses the console errors from maplibre-gl
          const transparentPixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
          map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array([0,0,0,0]) } as any);
        });

        const updateBounds = () => {
          try {
            const b = map.getBounds();
            setBounds([
              b.getWest(),
              b.getSouth(),
              b.getEast(),
              b.getNorth()
            ]);
            setZoom(map.getZoom());
          } catch {}
        };
        map.on('moveend', updateBounds);
        map.on('zoomend', updateBounds);
        map.on('load', updateBounds);

        if (cancelled) {
          try {
            map?.remove?.();
          } catch {}
          return;
        }

        mapRef.current = map;
        setMapReadyTick((x) => x + 1);
      } catch (err) {
        console.error("[MapCanvas] OlaMaps init failed:", err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Update map style when theme changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    
    const styleName = theme === "light" ? "default-light-standard" : "default-dark-standard";
    const newStyleUrl = `https://api.olamaps.io/tiles/vector/v1/styles/${styleName}/style.json?api_key=${OLA_KEY}`;
    
    try {
      map.setStyle(newStyleUrl);
    } catch (err) {
      console.warn("[MapCanvas] Failed to update map style:", err);
    }
  }, [theme]);

  const upsertMarker = useCallback(
    (
      key: string,
      point: MapPoint | AmbientDriver | null | undefined,
      fill: string,
      size = 28,
      variant: "pickup" | "drop" | "driver" | "nearby" | "cluster" = "nearby",
      vehicle?: "bike" | "auto" | "erickshaw",
    ) => {
      const map = mapRef.current;
      const olaMaps = olaMapsRef.current;
      if (!map || !olaMaps) return;

      const existing = markersRef.current.get(key);
      if (!point) {
        existing?.remove?.();
        markersRef.current.delete(key);
        return;
      }

      // Validate coordinates before creating/updating marker
      if (!isValidCoordinate(point.lat, point.lng)) {
        // Remove existing marker if coordinates become invalid
        if (existing) {
          existing.remove?.();
          markersRef.current.delete(key);
        }
        return;
      }

      const lngLat: [number, number] = [point.lng, point.lat];
      
      // Snap driver to polyline if available for perfect alignment
      let targetLngLat = lngLat;
      if (variant === "driver" && polyline) {
        try {
          const poly = decodePolyline(polyline);
          if (poly.length >= 2) {
            targetLngLat = snapToPolyline(lngLat, poly);
          }
        } catch (e) {
          console.warn("[MapCanvas] Snap error:", e);
        }
      }

      if (existing) {
        // Smoothly interpolate for live vehicle markers.
        if (variant === "driver" || variant === "nearby") {
          const current = existing.getLngLat?.();
          const fromLng = Number(current?.lng ?? targetLngLat[0]);
          const fromLat = Number(current?.lat ?? targetLngLat[1]);
          const toLng = targetLngLat[0];
          const toLat = targetLngLat[1];

          // If marker is at invalid position (0,0), force immediate update
          if (fromLat === 0 && fromLng === 0) {
            existing.setLngLat(targetLngLat);
            applyMarkerHeading(existing, point);
            return;
          }

          // Ignore micro GPS jitter so markers don't visibly "buzz" or jump.
          const jumpMeters = approxDistanceMeters([fromLng, fromLat], [toLng, toLat]);
          if (jumpMeters < 2) {
            applyMarkerHeading(existing, point);
            return;
          }

          const prevAnim = markerAnimRef.current.get(key);
          if (prevAnim) cancelAnimationFrame(prevAnim);
          const start = performance.now();
          const duration = 800; // Slightly slower for smoother visual flow
          const tick = (now: number) => {
            const t = Math.min((now - start) / duration, 1);
            const eased = 1 - Math.pow(1 - t, 4); // Quartic ease out for premium feel
            existing.setLngLat([
              fromLng + (toLng - fromLng) * eased,
              fromLat + (toLat - fromLat) * eased,
            ]);
            applyMarkerHeading(existing, point);
            if (t < 1) {
              const rafId = requestAnimationFrame(tick);
              markerAnimRef.current.set(key, rafId);
            }
          };
          const rafId = requestAnimationFrame(tick);
          markerAnimRef.current.set(key, rafId);
        } else {
          existing.setLngLat(targetLngLat);
          applyMarkerHeading(existing, point);
        }
        return;
      }

      try {
        // Validate coordinates before creating marker
        if (!isValidCoordinate(point.lat, point.lng)) {
          console.warn(`[MapCanvas] Skipping marker creation for ${key} - invalid coordinates:`, point.lat, point.lng);
          return;
        }

        // Use bottom anchor for pickup/drop pins so the tip is exactly at the coordinate
        const isPin = variant === "pickup" || variant === "drop";
        const markerEl = pinMarkerEl(fill, size, variant, vehicle, point);
        const markerOptions: any = { element: markerEl, anchor: isPin ? "bottom" : "center" };
        if (isPin) {
          markerOptions.offset = [0, -2]; // Fine-tuned offset
        } else {
          // Some SDK builds still treat custom markers as top-left anchored.
          // Keep visual center pinned to coordinates.
          const markerSize = Math.round(size * 1.3);
          markerOptions.offset = [Math.round(-markerSize / 2), Math.round(-markerSize / 2)];
        }
        const marker = olaMaps
          .addMarker(markerOptions)
          .setLngLat(targetLngLat)
          .addTo(map);
        markersRef.current.set(key, marker);
        applyMarkerHeading(marker, point);
      } catch (e) {
        console.warn("[MapCanvas] marker error:", e);
      }
    },
    [polyline],
  );

  // Memoized nearby markers to prevent constant re-renders from WebSocket updates
  const nearbyKey = useMemo(() => {
    // Create a stable key from nearby driver IDs and positions (rounded to reduce jitter)
    if (!nearby?.length) return "";
    return nearby
      .filter(d => d.status === "ONLINE")
      .map(d => `${d.id}:${Math.round(d.lat * 1000)}:${Math.round(d.lng * 1000)}`)
      .sort()
      .join("|");
  }, [nearby]);

  // Add CSS keyframes for pulse animation
  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = `
      @keyframes pulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.1); }
      }
      @keyframes bike-live {
        0%, 100% { filter: drop-shadow(0 4px 8px rgba(255,215,0,0.6)) drop-shadow(0 0 20px rgba(255,215,0,0.3)); }
        50% { filter: drop-shadow(0 6px 12px rgba(255,215,0,0.8)) drop-shadow(0 0 30px rgba(255,215,0,0.5)); }
      }
    `;
    document.head.appendChild(style);
    return () => { style.remove(); };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    const sync = () => {
      if (cancelled) return;


      upsertMarker("pickup", pickup, "#b91c1c", 26, "pickup");
      upsertMarker("drop", drop, "#1d4ed8", 26, "drop");
      
      // Only show driver marker if driver exists and has valid coordinates
      const hasValidDriverCoords = isValidCoordinate(driver?.lat, driver?.lng);
      const shouldShowDriver = driver && hasValidDriverCoords;
      upsertMarker("driver", shouldShowDriver ? driver : null, "#FFD700", 28, "driver", (driver as any)?.vehicle);

      const activeKeys = new Set<string>();
      
      for (const cluster of clusters) {
        const [lng, lat] = cluster.geometry.coordinates;
        const { cluster: isCluster, point_count } = cluster.properties as any;

        if (isCluster) {
          const key = `cluster-${cluster.id}`;
          activeKeys.add(key);
          upsertMarker(key, { lat, lng, properties: cluster.properties } as any, "#374151", 30, "cluster");
        } else {
          const d = cluster.properties as any;
          const key = `nearby-${d.driverId}`;
          activeKeys.add(key);
          upsertMarker(key, { lat, lng, ...d } as any, vehicleColor(d.vehicle), 22, "nearby", d.vehicle);
        }
      }

      for (const key of markersRef.current.keys()) {
        if ((key.startsWith("nearby-") || key.startsWith("cluster-")) && !activeKeys.has(key)) {
          markersRef.current.get(key)?.remove?.();
          markersRef.current.delete(key);
        }
      }

      const effectivePolyline = polyline || lastMainPolylineRef.current;
      const effectiveDriverLeg = driverToPickupPolyline || lastDriverLegPolylineRef.current;
      if (polyline) {
        lastMainPolylineRef.current = polyline;
      }
      if (driverToPickupPolyline) {
        lastDriverLegPolylineRef.current = driverToPickupPolyline;
      }

      try {
        // Build list of routes to display (supports both single polyline and multiple polylines)
        const routesToRender: RouteInfo[] = [];
        
        if (polylines && polylines.length > 0) {
          // Multiple routes provided
          routesToRender.push(...polylines);
        } else if (effectivePolyline) {
          // Single polyline for backward compatibility
          routesToRender.push({ polyline: effectivePolyline, color: "#3b82f6", width: 5, opacity: 0.9 });
        }

        // Render each route
        routesToRender.forEach((routeInfo, index) => {
          // Decode polyline from API - it already contains the full route from pickup to drop
          // decodePolyline returns [lng, lat] format (GeoJSON standard)
          let coords = routeInfo.polyline ? decodePolyline(routeInfo.polyline) : [];
          
          if (coords.length >= 2 && pickup && drop) {
            const routeStart = coords[0]; // [lng, lat]
            const routeEnd = coords[coords.length - 1]; // [lng, lat]
            // haversine expects (lat1, lng1, lat2, lng2)
            const distStartToPickup = haversine(routeStart[1], routeStart[0], pickup.lat, pickup.lng);
            const distStartToDrop = haversine(routeStart[1], routeStart[0], drop.lat, drop.lng);
            const distEndToPickup = haversine(routeEnd[1], routeEnd[0], pickup.lat, pickup.lng);
            const distEndToDrop = haversine(routeEnd[1], routeEnd[0], drop.lat, drop.lng);

            // Determine route orientation: which endpoint is closer to which marker
            const startIsCloserToPickup = distStartToPickup < distStartToDrop;
            const endIsCloserToDrop = distEndToDrop < distEndToPickup;

            // Debug logging for first render issues
            console.log(`[MapCanvas] Route ${index} alignment:`, {
              routeStart: { lat: routeStart[1], lng: routeStart[0] },
              routeEnd: { lat: routeEnd[1], lng: routeEnd[0] },
              pickup: { lat: pickup.lat, lng: pickup.lng },
              drop: { lat: drop.lat, lng: drop.lng },
              orientation: startIsCloserToPickup && endIsCloserToDrop ? "pickup->drop" : "drop->pickup",
              distStartToPickup: `${distStartToPickup.toFixed(3)}km`,
              distEndToDrop: `${distEndToDrop.toFixed(3)}km`,
              distStartToDrop: `${distStartToDrop.toFixed(3)}km`,
              distEndToPickup: `${distEndToPickup.toFixed(3)}km`,
            });

            // Tolerance for considering endpoints as "connected" to markers
            const TOLERANCE_KM = 0.5; // 500m tolerance
            // Small extension factor (~15 meters) to ensure line visually connects with pin tip
            // The rounded line cap makes the line end slightly before the coordinate
            const CONNECTOR_EXTENSION_METERS = 0.00013; // roughly 15m in degrees (at equator ~0.00013° ≈ 14.4m)

            // Helper to extend connector slightly toward the route so line cap touches pin
            const extendToward = (from: {lat: number, lng: number}, to: [number, number]): [number, number] => {
              const dLat = to[1] - from.lat;
              const dLng = to[0] - from.lng;
              const dist = Math.sqrt(dLat * dLat + dLng * dLng);
              if (dist < 0.00001) return [from.lng, from.lat]; // too close, return original
              const extendFactor = Math.min(CONNECTOR_EXTENSION_METERS / dist, 0.5); // cap at 50%
              return [from.lng + dLng * extendFactor, from.lat + dLat * extendFactor];
            };

            // Always extend route endpoints slightly toward markers for visual connection
            // This ensures rounded line caps meet the pin tip (rounded cap ends slightly before coordinate)
            if (startIsCloserToPickup && endIsCloserToDrop) {
              // Standard: route goes pickup -> drop
              if (distStartToPickup > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} start far from pickup (${distStartToPickup.toFixed(2)}km > ${TOLERANCE_KM}km), adding connector`);
              }
              coords.unshift(extendToward(pickup, routeStart));
              if (distEndToDrop > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} end far from drop (${distEndToDrop.toFixed(2)}km > ${TOLERANCE_KM}km), adding connector`);
              }
              coords.push(extendToward(drop, routeEnd));
            } else if (!startIsCloserToPickup && !endIsCloserToDrop) {
              // Reversed: route goes drop -> pickup
              if (distStartToDrop > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} start far from drop (${distStartToDrop.toFixed(2)}km > ${TOLERANCE_KM}km), adding connector`);
              }
              coords.unshift(extendToward(drop, routeStart));
              if (distEndToPickup > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} end far from pickup (${distEndToPickup.toFixed(2)}km > ${TOLERANCE_KM}km), adding connector`);
              }
              coords.push(extendToward(pickup, routeEnd));
            } else {
              // Mixed orientation - connect both ends to nearest marker
              const nearestStart = distStartToPickup < distStartToDrop ? pickup : drop;
              const nearestEnd = distEndToPickup < distEndToDrop ? pickup : drop;
              if (distStartToPickup > TOLERANCE_KM && distStartToDrop > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} start disconnected, connecting to ${nearestStart === pickup ? "pickup" : "drop"}`);
              }
              coords.unshift(extendToward(nearestStart, routeStart));
              if (distEndToPickup > TOLERANCE_KM && distEndToDrop > TOLERANCE_KM) {
                console.warn(`[MapCanvas] Route ${index} end disconnected, connecting to ${nearestEnd === pickup ? "pickup" : "drop"}`);
              }
              coords.push(extendToward(nearestEnd, routeEnd));
            }
          }
          
          const sourceId = `route-src-${index}`;
          const layerId = `route-layer-${index}`;
          
          if (coords.length >= 2) {
            // Determine style based on route index and options
            const isSecondary = index > 0;
            const color = routeInfo.color || (isSecondary ? "#10b981" : "#3b82f6"); // Blue for primary, green for secondary
            const width = routeInfo.width || (isSecondary ? 4 : 5);
            const opacity = routeInfo.opacity || (isSecondary ? 0.6 : 0.9);
            
            // Build paint object - only include dasharray for secondary routes
            // Ensure all values are numbers to prevent maplibre-gl errors
            const paint: Record<string, unknown> = {
              "line-color": color || "#3b82f6",
              "line-width": width ?? 5,
              "line-opacity": opacity ?? 0.9,
              "line-blur": 0,
            };
            
            if (isSecondary) {
              paint["line-dasharray"] = [2, 2]; // Dashed line for secondary route
            }
            
            upsertLine(map, sourceId, layerId, coords, paint);
          } else {
            removeLine(map, sourceId, layerId);
          }
        });

        // Clean up any extra route layers that might have been rendered before
        let extraIndex = routesToRender.length;
        while (true) {
          const sourceId = `route-src-${extraIndex}`;
          const layerId = `route-layer-${extraIndex}`;
          if (map?.getSource?.(sourceId)) {
            removeLine(map, sourceId, layerId);
            extraIndex++;
          } else {
            break;
          }
        }
      } catch (e) {
        console.warn("[MapCanvas] route error (pickup->drop):", e);
        // Clean up all route layers on error
        for (let i = 0; i < 5; i++) {
          removeLine(map, `route-src-${i}`, `route-layer-${i}`);
        }
      }

      try {
        if (showDriverLeg && driver && pickup && effectiveDriverLeg) {
          const legCoords = decodePolyline(effectiveDriverLeg);
          upsertLine(map, "driver-route-src", "driver-route-layer", legCoords, {
            "line-color": "#f5c518",
            "line-width": 4,
            "line-opacity": 1.0,
            "line-blur": 0,
          });
        } else {
          removeLine(map, "driver-route-src", "driver-route-layer");
        }
      } catch (e) {
        console.warn("[MapCanvas] route error (driver->pickup):", e);
        removeLine(map, "driver-route-src", "driver-route-layer");
      }

      const points: [number, number][] = [];
      if (driver) points.push([driver.lng, driver.lat]);
      if (pickup) points.push([pickup.lng, pickup.lat]);
      if (drop) points.push([drop.lng, drop.lat]);
      for (const item of nearby ?? []) {
        points.push([item.lng, item.lat]);
      }

      if (points.length === 1) {
        try {
          map.flyTo({ center: points[0], zoom: 14 });
        } catch {}
      } else if (points.length >= 2) {
        try {
          const lngs = points.map((p) => p[0]);
          const lats = points.map((p) => p[1]);
          map.fitBounds(
            [
              [Math.min(...lngs), Math.min(...lats)],
              [Math.max(...lngs), Math.max(...lats)],
            ] as any,
            { padding: 60, maxZoom: 16 },
          );
        } catch {}
      }
    };

    if (map.isStyleLoaded?.()) {
      sync();
    } else {
      const onLoad = () => sync();
      map.once?.("load", onLoad);
      return () => {
        cancelled = true;
        map.off?.("load", onLoad);
      };
    }

    return () => {
      cancelled = true;
    };
  // Use nearbyKey instead of nearby to prevent constant re-renders from WebSocket jitter
  // Only re-run when meaningful changes occur (pickup, drop, driver, polyline change)
  }, [
    mapReadyTick,
    driver,
    pickup,
    drop,
    nearbyKey, // Stable key instead of full nearby array
    clusters,  // Rerender when clustering changes
    showDriverLeg,
    polyline,
    polylines,
    driverToPickupPolyline,
    upsertMarker,
  ]);

  useEffect(() => {
    return () => {
      const map = mapRef.current;
      for (const marker of markersRef.current.values()) {
        try {
          marker.remove?.();
        } catch {}
      }
      for (const rafId of markerAnimRef.current.values()) {
        cancelAnimationFrame(rafId);
      }
      markerAnimRef.current.clear();
      markersRef.current.clear();
      if (map) {
        removeLine(map, "route-src", "route-layer");
        removeLine(map, "driver-route-src", "driver-route-layer");
      }
      try {
        map?.remove?.();
      } catch {}
      mapRef.current = null;
      olaMapsRef.current = null;
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className={`relative h-full w-full overflow-hidden rounded-xl ${className}`}
      style={{ minHeight: 200 }}
    />
  );
}

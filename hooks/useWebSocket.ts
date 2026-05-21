import { useEffect, useRef, useState, useCallback } from "react";
import { wsUrl } from "@/lib/api";

export type WsState = "connecting" | "open" | "closed" | "reconnecting";

interface UseWebSocketOptions<T> {
  onMessage?: (msg: T) => void;
  onSyncRequired?: (lastSeq?: number) => void;
  shouldReconnect?: boolean;
  heartbeatInterval?: number;
  maxReconnectAttempts?: number; // Max attempts before switching to low-frequency (default: 15)
  onReconnectSuccess?: () => void; // Callback when connection restored
  isActiveContext?: boolean; // If true: faster retry (5-10s) for active rides
}

/**
 * Enhanced Resilient WebSocket hook with exponential backoff and sync safety.
 */
export function useWebSocket<T = any>(
  path: string | null,
  optionsOrHandler: UseWebSocketOptions<T> | ((msg: T) => void) = {}
) {
  const options: UseWebSocketOptions<T> =
    typeof optionsOrHandler === "function"
      ? { onMessage: optionsOrHandler }
      : optionsOrHandler;

  const { onMessage, onSyncRequired, shouldReconnect = true, maxReconnectAttempts = 15, isActiveContext = false } = options;
  
  const [state, setState] = useState<WsState>("closed");
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [lastSeqId, setLastSeqId] = useState<number | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSyncingRef = useRef(false);
  const lastSeqIdRef = useRef<number | null>(null);
  const lastAppliedSeqRef = useRef<number>(0);
  const connectionIdRef = useRef<string>("");
  const syncBarrierSeqRef = useRef<number | null>(null);
  const gapTrackerRef = useRef<{ count: number; firstGapTime: number | null }>({ count: 0, firstGapTime: null });

  const connect = useCallback(() => {
    if (!path || typeof window === "undefined") return;

    // HARD CAP: Ensure only ONE active WebSocket per path
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING) {
        console.log("[WS_GUARD] Duplicate connection attempt blocked");
        return;
      }
      console.log("[WS_GUARD] Closing stale connection before reconnect");
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    setState(reconnectAttemptsRef.current > 0 ? "reconnecting" : "connecting");
    setIsReconnecting(reconnectAttemptsRef.current > 0);

    // Attach last_seq if available for cursor-based replay
    let fullPath = path;
    if (fullPath.startsWith("/ws/")) {
      const qIdx = fullPath.indexOf("?");
      if (qIdx === -1) {
        if (!fullPath.endsWith("/")) fullPath += "/";
      } else if (fullPath[qIdx - 1] !== "/") {
        fullPath = `${fullPath.slice(0, qIdx)}/${fullPath.slice(qIdx)}`;
      }
    }
    if (lastSeqId !== null) {
      const separator = fullPath.includes("?") ? "&" : "?";
      fullPath = `${fullPath}${separator}last_seq=${lastSeqId}`;
    }

    try {
      const ws = new WebSocket(wsUrl(fullPath));
      wsRef.current = ws;

      ws.onopen = () => {
        console.log(`[WS CONNECT] ${path}`);
        setState("open");
        setIsReconnecting(false);

        // On successful reconnect (not initial), trigger sync
        const wasReconnect = reconnectAttemptsRef.current > 0;
        reconnectAttemptsRef.current = 0; // RESET backoff on success

        // Generate new connection ID to track message freshness
        connectionIdRef.current = Math.random().toString(36).substring(2, 9);

        // Trigger refetch on reconnect to sync missed state
        if (wasReconnect) {
          console.log("[WS RECONNECT] restored, triggering state sync");
          options.onReconnectSuccess?.();
        }

        // Clear gap tracker on successful reconnect
        gapTrackerRef.current = { count: 0, firstGapTime: null };
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          // Handle mandatory SYNC_REQUIRED safeguard
          if (data.type === "SYNC_REQUIRED") {
            console.warn("[WS] SYNC_REQUIRED received", data);
            isSyncingRef.current = true;

            // SYNC BARRIER: Store barrier at current sequence
            const barrierSeq = lastSeqIdRef.current ?? 0;
            syncBarrierSeqRef.current = barrierSeq;
            console.log(`[SYNC_BARRIER] Activated at seq ${barrierSeq}`);

            // Failsafe: Timeout fallback (5s) to resume WS if REST fails
            setTimeout(() => {
              if (isSyncingRef.current) {
                console.warn("[WS] Sync timeout. Resuming WS updates as fallback.");
                isSyncingRef.current = false;
              }
            }, 5000);

            onSyncRequired?.(data.last_seq_on_server);
            return;
          }

          // SYNC LOCK: If we are currently syncing via REST, ignore WS updates
          if (isSyncingRef.current && (data.type === "ride_update" || data.type === "notification")) {
            console.log("[SYNC] Ignoring WS during sync", data.type);
            return;
          }

          // Track last sequence ID for future reconnections and stale detection
          const msgSeqId = data.notification?.sequence_id ?? data.sequence_id ?? null;
          if (msgSeqId !== null) {
            const msgSeq = Number(msgSeqId);
            const lastSeq = lastSeqIdRef.current ?? 0;

            // SYNC BARRIER: Ignore messages at or below barrier until we get strictly newer
            if (syncBarrierSeqRef.current !== null && msgSeq <= syncBarrierSeqRef.current) {
              console.log(`[SYNC_BARRIER] Ignored stale message seq ${msgSeq} <= barrier ${syncBarrierSeqRef.current}`);
              return;
            }

            // Clear barrier once we receive a strictly newer message
            if (syncBarrierSeqRef.current !== null && msgSeq > syncBarrierSeqRef.current) {
              console.log(`[SYNC_BARRIER] Cleared at seq ${msgSeq}`);
              syncBarrierSeqRef.current = null;
            }

            // SEQUENCE GAP DETECTION: If we missed messages, track and potentially force sync
            if (lastSeqIdRef.current !== null && msgSeq > lastSeq + 1) {
              const gap = msgSeq - lastSeq - 1;
              const now = Date.now();
              console.warn(`[WS] Sequence gap detected: missed ${gap} messages (${lastSeq + 1} to ${msgSeq - 1})`);

              // Track gaps for failsafe
              if (gapTrackerRef.current.firstGapTime === null) {
                gapTrackerRef.current.firstGapTime = now;
              }
              gapTrackerRef.current.count++;

              // FAILSAFE: If >3 gaps within 30 seconds, force full refresh
              const timeWindow = now - (gapTrackerRef.current.firstGapTime ?? now);
              if (gapTrackerRef.current.count > 3 && timeWindow < 30000) {
                console.warn(`[FORCE_SYNC] Triggered due to excessive gaps (${gapTrackerRef.current.count} in ${timeWindow}ms)`);
                gapTrackerRef.current = { count: 0, firstGapTime: null };
                onSyncRequired?.(msgSeq);
              }

              // Trigger sync to catch up
              onSyncRequired?.(msgSeq);
            }

            // STALE MESSAGE PROTECTION: Only apply if strictly greater than last applied
            if (msgSeq <= lastAppliedSeqRef.current) {
              console.log(`[WS_DROP] Dropped out-of-order message seq ${msgSeq}, lastApplied=${lastAppliedSeqRef.current}`);
              return;
            }

            // Only accept messages within reasonable tolerance of last seen
            if (msgSeq < lastSeq - 50) {
              console.warn("[WS] Ignoring stale message by sequence", data.type, msgSeq, "<", lastSeq);
              return;
            }

            lastSeqIdRef.current = Math.max(lastSeq, msgSeq);
            setLastSeqId(lastSeqIdRef.current);
          }

          // Apply message and track last applied sequence
          onMessage?.(data);
          if (msgSeqId !== null) {
            lastAppliedSeqRef.current = Number(msgSeqId);
          }
        } catch (err) {
          console.error("[WS] Message parse error", err);
        }
      };

      ws.onclose = (event) => {
        setState("closed");
        wsRef.current = null;
        if (shouldReconnect && event.code !== 1000) {
          console.log(`[WS RECONNECT] close_code=${event.code} path=${path}`);
        }
        
        if (shouldReconnect && event.code !== 1000) {
          scheduleReconnect();
        }
      };

      ws.onerror = (err) => {
        console.error("[WS] Error", err);
        ws.close();
      };
    } catch (err) {
      console.error("[WS] Connection failed", err);
      scheduleReconnect();
    }
  }, [path, lastSeqId, onMessage, onSyncRequired, shouldReconnect]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) return;

    const attempts = reconnectAttemptsRef.current;

    // GLOBAL JITTER: Add random 0-5000ms to ALL delays for anti-DDOS
    const jitter = Math.random() * 5000;

    // CONTEXT-AWARE: Active rides get faster retry even after max attempts
    if (attempts >= maxReconnectAttempts) {
      if (isActiveContext) {
        // Active ride: keep retrying every 5-10 seconds base + jitter (user is waiting)
        const baseDelay = 5000 + Math.random() * 5000; // 5-10s base
        const activeDelay = baseDelay + jitter;
        console.log(`[RECONNECT] Attempt ${attempts + 1}, delay ${Math.round(activeDelay)}ms (base ${Math.round(baseDelay)} + jitter ${Math.round(jitter)}, active=${isActiveContext})`);
        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null;
          connect();
        }, activeDelay);
      } else {
        // Inactive: switch to low-frequency background retries (~60s) + jitter
        const baseDelay = 60000;
        const lowFreqDelay = baseDelay + jitter;
        console.log(`[RECONNECT] Attempt ${attempts + 1}, delay ${Math.round(lowFreqDelay)}ms (base ${baseDelay} + jitter ${Math.round(jitter)}, inactive)`);
        setIsReconnecting(false);
        reconnectTimerRef.current = setTimeout(() => {
          reconnectTimerRef.current = null;
          connect();
        }, lowFreqDelay);
      }
      return;
    }

    // CONTEXT-AWARE: Active rides get faster initial backoff
    let baseDelay: number;
    if (isActiveContext && attempts < 5) {
      // Fast retry for active context (first 5 attempts)
      baseDelay = Math.min(10000, 1000 * Math.pow(1.5, attempts)); // Cap at 10s
    } else {
      // Standard backoff
      baseDelay = Math.min(30000, 1000 * Math.pow(2, attempts));
    }
    const delay = baseDelay + jitter;
    console.log(`[RECONNECT] Attempt ${attempts + 1}/${maxReconnectAttempts}, delay ${Math.round(delay)}ms (base ${Math.round(baseDelay)} + jitter ${Math.round(jitter)}, active=${isActiveContext})`);

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      reconnectAttemptsRef.current += 1;
      connect();
    }, delay);
  }, [connect, maxReconnectAttempts, isActiveContext]);

  const setSyncComplete = useCallback(() => {
    if (isSyncingRef.current) {
      console.log("[SYNC] Completed");
    }
    isSyncingRef.current = false;
  }, []);

  // Manual reconnect to reset max attempts counter
  const manualReconnect = useCallback(() => {
    reconnectAttemptsRef.current = 0;
    connect();
  }, [connect]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [path]); 

  const send = useCallback((data: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return {
    state,
    isReconnecting,
    isBackgroundRetrying: reconnectAttemptsRef.current >= maxReconnectAttempts,
    hasReachedMaxAttempts: reconnectAttemptsRef.current >= maxReconnectAttempts,
    send,
    lastSeqId,
    setSyncComplete,
    manualReconnect,
    connectionId: connectionIdRef.current,
  };
}

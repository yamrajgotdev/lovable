import React from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { WifiOff, Wifi, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface ConnectionStatusProps {
  ws: ReturnType<typeof useWebSocket>;
  className?: string;
}

/**
 * Non-intrusive connection status indicator.
 * Shown only during reconnection or when closed.
 */
export const ConnectionStatus: React.FC<ConnectionStatusProps> = ({ ws, className }) => {
  const { state, isReconnecting, isBackgroundRetrying, manualReconnect } = ws;

  if (state === "open") return null;

  // Background retrying state - subtle indicator
  if (isBackgroundRetrying) {
    return (
      <div
        className={cn(
          "fixed top-4 left-1/2 -translate-x-1/2 z-[100] px-3 py-1.5 rounded-full shadow flex items-center gap-2 text-xs font-medium transition-all duration-300 bg-slate-100 text-slate-600 border border-slate-200",
          className
        )}
      >
        <WifiOff className="w-3 h-3" />
        <span>Offline — retrying in background</span>
        <button
          onClick={manualReconnect}
          className="ml-2 underline hover:text-slate-800"
        >
          Retry now
        </button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "fixed top-4 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-full shadow-lg flex items-center gap-2 text-sm font-medium transition-all duration-300",
        state === "connecting" || state === "reconnecting"
          ? "bg-amber-100 text-amber-800 border border-amber-200"
          : "bg-red-100 text-red-800 border border-red-200",
        className
      )}
    >
      {state === "connecting" || state === "reconnecting" ? (
        <>
          <Wifi className="w-4 h-4 animate-pulse" />
          <span>{isReconnecting ? "Reconnecting..." : "Connecting..."}</span>
        </>
      ) : (
        <>
          <WifiOff className="w-4 h-4" />
          <span>Connection lost. Retrying...</span>
        </>
      )}
    </div>
  );
};

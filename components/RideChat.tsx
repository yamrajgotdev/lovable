/**
 * ==============================================================================
 * CHAT SYSTEM - React Component
 * ADDED: Isolated, non-breaking chat UI component for rider-passenger chat
 * ==============================================================================
 *
 * Features:
 * - WebSocket real-time chat (primary)
 * - REST API polling fallback (every 4 seconds)
 * - Quick message buttons
 * - Read-only mode when ride ends
 * - Privacy-compliant (first names only)
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useWebSocket } from "@/hooks/useWebSocket";
import { api } from "@/lib/api";
import { toast } from "sonner";

// Quick message options
const QUICK_MESSAGES_PASSENGER = [
  "Where are you?",
  "I am at pickup point",
  "Please come fast",
  "I will be 2 minutes late",
];

const QUICK_MESSAGES_DRIVER = [
  "I have arrived",
  "I am nearby",
  "Stuck in traffic",
  "Please come to pickup point",
];

export type ChatMessage = {
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

type ChatState = {
  messages: ChatMessage[];
  canSend: boolean;
  readOnly: boolean;
  rideStatus: string | null;
};

type WebSocketMessage =
  | { type: "chat_history"; messages: ChatMessage[]; can_send: boolean; read_only: boolean; ride_status: string }
  | { type: "new_message"; message: ChatMessage }
  | { type: "typing"; role: string; typing: boolean }
  | { type: "user_joined"; role: string }
  | { type: "messages_marked_read"; message_ids: number[] }
  | { type: "error"; code?: string; message: string };

interface RideChatProps {
  rideId: string;
  role: "passenger" | "rider";
  rideStatus?: string;
  /** If true, chat opens automatically (for demo/testing) */
  autoOpen?: boolean;
  /** Callback when new message arrives */
  onNewMessage?: () => void;
}

export function RideChat({ rideId, role, rideStatus, autoOpen = false, onNewMessage }: RideChatProps) {
  const [isOpen, setIsOpen] = useState(autoOpen);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [canSend, setCanSend] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const [otherTyping, setOtherTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTypingRef = useRef<number>(0);
  const lastPollRef = useRef<number>(0);
  const wsSendRef = useRef<((data: unknown) => void) | undefined>(undefined);
  const POLL_INTERVAL = 4000; // 4 seconds for REST fallback

  // Determine quick messages based on role
  const quickMessages = role === "passenger" ? QUICK_MESSAGES_PASSENGER : QUICK_MESSAGES_DRIVER;
  const otherRole = role === "passenger" ? "RIDER" : "PASSENGER";

  // WebSocket message handler
  const handleWebSocketMessage = useCallback((msg: WebSocketMessage) => {
    if (msg.type === "chat_history") {
      setMessages(msg.messages);
      setCanSend(msg.can_send);
      setReadOnly(msg.read_only);
      // Calculate unread
      const unread = msg.messages.filter((m: ChatMessage) => !m.is_read && !m.is_mine).length;
      setUnreadCount(unread);
    } else if (msg.type === "new_message") {
      setMessages((prev) => {
        // Prevent duplicates
        if (prev.some((m: ChatMessage) => m.id === msg.message.id)) return prev;
        return [...prev, msg.message];
      });
      if (!msg.message.is_mine) {
        setUnreadCount((c) => c + 1);
        onNewMessage?.();
        // Mark as read immediately if chat is open
        if (isOpen) {
          wsSendRef.current?.({ action: "mark_read", message_ids: [msg.message.id] });
        }
      }
    } else if (msg.type === "typing") {
      if (msg.role !== role.toUpperCase()) {
        setOtherTyping(msg.typing);
        // Clear typing after 3 seconds if no update
        if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
        if (msg.typing) {
          typingTimeoutRef.current = setTimeout(() => setOtherTyping(false), 3000);
        }
      }
    } else if (msg.type === "messages_marked_read") {
      setMessages((prev) =>
        prev.map((m: ChatMessage) => (msg.message_ids.includes(m.id) ? { ...m, is_read: true } : m))
      );
    } else if (msg.type === "error") {
      if (msg.code === "RATE_LIMITED") {
        toast.error("Please wait before sending another message");
      } else {
        toast.error(msg.message);
      }
    }
  }, [isOpen, onNewMessage, role]);

  // WebSocket connection - stay connected in background to receive notifications/unread count
  const { state: wsState, send: wsSend } = useWebSocket<WebSocketMessage>(
    rideId ? `/ws/chat/${rideId}/` : null,
    { onMessage: handleWebSocketMessage }
  );

  // Store send function in ref for use in callbacks
  useEffect(() => {
    wsSendRef.current = wsSend;
  }, [wsSend]);

  // Fallback polling only when WebSocket is disconnected.
  useEffect(() => {
    if (!isOpen || wsState !== "closed") return;

    let alive = true;
    const poll = async () => {
      if (!alive) return;
      try {
        const now = Date.now();
        if (now - lastPollRef.current < POLL_INTERVAL) return;
        lastPollRef.current = now;

        const res = await api.getChatMessages(rideId);
        if (!alive) return;

        setMessages((prev) => {
          // Merge new messages, avoiding duplicates
          const existingIds = new Set(prev.map((m: ChatMessage) => m.id));
          const newMsgs = res.messages.filter((m: ChatMessage) => !existingIds.has(m.id));
          if (newMsgs.length > 0) {
            onNewMessage?.();
            setUnreadCount((c) => c + newMsgs.filter((m: ChatMessage) => !m.is_mine).length);
          }
          return [...prev, ...newMsgs];
        });
        setCanSend(res.canSend);
        setReadOnly(res.readOnly);
      } catch {
        // Silent fail for polling
      }
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL);
    return () => {
      alive = false;
      clearInterval(interval);
    };
  }, [isOpen, wsState, rideId, onNewMessage]);

  // Always fetch latest chat state when opening chat so send state is never blocked.
  useEffect(() => {
    if (!isOpen) return;
    let alive = true;
    (async () => {
      try {
        const res = await api.getChatMessages(rideId);
        if (!alive) return;
        setMessages(res.messages || []);
        setCanSend(res.canSend);
        setReadOnly(res.readOnly);
      } catch {
        // Silent fail, websocket/polling will continue.
      }
    })();
    return () => {
      alive = false;
    };
  }, [isOpen, rideId]);

  // Scroll to bottom when messages change
  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isOpen]);

  // Mark messages as read when opening chat
  useEffect(() => {
    if (isOpen && unreadCount > 0) {
      const unreadIds = messages.filter((m: ChatMessage) => !m.is_read && !m.is_mine).map((m: ChatMessage) => m.id);
      if (unreadIds.length > 0) {
        if (wsState === "open" && wsSendRef.current) {
          wsSendRef.current({ action: "mark_read", message_ids: unreadIds });
        } else {
          // REST fallback
          api.markChatMessagesRead(rideId, unreadIds).catch(() => {});
        }
      }
      setUnreadCount(0);
    }
  }, [isOpen, messages, unreadCount, wsState, rideId]);

  // Handle ride status changes from props (updates chat availability)
  useEffect(() => {
    if (rideStatus) {
      console.log(`[RideChat] Current ride status: ${rideStatus}`);
      // Chat available from booking until ride ends
      // Backend database stores lowercase statuses via serializer: searching_driver, driver_assigned, etc.
      const status = rideStatus.toLowerCase();
      const validStatuses = [
        "requested",
        "searching",
        "searching_driver",
        "booked",
        "accepted",
        "driver_assigned",
        "driver_arriving",
        "arrived",
        "otp_verified",
        "started",
        "reached_destination",
        "payment_required",
        "payment_confirmed",
      ];
      const readOnlyStatuses = ["completed", "cancelled"];
      
      const isAvailable = validStatuses.includes(status) || readOnlyStatuses.includes(status);
      console.log(`[RideChat] Chat available: ${isAvailable}`);

      setCanSend(validStatuses.includes(status));
      setReadOnly(readOnlyStatuses.includes(status));
    }
  }, [rideStatus]);

  const handleSend = async () => {
    const text = inputText.trim();
    if (!text || !canSend) return;

    setInputText("");

    if (wsState === "open" && wsSendRef.current) {
      wsSendRef.current({
        action: "send_message",
        message: text,
        message_type: "TEXT",
      });
    } else {
      // REST fallback
      try {
        const res = await api.sendChatMessage(rideId, text, "TEXT");
        if (res.success) {
          setMessages((prev) => [...prev, res.message]);
        }
      } catch (e: any) {
        toast.error(e.message || "Failed to send message");
      }
    }
  };

  const handleQuickSend = async (text: string) => {
    if (!canSend) return;

    if (wsState === "open" && wsSendRef.current) {
      wsSendRef.current({
        action: "send_message",
        message: text,
        message_type: "QUICK",
      });
    } else {
      // REST fallback
      try {
        const res = await api.sendChatMessage(rideId, text, "QUICK");
        if (res.success) {
          setMessages((prev) => [...prev, res.message]);
        }
      } catch (e: any) {
        toast.error(e.message || "Failed to send message");
      }
    }
  };

  const handleTyping = (isTyping: boolean) => {
    if (wsState === "open" && wsSendRef.current) {
      // Throttle: only send typing=true once every 2 seconds
      const now = Date.now();
      if (isTyping) {
        if (now - lastTypingRef.current > 2000) {
          lastTypingRef.current = now;
          wsSendRef.current({ action: "typing", typing: true });
        }
      } else {
        // Always send typing=false immediately
        lastTypingRef.current = 0;
        wsSendRef.current({ action: "typing", typing: false });
      }
    }
  };

  // Format timestamp to readable time
  const formatTime = (isoString: string) => {
    const date = new Date(isoString);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  // Floating chat button (when closed)
  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500 text-white shadow-lg hover:bg-emerald-600 transition-transform hover:scale-105 active:scale-95"
        aria-label="Open chat"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-xs font-bold text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>
    );
  }

  // Chat panel (when open)
  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 20, scale: 0.95 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 20, scale: 0.95 }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
        className="fixed bottom-6 right-6 z-50 w-[calc(100vw-48px)] max-w-sm"
      >
        <div className="overflow-hidden rounded-2xl bg-slate-900 border border-white/10 shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between bg-slate-800 px-4 py-3">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500/20">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  className="text-emerald-400"
                >
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
              </div>
              <div>
                <div className="text-sm font-semibold text-white">
                  {role === "passenger" ? "Driver" : "Passenger"}
                </div>
                <div className="flex items-center gap-1.5 text-xs text-gray-300">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      wsState === "open" ? "bg-emerald-400" : "bg-amber-400"
                    }`}
                  />
                  {wsState === "open" ? "Online" : "Connecting..."}
                </div>
              </div>
            </div>
            <button
              onClick={() => setIsOpen(false)}
              className="grid h-8 w-8 place-items-center rounded-full text-gray-300 hover:bg-white/10 hover:text-white transition"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M18 6 6 18" />
                <path d="m6 6 12 12" />
              </svg>
            </button>
          </div>

          {/* Messages area */}
          <div className="h-80 overflow-y-auto bg-slate-950 p-3 space-y-3">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center text-center text-muted-foreground">
                <div className="mb-2 text-4xl">💬</div>
                <p className="text-sm">No messages yet</p>
                <p className="text-xs mt-1 max-w-[200px]">
                  {readOnly
                    ? "Chat is read-only for this ride"
                    : canSend
                      ? role === "passenger"
                        ? "You can chat now. Driver will see messages once assigned."
                        : "Start chatting with your passenger"
                      : "Chat is currently unavailable"}
                </p>
              </div>
            ) : (
              <>
                {messages.map((msg, idx) => {
                  const isMine = msg.is_mine;
                  const showSender =
                    idx === 0 || messages[idx - 1].sender_role !== msg.sender_role;

                  return (
                    <div
                      key={msg.id}
                      className={`flex flex-col ${isMine ? "items-end" : "items-start"}`}
                    >
                      {showSender && (
                        <span className="mb-1 text-[10px] text-muted-foreground">
                          {msg.sender_name}
                        </span>
                      )}
                      <div
                        className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                          isMine
                            ? "bg-emerald-500/20 text-emerald-100 rounded-br-md"
                            : "bg-surface-2 text-foreground rounded-bl-md"
                        }`}
                      >
                        {msg.message_type === "QUICK" && (
                          <span className="mr-1.5 text-xs text-emerald-400">⚡</span>
                        )}
                        {msg.message_text}
                        <span className="ml-2 text-[10px] text-muted-foreground">
                          {formatTime(msg.timestamp)}
                          {isMine && (
                            <span className="ml-1">
                              {msg.is_read ? (
                                <svg
                                  className="inline h-3 w-3 text-emerald-400"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                >
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              ) : (
                                <svg
                                  className="inline h-3 w-3 text-muted-foreground"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                >
                                  <polyline points="20 6 9 17 4 12" />
                                </svg>
                              )}
                            </span>
                          )}
                        </span>
                      </div>
                    </div>
                  );
                })}
                {otherTyping && (
                  <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="animate-pulse">typing...</span>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {/* Quick messages */}
          {canSend && !readOnly && (
            <div className="border-t border-white/5 bg-surface-1 p-2">
              <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-thin">
                {quickMessages.map((text) => (
                  <button
                    key={text}
                    onClick={() => handleQuickSend(text)}
                    className="whitespace-nowrap rounded-full bg-surface-2 px-3 py-1 text-xs text-muted-foreground hover:bg-emerald-500/10 hover:text-emerald-400 transition"
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Input area */}
          <div className="border-t border-white/10 bg-surface-1 p-3">
            {readOnly ? (
              <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-center text-sm text-rose-400">
                Chat is read-only - Ride has ended
              </div>
            ) : canSend ? (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={inputText}
                  onChange={(e) => {
                    setInputText(e.target.value);
                    handleTyping(e.target.value.length > 0);
                  }}
                  onBlur={() => handleTyping(false)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                      handleTyping(false);
                    }
                  }}
                  placeholder="Type a message..."
                  maxLength={500}
                  className="flex-1 rounded-xl bg-background px-3 py-2 text-sm outline-none ring-1 ring-white/10 focus:ring-emerald-500/50 transition"
                />
                <button
                  onClick={handleSend}
                  disabled={!inputText.trim()}
                  className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="m22 2-7 20-4-9-9-4 20-7z" />
                  </svg>
                </button>
              </div>
            ) : (
              <div className="rounded-lg bg-amber-500/10 px-3 py-2 text-center text-sm text-amber-400">
                Chat is currently unavailable for this ride
              </div>
            )}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

export default RideChat;

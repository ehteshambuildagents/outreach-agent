"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, type ConversationSummary } from "@/lib/api";

/**
 * Shared chat-history state so the single app sidebar (which now lists recent
 * conversations) and the chat page stay in sync. The sidebar drives which
 * conversation is open; the chat page reads `activeId`, loads it, and reports back
 * the id + a refresh when it creates/updates a conversation.
 */
type ChatNav = {
  conversations: ConversationSummary[];
  activeId: string | null;
  refresh: () => void;
  open: (id: string) => void;
  startNew: () => void;
  remove: (id: string) => void;
  setActive: (id: string | null) => void;
};

const Ctx = createContext<ChatNav | null>(null);

export function useChatNav(): ChatNav {
  const c = useContext(Ctx);
  if (!c) throw new Error("useChatNav must be used within ChatNavProvider");
  return c;
}

export function ChatNavProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    void api.conversations().then((r) => r.ok && setConversations(r.data.conversations || []));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const open = useCallback(
    (id: string) => {
      setActiveId(id);
      if (pathname !== "/ai") router.push("/ai");
    },
    [pathname, router],
  );

  const startNew = useCallback(() => {
    setActiveId(null);
    if (pathname !== "/ai") router.push("/ai");
  }, [pathname, router]);

  const remove = useCallback((id: string) => {
    setConversations((prev) => prev.filter((t) => t.id !== id));
    void api.deleteConversation(id);
    setActiveId((cur) => (cur === id ? null : cur));
  }, []);

  return (
    <Ctx.Provider value={{ conversations, activeId, refresh, open, startNew, remove, setActive: setActiveId }}>
      {children}
    </Ctx.Provider>
  );
}

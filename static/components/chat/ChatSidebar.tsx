"use client";

import { MessageSquarePlus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ChatSession } from "@/lib/types";

type ChatSidebarProps = {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onCreateSession: () => void;
  /** When provided, shows a delete icon on each session item */
  onDeleteSession?: (sessionId: string) => void;
  className?: string; // allow parent to control layout constraints
};

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ChatSidebar({
  sessions,
  activeSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  className = "",
}: ChatSidebarProps) {
  return (
    <aside className={`flex h-full w-72 shrink-0 flex-col bg-background/95 backdrop-blur z-30 border-r border-border/30 shadow-[4px_0_24px_rgba(0,0,0,0.02)] transition-transform duration-300 ${className}`}>
      <div className="border-b border-border/30 p-4">
        <Button className="w-full justify-start gap-2 bg-primary/5 text-primary hover:bg-primary/10 shadow-none border border-primary/10" variant="outline" onClick={onCreateSession}>
          <MessageSquarePlus className="h-4 w-4" />
          新建对话
        </Button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 scrollbar-elegant">
        {sessions.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border/50 p-4 text-sm text-muted-foreground text-center mt-2">
            暂无历史对话
          </div>
        ) : (
          sessions.map((session) => {
            const preview =
              session.messages.find((m) => m.role === "user")?.content ?? "空白对话";
            const isActive = session.id === activeSessionId;
            return (
              <div key={session.id} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelectSession(session.id)}
                  className={[
                    "w-full rounded-2xl px-4 py-3 text-left transition-all border border-transparent",
                    isActive
                      ? "bg-primary/5 border-primary/10"
                      : "bg-transparent hover:bg-muted/40 hover:border-border/30",
                  ].join(" ")}
                >
                  <div className={`truncate pr-6 text-sm font-medium ${isActive ? 'text-primary' : 'text-foreground'}`}>
                    {session.title}
                  </div>
                  <div className="mt-1 line-clamp-2 text-xs text-muted-foreground/70">{preview}</div>
                  <div className="mt-2 text-[10px] text-muted-foreground/50">
                    {formatTime(session.updatedAt)}
                  </div>
                </button>

                {onDeleteSession && (
                  <button
                    type="button"
                    title="删除对话"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteSession(session.id);
                    }}
                    className="absolute right-2 top-2 z-10 hidden rounded-full p-1.5 text-muted-foreground hover:bg-rose-500/10 hover:text-rose-600 group-hover:flex transition-colors shrink-0 items-center justify-center bg-background/80 backdrop-blur-sm"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}

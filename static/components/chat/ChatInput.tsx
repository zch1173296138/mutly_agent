"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Settings2, Square, RotateCcw, Send } from "lucide-react";

type ChatInputProps = {
  onSend: (value: string) => void;
  onStop: () => void;
  onRegenerate: () => void;
  onOpenSettings: () => void;
  isStreaming: boolean;
};

export function ChatInput({ onSend, onStop, onRegenerate, onOpenSettings, isStreaming }: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  }, [value]);

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="px-4 pb-8 pt-4 w-full max-w-4xl mx-auto Shrink-0">
      <div className="relative flex flex-col gap-3 rounded-[32px] bg-background p-4 shadow-[0_0_40px_rgba(0,0,0,0.04)] border border-border/40 transition-shadow focus-within:shadow-[0_0_60px_rgba(0,0,0,0.06)]">
        <Textarea
          ref={textareaRef}
          placeholder="有什么我可以帮您的？"
          className="min-h-[50px] max-h-[140px] resize-none border-none bg-transparent px-4 py-2 text-base shadow-none focus-visible:ring-0"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              handleSend();
            }
          }}
        />
        <div className="flex flex-wrap items-center justify-between px-2 pb-1 gap-3">
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="gap-2 text-muted-foreground hover:bg-muted/50 rounded-full h-9 px-4"
              onClick={onOpenSettings}
            >
              <Settings2 className="h-4 w-4" />
              <span className="text-sm">设置</span>
            </Button>
            {isStreaming ? (
              <Button variant="ghost" size="sm" className="gap-2 text-muted-foreground hover:bg-muted/50 rounded-full h-9 px-4" onClick={onStop}>
                <Square className="h-4 w-4" />
                <span className="text-sm">停止生成</span>
              </Button>
            ) : (
              <Button variant="ghost" size="sm" className="gap-2 text-muted-foreground hover:bg-muted/50 rounded-full h-9 px-4" onClick={onRegenerate}>
                <RotateCcw className="h-4 w-4" />
                <span className="text-sm">重新生成</span>
              </Button>
            )}
          </div>
          <Button 
            onClick={handleSend} 
            size="sm"
            className="gap-2 rounded-full h-10 px-5 shadow-sm text-sm"
            disabled={!value.trim()}
          >
            发送
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

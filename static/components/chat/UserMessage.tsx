"use client";

import { motion } from "framer-motion";
import type { UserMessage as UserMessageType } from "@/lib/types";

export function UserMessage({ message }: { message: UserMessageType }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex justify-end mb-4"
    >
      <div className="max-w-[80%] rounded-[24px] rounded-br-[8px] bg-foreground px-6 py-4 text-[15px] leading-relaxed text-background shadow-[0_4px_14px_rgba(0,0,0,0.05)] border border-border/10">
        {message.content}
      </div>
    </motion.div>
  );
}

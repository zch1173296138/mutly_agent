import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";

interface TopBarProps {
  onToggleSidebar?: () => void;
}

export function TopBar({ onToggleSidebar }: TopBarProps) {
  return (
    <div className="flex items-center justify-between border-b border-border/30 bg-background/80 px-5 py-3 backdrop-blur shadow-[0_1px_3px_0_rgba(0,0,0,0.01)] z-20 relative transition-all">
      <div className="flex items-center gap-3">
        {onToggleSidebar && (
          <Button variant="ghost" size="icon" className="h-9 w-9 rounded-full text-muted-foreground mr-1 hover:bg-muted/50" onClick={onToggleSidebar}>
            <Menu className="h-5 w-5" />
          </Button>
        )}
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/5 text-primary ring-1 ring-primary/10">
          <span className="text-sm font-bold">◎</span>
        </div>
        <div>
          <p className="text-sm font-semibold text-foreground tracking-tight">深度研究助手</p>
        </div>
      </div>
    </div>
  );
}

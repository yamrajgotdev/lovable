import { Link } from "@tanstack/react-router";
import { Brand } from "./Brand";
import { NotificationBell } from "./NotificationBell";
import { ThemeToggle } from "./ThemeToggle";
import { LanguageToggle } from "./LanguageToggle";
import { useTranslation } from "@/hooks/useTranslation";

export function Navbar({ to = "/", wsMsg }: { to?: string; wsMsg?: any }) {
  const { t } = useTranslation();
  return (
    <header className="sticky top-0 z-20 flex items-center justify-between bg-background/70 px-5 py-4 backdrop-blur-md border-b border-white/5">
      <div className="flex items-center gap-3">
        <Brand to={to} />
        <div className="h-4 w-px bg-white/10" />
        <NotificationBell wsMsg={wsMsg} />
      </div>
      
      <div className="flex items-center gap-2">
        <LanguageToggle />
        <ThemeToggle />
        {to.startsWith("/passenger") ? (
          <div className="flex items-center gap-3 text-sm">
            <Link 
              to="/passenger/history" 
              className="text-muted-foreground transition hover:text-foreground hidden sm:block"
              activeProps={{ className: "text-foreground" }}
            >
              {t("history")}
            </Link>
            <span className="h-4 w-px bg-border hidden sm:block" />
            <Link 
              to="/passenger/settings" 
              className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
              activeProps={{ className: "text-foreground" }}
            >
              {t("account")}
            </Link>
          </div>
        ) : (
          <div className="flex items-center gap-3 text-sm">
            <Link 
              to="/driver/history" 
              className="text-muted-foreground transition hover:text-foreground"
              activeProps={{ className: "text-foreground" }}
            >
              {t("history")}
            </Link>
            <span className="h-4 w-px bg-border" />
            <Link 
              to="/driver/earnings" 
              className="text-muted-foreground transition hover:text-foreground"
              activeProps={{ className: "text-foreground" }}
            >
              {t("earnings")}
            </Link>
            <span className="h-4 w-px bg-border" />
            <Link 
              to="/driver/settings" 
              className="text-muted-foreground transition hover:text-foreground"
              activeProps={{ className: "text-foreground" }}
            >
              {t("account")}
            </Link>
          </div>
        )}
      </div>
    </header>
  );
}

import { useTranslation } from "@/hooks/useTranslation";
import { Globe } from "lucide-react";

export function LanguageToggle() {
  const { lang, setLanguage } = useTranslation();

  const toggleLanguage = () => {
    setLanguage(lang === "en" ? "hi" : "en");
    // Translations update reactively via context
  };

  return (
    <button
      onClick={toggleLanguage}
      className="flex items-center justify-center w-9 h-9 rounded-lg bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground transition-colors press"
      aria-label={lang === "en" ? "Switch to Hindi" : "Switch to English"}
      title={lang === "en" ? "Switch to Hindi" : "Switch to English"}
    >
      <span className="text-sm font-semibold">{lang === "en" ? "EN" : "हि"}</span>
    </button>
  );
}

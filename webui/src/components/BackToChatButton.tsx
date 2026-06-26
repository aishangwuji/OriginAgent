import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

interface BackToChatButtonProps {
  onBackToChat: () => void;
}

export function BackToChatButton({ onBackToChat }: BackToChatButtonProps) {
  const { t } = useTranslation();

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="rounded-full"
      onClick={onBackToChat}
      aria-label={t("common.backToChat", "Back to chat")}
    >
      <ArrowLeft className="h-4 w-4" aria-hidden />
    </Button>
  );
}

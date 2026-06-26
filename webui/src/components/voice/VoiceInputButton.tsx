import { Mic, MicOff, Square, AlertCircle, Loader2 } from "lucide-react";
import type { VoiceRecorderState } from "@/hooks/useVoiceRecorder";

export interface VoiceInputButtonProps {
  state: VoiceRecorderState;
  elapsed: number;
  error: string | null;
  supported: boolean;
  onStart: () => void;
  onStop: () => void;
  onCancel: () => void;
  disabled?: boolean;
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function VoiceInputButton({
  state,
  elapsed,
  error,
  supported,
  onStart,
  onStop,
  onCancel,
  disabled,
}: VoiceInputButtonProps) {
  if (!supported) return null;

  const isRecording = state === "recording";
  const isProcessing = state === "processing" || state === "requesting";

  if (isProcessing) {
    return (
      <button
        type="button"
        disabled
        className="inline-flex items-center justify-center w-9 h-9 rounded-full
                   bg-muted text-muted-foreground animate-pulse"
        aria-label="Processing voice..."
      >
        <Loader2 className="w-4 h-4 animate-spin" />
      </button>
    );
  }

  if (error) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={onStart}
        className="inline-flex items-center justify-center w-9 h-9 rounded-full
                   bg-destructive/10 text-destructive hover:bg-destructive/20
                   transition-colors"
        title={error}
        aria-label={`Voice error: ${error}. Click to retry.`}
      >
        <AlertCircle className="w-4 h-4" />
      </button>
    );
  }

  if (isRecording) {
    return (
      <div className="inline-flex items-center gap-1.5">
        <span className="text-xs tabular-nums text-red-500 animate-pulse font-mono min-w-[2.5rem]">
          {formatElapsed(elapsed)}
        </span>
        <button
          type="button"
          disabled={disabled}
          onClick={onStop}
          className="inline-flex items-center justify-center w-9 h-9 rounded-full
                     bg-red-500 text-white hover:bg-red-600 transition-colors
                     shadow-sm shadow-red-500/30"
          aria-label="Stop recording"
        >
          <Square className="w-3.5 h-3.5 fill-current" />
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={onCancel}
          className="inline-flex items-center justify-center w-7 h-7 rounded-full
                     bg-muted text-muted-foreground hover:bg-muted-foreground/20
                     transition-colors"
          aria-label="Cancel recording"
        >
          <MicOff className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onStart}
      className="inline-flex items-center justify-center w-9 h-9 rounded-full
                 bg-muted text-muted-foreground hover:bg-primary/10
                 hover:text-primary transition-colors"
      aria-label="Start voice recording"
      title="Hold to talk"
    >
      <Mic className="w-4 h-4" />
    </button>
  );
}

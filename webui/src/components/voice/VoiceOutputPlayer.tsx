import { useCallback, useRef, useState } from "react";
import { Volume2, Loader2, Square } from "lucide-react";

export interface VoiceOutputPlayerProps {
  /** URL of the TTS audio to play (signed media URL). */
  audioUrl: string;
}

export function VoiceOutputPlayer({ audioUrl }: VoiceOutputPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleClick = useCallback(() => {
    if (playing) {
      // Stop playback
      audioRef.current?.pause();
      audioRef.current = null;
      setPlaying(false);
      return;
    }

    setLoading(true);
    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    audio.preload = "auto";

    audio.addEventListener("canplaythrough", () => setLoading(false));
    audio.addEventListener("play", () => setPlaying(true));
    audio.addEventListener("ended", () => {
      setPlaying(false);
      audioRef.current = null;
    });
    audio.addEventListener("error", () => {
      setLoading(false);
      setPlaying(false);
      audioRef.current = null;
    });

    audio.play().catch(() => {
      setLoading(false);
    });
  }, [audioUrl, playing]);

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={loading}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-full
                 bg-muted/50 hover:bg-muted/80 text-muted-foreground
                 hover:text-foreground transition-colors text-xs"
      title={playing ? "Stop" : "Play voice reply"}
      aria-label={playing ? "Stop voice" : "Play voice reply"}
    >
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      ) : playing ? (
        <Square className="w-3 h-3 fill-current" />
      ) : (
        <Volume2 className="w-3.5 h-3.5" />
      )}
      <span>{playing ? "Stop" : loading ? "Loading..." : "Voice"}</span>
    </button>
  );
}

import { useEffect, useRef, useState } from "react";
import { Volume2, Loader2 } from "lucide-react";

export interface VoiceOutputPlayerProps {
  /** URL or data URL of the TTS audio to play. */
  audioUrl: string | null;
  /** Called when the audio finishes playing. */
  onEnded?: () => void;
}

export function VoiceOutputPlayer({ audioUrl, onEnded }: VoiceOutputPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!audioUrl) return;
    setLoading(true);

    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    audio.preload = "auto";

    const onCanPlay = () => setLoading(false);
    const onPlay = () => setPlaying(true);
    const onEnd = () => {
      setPlaying(false);
      onEnded?.();
    };
    const onErr = () => {
      setLoading(false);
      setPlaying(false);
    };

    audio.addEventListener("canplaythrough", onCanPlay);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("ended", onEnd);
    audio.addEventListener("error", onErr);

    audio.play().catch(() => {
      // Autoplay may be blocked — still show the player
      setLoading(false);
    });

    return () => {
      audio.removeEventListener("canplaythrough", onCanPlay);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("ended", onEnd);
      audio.removeEventListener("error", onErr);
      audio.pause();
      audio.src = "";
      audioRef.current = null;
    };
  }, [audioUrl, onEnded]);

  if (!audioUrl) return null;

  return (
    <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full bg-muted/50">
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />
      ) : playing ? (
        <Volume2 className="w-3.5 h-3.5 text-primary animate-pulse" />
      ) : (
        <Volume2 className="w-3.5 h-3.5 text-muted-foreground" />
      )}
      <span className="text-xs text-muted-foreground">
        {loading ? "Loading..." : playing ? "Playing..." : "Voice reply"}
      </span>
    </div>
  );
}

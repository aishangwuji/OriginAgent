import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceRecorderState = "idle" | "requesting" | "recording" | "processing";

export interface VoiceRecorderResult {
  /** Current recorder lifecycle state. */
  state: VoiceRecorderState;
  /** Elapsed recording seconds (approximate). */
  elapsed: number;
  /** Latest error message, or null. */
  error: string | null;
  /** Start recording (requests mic permission on first call). */
  start: () => Promise<void>;
  /** Stop recording and return the audio as a base64 data URL. */
  stop: () => Promise<string | null>;
  /** Cancel recording without producing a result. */
  cancel: () => void;
  /** True when the browser supports MediaRecorder. */
  supported: boolean;
}

const MAX_RECORD_MS = 60_000; // 60 seconds max

function mimeType(): string {
  const preferred = ["audio/webm", "audio/ogg", "audio/wav", "audio/mp4"];
  if (typeof MediaRecorder === "undefined") return "";
  for (const m of preferred) {
    if (MediaRecorder.isTypeSupported?.(m)) return m;
  }
  return "";
}

export function useVoiceRecorder(): VoiceRecorderResult {
  const [state, setState] = useState<VoiceRecorderState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const supported = typeof MediaRecorder !== "undefined" && mimeType() !== "";

  const cleanup = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      try { recorderRef.current.stop(); } catch { /* already stopped */ }
    }
    recorderRef.current = null;
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop();
      streamRef.current = null;
    }
  }, []);

  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  const start = useCallback(async () => {
    if (!supported) {
      setError("MediaRecorder not supported in this browser");
      return;
    }
    setError(null);
    setState("requesting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream, {
        mimeType: mimeType(),
      });
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (e: BlobEvent) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onerror = () => {
        setError("Recording error");
        setState("idle");
        cleanup();
      };

      recorder.onstop = () => {
        setState("processing");
      };

      recorder.start(250); // emit data every 250ms
      setState("recording");
      setElapsed(0);

      timerRef.current = setInterval(() => {
        setElapsed((prev) => {
          if (prev >= MAX_RECORD_MS / 1000) {
            // Auto-stop at max duration
            if (recorderRef.current?.state === "recording") {
              recorderRef.current.stop();
            }
            return prev;
          }
          return prev + 0.25;
        });
      }, 250);
    } catch (err: unknown) {
      const message =
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone permission denied"
          : err instanceof Error
            ? err.message
            : "Failed to start recording";
      setError(message);
      setState("idle");
    }
  }, [supported, cleanup]);

  const stopRecording = useCallback(
    async (shouldProduce: boolean): Promise<string | null> => {
      if (!recorderRef.current || recorderRef.current.state !== "recording") {
        setState("idle");
        return null;
      }

      return new Promise<string | null>((resolve) => {
        const recorder = recorderRef.current!;
        recorder.onstop = async () => {
          if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
          }

          // Stop the mic stream
          if (streamRef.current) {
            for (const track of streamRef.current.getTracks()) track.stop();
            streamRef.current = null;
          }

          if (!shouldProduce || chunksRef.current.length === 0) {
            setState("idle");
            resolve(null);
            return;
          }

          const mime = mimeType();
          const blob = new Blob(chunksRef.current, { type: mime });
          chunksRef.current = [];

          try {
            const dataUrl = await blobToDataURL(blob);
            setState("idle");
            resolve(dataUrl);
          } catch {
            setError("Failed to encode audio");
            setState("idle");
            resolve(null);
          }
        };

        recorder.stop();
      });
    },
    [],
  );

  const stop = useCallback(() => stopRecording(true), [stopRecording]);
  const cancel = useCallback(() => {
    stopRecording(false);
  }, [stopRecording]);

  return { state, elapsed, error, start, stop, cancel, supported };
}

function blobToDataURL(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error("FileReader error"));
    reader.readAsDataURL(blob);
  });
}

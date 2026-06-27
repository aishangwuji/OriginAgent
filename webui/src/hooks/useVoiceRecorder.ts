import { useCallback, useRef, useState } from "react";

export type VoiceRecorderState = "idle" | "listening" | "processing" | "error";

export interface VoiceRecorderResult {
  state: VoiceRecorderState;
  /** Latest partial transcript (updates in real time while listening). */
  interim: string;
  /** Latest final transcript (set when speech ends). */
  transcript: string;
  error: string | null;
  /** Start listening (requests mic permission on first call). */
  start: () => void;
  /** Stop listening and return the final transcript. */
  stop: () => string | null;
  /** Cancel listening without producing a result. */
  cancel: () => void;
  /** True when the browser supports SpeechRecognition. */
  supported: boolean;
}

// Web Speech API — only available in Chromium-based browsers. Access it
// through the window object since TypeScript's dom lib doesn't include it.
const SpeechCtor: { new(): { start: () => void; stop: () => void; abort: () => void; continuous: boolean; interimResults: boolean; lang: string; onresult: ((ev: any) => void) | null; onerror: ((ev: any) => void) | null; onend: (() => void) | null } } | undefined =
  typeof window !== "undefined"
    ? (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition
    : undefined;

export function useVoiceRecorder(): VoiceRecorderResult {
  const [state, setState] = useState<VoiceRecorderState>("idle");
  const [interim, setInterim] = useState("");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<any>(null);
  const finalTranscriptRef = useRef("");

  const supported = !!SpeechCtor;

  const start = useCallback(() => {
    if (!SpeechCtor) {
      setError("Speech recognition not supported in this browser");
      return;
    }

    setError(null);
    setInterim("");
    setTranscript("");
    finalTranscriptRef.current = "";
    setState("listening");

    const recognition = new SpeechCtor();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "zh-CN";

    recognition.onresult = (event: any) => {
      let final = "";
      let interimText = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          final += result[0].transcript;
        } else {
          interimText += result[0].transcript;
        }
      }

      if (final) {
        finalTranscriptRef.current += final;
        setTranscript(finalTranscriptRef.current);
      }
      setInterim(interimText);
    };

    recognition.onerror = () => {
      setError("Recognition error");
      setState("error");
      recognitionRef.current = null;
    };

    recognition.onend = () => {
      if (recognitionRef.current !== null) {
        setState("processing");
        recognitionRef.current = null;
      }
    };

    recognition.start();
    recognitionRef.current = recognition;
  }, []);

  const stop = useCallback((): string | null => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    const text = finalTranscriptRef.current;
    setTranscript(text);
    setState("idle");
    return text || null;
  }, []);

  const cancel = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }
    finalTranscriptRef.current = "";
    setInterim("");
    setTranscript("");
    setState("idle");
  }, []);

  return { state, interim, transcript, error, start, stop, cancel, supported };
}

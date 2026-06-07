import { useCallback, useEffect, useRef, useState } from "react";

import { encodeImage, type EncodeFailure } from "@/lib/imageEncode";

export type AttachmentStatus = "encoding" | "ready" | "error";

export type AttachmentCategory = "image" | "video" | "audio" | "document";

export interface AttachedAttachment {
  id: string;
  file: File;
  kind: AttachmentCategory;
  /** Optional optimistic ``blob:`` preview URL for image thumbnails. */
  previewUrl?: string;
  status: AttachmentStatus;
  dataUrl?: string;
  encodedBytes?: number;
  normalized?: boolean;
  error?: AttachmentError;
}

/** Back-compat alias; the composer now stages generic attachments. */
export type AttachedImage = AttachedAttachment;

export type AttachmentError =
  | "unsupported_type"
  | "too_many_attachments"
  | "too_many_images"
  | "too_many_videos"
  | "too_many_audio"
  | "magic_mismatch"
  | "decode_failed"
  | "too_large"
  | "io";

export const MAX_ATTACHMENTS_PER_MESSAGE = 6;
export const MAX_IMAGES_PER_MESSAGE = 4;
export const MAX_VIDEOS_PER_MESSAGE = 1;
export const MAX_AUDIOS_PER_MESSAGE = 2;
export const MAX_TOTAL_ATTACHMENT_BYTES = 24 * 1024 * 1024;

const MAX_BYTES_BY_KIND: Readonly<Record<Exclude<AttachmentCategory, "image">, number>> = {
  video: 20 * 1024 * 1024,
  audio: 12 * 1024 * 1024,
  document: 10 * 1024 * 1024,
};

const IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);

const VIDEO_MIMES = new Set([
  "video/mp4",
  "video/webm",
  "video/quicktime",
]);

const AUDIO_MIMES = new Set([
  "audio/mpeg",
  "audio/mp3",
  "audio/mp4",
  "audio/x-m4a",
  "audio/wav",
  "audio/x-wav",
  "audio/ogg",
  "audio/webm",
  "audio/flac",
  "audio/aac",
]);

const DOCUMENT_MIMES = new Set(["application/pdf"]);

const MIME_BY_EXTENSION: Readonly<Record<string, string>> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mov": "video/quicktime",
  ".mp3": "audio/mpeg",
  ".m4a": "audio/x-m4a",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".flac": "audio/flac",
  ".aac": "audio/aac",
  ".pdf": "application/pdf",
};

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function resolvedMime(file: File): string | null {
  const byType = file.type.trim().toLowerCase();
  if (byType) return byType;
  return MIME_BY_EXTENSION[extensionOf(file.name)] ?? null;
}

function classifyAttachment(file: File): { kind: AttachmentCategory; mime: string } | null {
  const mime = resolvedMime(file);
  if (!mime) return null;
  if (IMAGE_MIMES.has(mime)) return { kind: "image", mime };
  if (VIDEO_MIMES.has(mime)) return { kind: "video", mime };
  if (AUDIO_MIMES.has(mime)) return { kind: "audio", mime };
  if (DOCUMENT_MIMES.has(mime)) return { kind: "document", mime };
  return null;
}

function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return (crypto as Crypto).randomUUID();
  }
  return `att-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function mapEncodeFailure(reason: EncodeFailure["reason"]): AttachmentError {
  switch (reason) {
    case "invalid_mime":
    case "magic_mismatch":
      return "magic_mismatch";
    case "too_large_after_normalize":
      return "too_large";
    case "io":
      return "io";
    case "decode_failed":
    default:
      return "decode_failed";
  }
}

function readFileAsDataUrl(file: File, mime: string): Promise<{ dataUrl: string; bytes: number }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const blob =
      file.type.trim().toLowerCase() === mime
        ? file
        : new File([file], file.name, { type: mime });
    reader.onerror = () => reject(new Error("file read failed"));
    reader.onload = () => {
      const value = reader.result;
      if (typeof value !== "string" || !value.startsWith("data:")) {
        reject(new Error("file read failed"));
        return;
      }
      resolve({ dataUrl: value, bytes: file.size });
    };
    reader.readAsDataURL(blob);
  });
}

function stagedBytes(entries: AttachedAttachment[]): number {
  return entries.reduce((sum, entry) => sum + entry.file.size, 0);
}

function stagedCount(entries: AttachedAttachment[], kind: AttachmentCategory): number {
  return entries.reduce((sum, entry) => sum + (entry.kind === kind ? 1 : 0), 0);
}

export interface UseAttachedImagesApi {
  images: AttachedAttachment[];
  enqueue: (files: Iterable<File>) => {
    rejected: Array<{ file: File; reason: AttachmentError }>;
  };
  remove: (id: string) => { nextFocusId: string | null };
  clear: () => void;
  encoding: boolean;
  full: boolean;
}

/** Manage staged composer attachments.
 *
 * Images still flow through the existing worker-based normalization path.
 * Other supported media types are preserved byte-for-byte as base64 data URLs.
 */
export function useAttachedImages(): UseAttachedImagesApi {
  const [images, setImages] = useState<AttachedAttachment[]>([]);
  const imagesRef = useRef<AttachedAttachment[]>([]);
  imagesRef.current = images;

  const setEntry = useCallback((id: string, patch: Partial<AttachedAttachment>) => {
    setImages((prev) => {
      const next = prev.map((img) => (img.id === id ? { ...img, ...patch } : img));
      imagesRef.current = next;
      return next;
    });
  }, []);

  const enqueue = useCallback(
    (files: Iterable<File>) => {
      const rejected: Array<{ file: File; reason: AttachmentError }> = [];
      const toAdd: Array<AttachedAttachment & { mime: string }> = [];
      let totalCount = imagesRef.current.length;
      let imageCount = stagedCount(imagesRef.current, "image");
      let videoCount = stagedCount(imagesRef.current, "video");
      let audioCount = stagedCount(imagesRef.current, "audio");
      let totalBytes = stagedBytes(imagesRef.current);

      for (const file of files) {
        const classified = classifyAttachment(file);
        if (!classified) {
          rejected.push({ file, reason: "unsupported_type" });
          continue;
        }
        if (totalCount >= MAX_ATTACHMENTS_PER_MESSAGE) {
          rejected.push({ file, reason: "too_many_attachments" });
          continue;
        }
        if (classified.kind === "image" && imageCount >= MAX_IMAGES_PER_MESSAGE) {
          rejected.push({ file, reason: "too_many_images" });
          continue;
        }
        if (classified.kind === "video" && videoCount >= MAX_VIDEOS_PER_MESSAGE) {
          rejected.push({ file, reason: "too_many_videos" });
          continue;
        }
        if (classified.kind === "audio" && audioCount >= MAX_AUDIOS_PER_MESSAGE) {
          rejected.push({ file, reason: "too_many_audio" });
          continue;
        }
        if (
          classified.kind !== "image"
          && file.size > MAX_BYTES_BY_KIND[classified.kind]
        ) {
          rejected.push({ file, reason: "too_large" });
          continue;
        }
        if (totalBytes + file.size > MAX_TOTAL_ATTACHMENT_BYTES) {
          rejected.push({ file, reason: "too_large" });
          continue;
        }

        totalCount += 1;
        totalBytes += file.size;
        if (classified.kind === "image") imageCount += 1;
        if (classified.kind === "video") videoCount += 1;
        if (classified.kind === "audio") audioCount += 1;

        toAdd.push({
          id: uuid(),
          file,
          kind: classified.kind,
          previewUrl: classified.kind === "image" ? URL.createObjectURL(file) : undefined,
          status: "encoding",
          mime: classified.mime,
        });
      }

      if (toAdd.length > 0) {
        const staged = toAdd.map(({ mime: _mime, ...entry }) => entry);
        const next = [...imagesRef.current, ...staged];
        imagesRef.current = next;
        setImages(next);
        for (const entry of toAdd) {
          queueMicrotask(() => {
            if (entry.kind === "image") {
              encodeImage(entry.file).then(
                (result) => {
                  if (result.ok) {
                    setEntry(entry.id, {
                      status: "ready",
                      dataUrl: result.dataUrl,
                      encodedBytes: result.bytes,
                      normalized: result.normalized,
                    });
                  } else {
                    setEntry(entry.id, {
                      status: "error",
                      error: mapEncodeFailure(result.reason),
                    });
                  }
                },
                () => {
                  setEntry(entry.id, {
                    status: "error",
                    error: "decode_failed",
                  });
                },
              );
              return;
            }

            readFileAsDataUrl(entry.file, entry.mime).then(
              (result) => {
                setEntry(entry.id, {
                  status: "ready",
                  dataUrl: result.dataUrl,
                  encodedBytes: result.bytes,
                  normalized: false,
                });
              },
              () => {
                setEntry(entry.id, {
                  status: "error",
                  error: "io",
                });
              },
            );
          });
        }
      }
      return { rejected };
    },
    [setEntry],
  );

  const remove = useCallback((id: string) => {
    let nextFocusId: string | null = null;
    setImages((prev) => {
      const idx = prev.findIndex((img) => img.id === id);
      if (idx === -1) return prev;
      const target = prev[idx];
      if (target.previewUrl) {
        try {
          URL.revokeObjectURL(target.previewUrl);
        } catch {
          // Best-effort cleanup.
        }
      }
      const next = [...prev.slice(0, idx), ...prev.slice(idx + 1)];
      imagesRef.current = next;
      const candidate = next[idx] ?? next[idx - 1];
      nextFocusId = candidate?.id ?? null;
      return next;
    });
    return { nextFocusId };
  }, []);

  const clear = useCallback(() => {
    setImages((prev) => {
      for (const img of prev) {
        if (!img.previewUrl) continue;
        try {
          URL.revokeObjectURL(img.previewUrl);
        } catch {
          // best-effort
        }
      }
      imagesRef.current = [];
      return [];
    });
  }, []);

  useEffect(() => {
    return () => {
      for (const img of imagesRef.current) {
        if (!img.previewUrl) continue;
        try {
          URL.revokeObjectURL(img.previewUrl);
        } catch {
          // best-effort cleanup on unmount
        }
      }
    };
  }, []);

  const encoding = images.some((img) => img.status === "encoding");
  const full = images.length >= MAX_ATTACHMENTS_PER_MESSAGE;

  return { images, enqueue, remove, clear, encoding, full };
}

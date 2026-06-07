import { useCallback, useRef, useState } from "react";

const SUPPORTED_PREFIXES = ["image/", "video/", "audio/"];
const SUPPORTED_EXACT = new Set(["application/pdf"]);

function isSupportedFile(file: File | null): file is File {
  if (!file) return false;
  const type = file.type.trim().toLowerCase();
  if (!type) return false;
  return SUPPORTED_PREFIXES.some((prefix) => type.startsWith(prefix)) || SUPPORTED_EXACT.has(type);
}

/** Extract staged attachment ``File``s from a paste event.
 *
 * Only file payloads are surfaced; pasted HTML and remote URLs are ignored so
 * the textarea can keep handling plain text naturally.
 */
export function extractImageFilesFromPaste(
  event: ClipboardEvent | React.ClipboardEvent,
): File[] {
  const clipboard = (event as ClipboardEvent).clipboardData
    ?? (event as React.ClipboardEvent).clipboardData;
  if (!clipboard) return [];
  const files: File[] = [];
  for (const item of Array.from(clipboard.items)) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (isSupportedFile(file)) files.push(file);
  }
  return files;
}

/** Extract dropped attachments, mirroring ``extractImageFilesFromPaste``. */
export function extractImageFilesFromDrop(
  event: DragEvent | React.DragEvent,
): File[] {
  const dt = (event as DragEvent).dataTransfer
    ?? (event as React.DragEvent).dataTransfer;
  if (!dt) return [];
  const files: File[] = [];
  for (const item of Array.from(dt.files)) {
    if (isSupportedFile(item)) files.push(item);
  }
  return files;
}

export interface UseClipboardAndDropApi {
  isDragging: boolean;
  onPaste: (event: React.ClipboardEvent) => void;
  onDragEnter: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  onDragLeave: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
}

export function useClipboardAndDrop(
  onFiles: (files: File[]) => void,
): UseClipboardAndDropApi {
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);

  const onPaste = useCallback(
    (event: React.ClipboardEvent) => {
      const files = extractImageFilesFromPaste(event);
      if (files.length === 0) return;
      event.preventDefault();
      onFiles(files);
    },
    [onFiles],
  );

  const onDragEnter = useCallback((event: React.DragEvent) => {
    if (!Array.from(event.dataTransfer.types ?? []).includes("Files")) return;
    event.preventDefault();
    dragDepth.current += 1;
    setIsDragging(true);
  }, []);

  const onDragOver = useCallback((event: React.DragEvent) => {
    if (!Array.from(event.dataTransfer.types ?? []).includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  const onDragLeave = useCallback((event: React.DragEvent) => {
    if (!Array.from(event.dataTransfer.types ?? []).includes("Files")) return;
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragging(false);
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      dragDepth.current = 0;
      setIsDragging(false);
      const files = extractImageFilesFromDrop(event);
      if (files.length === 0) return;
      event.preventDefault();
      onFiles(files);
    },
    [onFiles],
  );

  return { isDragging, onPaste, onDragEnter, onDragOver, onDragLeave, onDrop };
}

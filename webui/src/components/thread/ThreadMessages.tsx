import { useEffect, useRef, type ReactNode } from "react";

import { MessageBubble } from "@/components/MessageBubble";
import {
  AgentActivityCluster,
  isAgentActivityMember,
} from "@/components/thread/AgentActivityCluster";
import type { VirtualDisplayUnit } from "@/hooks/useThreadVirtualizer";
import type { UIMessage } from "@/lib/types";

interface ThreadMessagesProps {
  messages: UIMessage[];
  /** When true, agent turn still in flight — keeps activity cluster expanded. */
  isStreaming?: boolean;
}

interface VirtualThreadMessagesProps {
  virtualItems: VirtualDisplayUnit[];
  totalHeight: number;
  allUnits: DisplayUnit[];
  /** When true, agent turn still in flight — keeps activity cluster expanded. */
  isStreaming?: boolean;
  onUnitMeasured: (key: string, height: number) => void;
}

export type DisplayUnit =
  | { type: "cluster"; messages: UIMessage[] }
  | { type: "single"; message: UIMessage };

/** True when this unit index is the last assistant text slice before the next user message (or end of thread). */
export function isFinalAssistantSliceBeforeNextUser(
  units: DisplayUnit[],
  index: number,
): boolean {
  const u = units[index];
  if (u.type !== "single" || u.message.role !== "assistant") return true;
  for (let j = index + 1; j < units.length; j++) {
    const v = units[j];
    if (v.type === "single" && v.message.role === "user") break;
    return false;
  }
  return true;
}

export function buildDisplayUnits(messages: UIMessage[]): DisplayUnit[] {
  const out: DisplayUnit[] = [];
  let i = 0;
  while (i < messages.length) {
    const m = messages[i];
    if (isAgentActivityMember(m)) {
      const cluster: UIMessage[] = [];
      while (i < messages.length && isAgentActivityMember(messages[i])) {
        cluster.push(messages[i]);
        i += 1;
      }
      out.push({ type: "cluster", messages: cluster });
      continue;
    }
    out.push({ type: "single", message: m });
    i += 1;
  }
  return out;
}

export function ThreadMessages({ messages, isStreaming = false }: ThreadMessagesProps) {
  const units = buildDisplayUnits(messages);

  return (
    <div className="flex w-full flex-col">
      {units.map((unit, index) => {
        return (
          <div
            key={unitKey(unit, index)}
            className={index > 0 ? marginAfterPrevUnit(units[index - 1]) : ""}
          >
            {renderDisplayUnit(unit, index, units, isStreaming)}
          </div>
        );
      })}
    </div>
  );
}

export function VirtualThreadMessages({
  virtualItems,
  totalHeight,
  allUnits,
  isStreaming = false,
  onUnitMeasured,
}: VirtualThreadMessagesProps) {
  let cursor = 0;
  return (
    <div className="flex w-full flex-col" style={{ minHeight: totalHeight }}>
      {virtualItems.map((item) => {
        const gap = Math.max(0, item.top - cursor);
        cursor = item.top + item.height;
        return (
          <MeasuredUnit
            key={item.key}
            unitKey={item.key}
            gapBefore={gap}
            marginBefore={item.index > 0 ? marginAfterPrevUnitPx(allUnits[item.index - 1]) : 0}
            onMeasured={onUnitMeasured}
          >
            {renderDisplayUnit(item.unit, item.index, allUnits, isStreaming)}
          </MeasuredUnit>
        );
      })}
      <div aria-hidden style={{ height: Math.max(0, totalHeight - cursor) }} />
    </div>
  );
}

interface MeasuredUnitProps {
  unitKey: string;
  gapBefore: number;
  marginBefore: number;
  onMeasured: (key: string, height: number) => void;
  children: ReactNode;
}

function MeasuredUnit({
  unitKey: key,
  gapBefore,
  marginBefore,
  onMeasured,
  children,
}: MeasuredUnitProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const report = () => {
      const rect = el.getBoundingClientRect();
      onMeasured(key, Math.ceil(rect.height));
    };
    report();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, [key, onMeasured]);

  return (
    <>
      {gapBefore > 0 ? <div aria-hidden style={{ height: gapBefore }} /> : null}
      <div ref={ref} style={{ paddingTop: marginBefore }}>{children}</div>
    </>
  );
}

function renderDisplayUnit(
  unit: DisplayUnit,
  index: number,
  units: DisplayUnit[],
  isStreaming: boolean,
): ReactNode {
  const next = units[index + 1];
  const hasBodyBelow =
    unit.type === "cluster"
    && next?.type === "single"
    && next.message.role === "assistant";

  if (unit.type === "cluster") {
    return (
      <AgentActivityCluster
        messages={unit.messages}
        isTurnStreaming={isStreaming}
        hasBodyBelow={hasBodyBelow}
      />
    );
  }
  return (
    <MessageBubble
      message={unit.message}
      showAssistantCopyAction={
        unit.message.role === "assistant"
          ? isFinalAssistantSliceBeforeNextUser(units, index)
          : true
      }
    />
  );
}

export function unitKey(unit: DisplayUnit, index: number): string {
  if (unit.type === "cluster") {
    const anchor = unit.messages[0]?.id;
    return anchor != null ? `cluster-${anchor}` : `cluster-idx-${index}`;
  }
  return unit.message.id;
}

export function marginAfterPrevUnit(prev: DisplayUnit): string {
  if (prev.type === "cluster") {
    return "mt-4";
  }
  const p = prev.message;
  const denseP =
    p.kind === "trace"
    || (
      p.role === "assistant"
      && p.content.trim().length === 0
      && (!!p.reasoning || !!p.reasoningStreaming)
    );
  if (denseP) {
    return "mt-2";
  }
  return "mt-5";
}

export function marginAfterPrevUnitPx(prev: DisplayUnit): number {
  if (prev.type === "cluster") return 16;
  const p = prev.message;
  const denseP =
    p.kind === "trace"
    || (
      p.role === "assistant"
      && p.content.trim().length === 0
      && (!!p.reasoning || !!p.reasoningStreaming)
    );
  return denseP ? 8 : 20;
}

# Attachments Ingress

This project keeps attachment ingress provider-neutral. WebUI upload is only
one producer. The runtime contract is path-first so future producers such as
cameras, audio pipelines, document sync jobs, or local automation processes can
reuse the same handoff shape.

## Goals

- Keep the core Agent runtime independent from any single multimodal vendor.
- Preserve the existing `InboundMessage.media: list[str]` contract.
- Support multiple ingress producers without redesigning the session bus.
- Keep provider-native multimodal mapping at the adapter layer only.

## Directory Contract

- `workspace/uploads/<source>/`
  - Agent-managed staging area for files accepted by a built-in ingress path.
  - Current examples: `websocket`, `api`.
- `workspace/inbox/<source>/`
  - Reserved handoff area for future external producers.
  - External tools should place completed files here for Agent discovery.

## Producer Rules

- Write to a temporary name ending in `.part`.
- Atomically rename to the final filename only after the write completes.
- Prefer stable, collision-resistant filenames.
- If sidecar metadata is needed later, use the same basename plus `.json`.

Example:

```text
workspace/inbox/camera/alice-20260607T101530Z.jpg.part
workspace/inbox/camera/alice-20260607T101530Z.jpg
workspace/inbox/camera/alice-20260607T101530Z.json
```

## Current First-Class Attachment Types

- Image
- Video
- Audio
- PDF

Other document types may still be accepted through existing file paths, but
provider-native multimodal handling is only guaranteed for the validated set
above in this phase.

## Runtime Boundary

- Ingress normalizes accepted files onto workspace-visible paths.
- The Agent runtime only receives local file paths through `media`.
- Context building converts those paths into provider-neutral attachment blocks.
- Provider adapters may map those blocks to native multimodal request formats.
- If a provider cannot handle a native attachment kind, adapters must degrade
  gracefully to textual breadcrumbs rather than changing core runtime behavior.

## Why This Stays Provider-Neutral

Vendor docs such as Doubao or Ark can inform adapter work, but they must not
drive the core runtime shape. The stable abstraction is:

1. Stage file into a trusted workspace path.
2. Describe attachment generically.
3. Let the selected provider adapter decide whether to send it natively or
   degrade it.

## Future Extensions

- Inbox polling or event-driven watchers.
- Sidecar manifests with producer metadata.
- Liveness/health checks for external producers.
- Subagent triggers tied to specific inbox sources.

Those are intentionally out of scope for this change. This phase only reserves
the contract and aligns the current WebUI/API ingress with it.

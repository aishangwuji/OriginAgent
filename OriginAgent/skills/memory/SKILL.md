---
name: memory
description: Two-layer memory system with Dream-managed knowledge files.
always: true
---

# Memory

## Structure

- `SOUL.md` — Bot personality and communication style. Dream-managed.
- `USER.md` — User profile and preferences. User-authored file with an optional managed profile shadow section.
- `memory/MEMORY.md` — Long-term fact summary rendered from facts. Dream-managed.
- `memory/history.jsonl` — append-only JSONL, not loaded into context. Prefer the built-in `grep` tool to search it.
- `memory/nearline/` — sidecar layered memory artifacts: memcells, episodes, foresights, agent cases, profiles, and pipeline events.

## Responsibility Split

- Dream owns `facts.jsonl` consolidation and `memory/MEMORY.md` rendering.
- Nearline pipeline owns `memory/nearline/*.jsonl`, layered retrieval inputs, foresight follow-up inputs, and profile sidecar snapshots.
- Nearline objects are recall/proactivity aids, not human-review proposal sources.
- `USER.md` is not fully taken over by nearline. Only the managed profile shadow block may be updated automatically when enabled.

## Search Past Events

`memory/history.jsonl` is JSONL format — each line is a JSON object with `cursor`, `timestamp`, `content`.

- For broad searches, start with `grep(..., path="memory", glob="*.jsonl", output_mode="count")` or the default `files_with_matches` mode before expanding to full content
- Use `output_mode="content"` plus `context_before` / `context_after` when you need the exact matching lines
- Use `fixed_strings=true` for literal timestamps or JSON fragments
- Use `head_limit` / `offset` to page through long histories
- Use `exec` only as a last-resort fallback when the built-in search cannot express what you need

Examples (replace `keyword`):
- `grep(pattern="keyword", path="memory/history.jsonl", case_insensitive=true)`
- `grep(pattern="2026-04-02 10:00", path="memory/history.jsonl", fixed_strings=true)`
- `grep(pattern="keyword", path="memory", glob="*.jsonl", output_mode="count", case_insensitive=true)`
- `grep(pattern="oauth|token", path="memory", glob="*.jsonl", output_mode="content", case_insensitive=true)`

## Important

- Do not edit `SOUL.md` or `memory/MEMORY.md` directly.
- Treat `USER.md` as partly user-authored. Preserve manual content outside the managed profile section.
- If facts or long-term summaries look outdated, Dream is the owner.
- If episodes / foresights / agent cases / profiles look outdated, nearline pipeline is the owner.
- Users can view Dream's activity with the `/dream-log` command.

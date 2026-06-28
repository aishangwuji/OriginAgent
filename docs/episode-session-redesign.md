# Episode-Based Session Redesign

> Design document for transforming OriginAgent's session management from a
> storage-boundary model to an experience-continuity model.

**Status:** Draft  
**Author:** HongHouOS  
**Date:** 2026-06-27

---

## 1. Problem Statement

The current session model treats "session" as a **storage unit**:

- Session key = `channel:chat_id` (fixed, never changes)
- Session split = TTL timeout (`session_ttl_minutes`) + file size cap (2000 messages)
- Cross-session continuity = `continuity_checkpoint` JSON snapshot + LLM summaries
- Context loading = system guesses relevance via `RetrievalFusion` + `session_search`

This leaks storage concerns into user experience:

| What happens | Why it breaks |
|---|---|
| User talks for 30 min, leaves 2h, comes back → **new session** | TTL archived the old session |
| Continuity checkpoint only captures `current_goal` + `plan` + `open_loops` | Loses tone, rhythm, relationship context |
| LLM summary replaces raw messages after consolidation | "想你了感觉你好傻" → "用户表达了思念并批评了助手表现" — all emotional nuance lost |
| Session search returns facts, not conversation replay | Agent gets metadata, not experience |
| All context blocks get equal budget treatment | Recent topic-relevant messages compete with generic facts on token budget |

## 2. Core Concept: Episodes

Replace the monolithic session with **episodes** as the semantic unit of conversation.

### 2.1 Definition

An **episode** is a contiguous block of messages that covers one topic, task, or intent.
Episodes nest inside a session (`channel:chat_id`) and are tagged with metadata.

```python
@dataclass
class Episode:
    """A contiguous topic-block within a session."""
    episode_id: str          # uuid
    session_key: str         # channel:chat_id — owning session
    label: str               # optional human label, e.g. "BDI module design"
    started_at: datetime     # first message timestamp
    ended_at: datetime       # last message timestamp
    msg_start: int           # index into session.messages (inclusive)
    msg_end: int             # index into session.messages (exclusive)
    topic_embedding: list[float] | None = None  # optional semantic fingerprint
    status: str = "active"   # "active" | "closed" | "archived"
```

### 2.2 Session Changes

```python
@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]]
    episodes: list[Episode]          # NEW — ordered list of episodes
    active_episode_index: int = 0    # NEW — index into episodes
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
    last_consolidated: int = 0       # messages before this are in consolidated store
```

### 2.3 Episode Lifecycle

```
[user talks about topic A]           → Episode 0: "BDI design" (active)
[user says "不管这个了"]              → Episode 0 closed, Episode 1 begins
[user asks about something else]     → Episode 1: "New topic" (active)
[user comes back after 2h]           → Episode 1 still active (no topic change)
                                      → Episode 0 is "closed" → eligible for archive
```

**Episode start triggers** (implicit):
- First message in session → starts episode 0
- User says "/topic <label>" → closes current, opens new
- Agent detects semantic shift: similarity(topic_embedding) < threshold
- User explicitly changes subject ("换个话题", "不说这个了", "对了问你个事")

**Episode start triggers** (explicit):
- `/topic` slash command
- Agent asks "要不要开始一个新话题？" when it senses drift (optional)

**Episode close triggers:**
- New episode starts
- Agent explicitly closes it ("好的，BDI 的部分我们聊完了")
- Episode reaches `max_messages` threshold (default 200, configurable)
- Episode age exceeds `max_idle` threshold (default 7d, configurable)

### 2.4 Message Schema Extension

Each message gets an optional `episode_id` field:

```python
msg = {
    "role": "user",
    "content": "...",
    "timestamp": "...",
    "episode_id": "uuid-of-active-episode",  # NEW
    # ... existing fields
}
```

This is set when the message is added via `Session.add_message()`.

---

## 3. Context Loading Strategy

The core change: instead of loading all context and hoping relevance works,
**load episodes as the primary unit, then layer other context around them.**

### 3.1 Assembly Order (new)

```
[system_prompt]
  → [runtime_state]
  → [recovered_continuity_checkpoint]     # only if process restarted mid-turn
  → [active_episode_raw_history]          # NEW — raw messages from current episode
  → [recent_closed_episode_summaries]     # NEW — summary of recent closed episodes
  → [continuity_blocks]                   # working_memory, world_state (unchanged)
  → [reference_blocks]                    # facts, user_profile, session_search (unchanged)
  → [internal_event]
  → [current_user_message]
```

### 3.2 Active Episode Raw History

Replace `session.get_history(max_messages, max_tokens)` with episode-scoped loading:

```python
def get_episode_history(
    session: Session,
    episode: Episode,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Return raw messages from the active episode, trimmed to budget."""
    raw = session.messages[episode.msg_start:episode.msg_end]
    return trim_to_token_budget(raw, max_tokens)
```

If token budget allows, load the **entire active episode** raw. Only trim if
the episode is too long.

**Fallback:** If the active episode alone fits in budget, no trimming needed.
Only the current episode's messages are loaded — not the entire session history.
This prevents old, unrelated conversation from polluting context.

### 3.3 Closed Episode Summaries

Closed episodes (especially the immediately preceding one) get a **compact summary**
generated by Dream at close time, not by a separate consolidation pass:

```python
@dataclass
class EpisodeSummary:
    episode_id: str
    label: str
    summary: str             # 2-3 sentence Dream-generated
    decisions: list[str]
    constraints: list[str]
    open_loops: list[str]
    tone_notes: str          # NEW — preserves "用户调侃语气" / "情绪低落" etc.
    raw_preview: str         # First and last 3 messages verbatim — preserves tone
```

These are stored in `session.metadata["_episode_summaries"]` (FIFO, max 5).

**Loading rule:** Always load the immediately preceding episode's summary.
Load older episode summaries only if budget permits.

### 3.4 Topic Embedding Similarity for Reference Loading

When building `reference_blocks`, use the **active episode's topic embedding**
as the query instead of (or in addition to) the current user message:

```python
# Current: RetrievalFusion.retrieve(query=current_message)
# New:
query_vector = active_episode.topic_embedding or embed(current_message)
reference_blocks = RetrievalFusion.retrieve(query_vector=query_vector)
```

This ensures retrieved facts are relevant to the **ongoing topic**, not just
the latest utterance.

---

## 4. Agent-Driven Context Retrieval

### 4.1 Enhanced `session_search` Tool

The existing `session_search` tool is already capable. The enhancements:

**Add `episode` mode** to search:
```python
# Search within a specific episode
session_search(query="BDI", mode="episode", episode_id="...")

# Search all episodes that match
session_search(query="BDI", mode="episode", result_shape="episodes")
```

**Add `replay` mode** — the agent can request raw message replay:
```python
session_search(query="", mode="replay", episode_id="...")
# Returns raw messages from that episode, formatted for inclusion in next turn
```

**Add `context` tool** — a single-call context-expansion tool:
```python
episode_context(episode_id="...")
# Returns: raw messages + episode summary + tone notes + key decisions
```

### 4.2 Context Insufficiency Detection

When the agent detects it's missing context, it can:
1. Call `session_search(mode="replay")` to load raw history
2. The tool returns messages formatted as `<reference_context>` blocks
3. The agent can reference them in its next response

**System-level hint:** If the active episode is very long (>50 messages) and
the agent's response is generic, the system can inject an internal event:
```
[Internal Event] You have 80+ messages in the current episode (BDI design).
The most relevant context to your current answer may not be in the last 5 messages.
Use session_search(mode="replay") to review earlier discussion.
```

### 4.3 Token Budget Awareness

Both `session_search` and `episode_context` return a `token_estimate` field
so the agent can make informed decisions:

```json
{
  "episode_id": "...",
  "message_count": 34,
  "token_estimate": 4200,
  "raw_preview": "[first 3 messages...] ... [last 3 messages...]",
  "summary": "..."
}
```

---

## 5. Dream Integration

Dream currently processes facts from history. With episodes, it gains a new phase:

### 5.1 Episode Close Processing *(Phase 4 — implemented)*

When an episode closes, inline processing generates:
1. **Episode summary** — compact text preview (first/last user message)
2. **Tone notes** (Phase 4) — heuristic analysis: "inquisitive", "terse", "emphatic",
   "detailed / explanatory", "casual / playful", "neutral / conversational"
3. **Key verbatim quotes** (Phase 4) — longest, non-procedural user messages
4. **`llm_summary`** (Phase 4 — Dream Phase 0) — structured summary combining
   label, preview, tone, and key quotes, set asynchronously during Dream's cron cycle

These live in `session.metadata["_episode_summaries"]` and are rendered into the
`closed_episode_summaries` reference context block with tone and quotes. They also
feed into nearline memory's `EpisodeRecord` for long-term retention.

### 5.2 Topic Embedding Generation

At episode close (or periodically for long episodes), compute a lightweight
topic embedding:

```python
def compute_topic_embedding(messages: list[dict]) -> list[float]:
    """Simple bag-of-words TF-IDF over episode content."""
    # Keep it cheap and deterministic — no LLM call
    text = " ".join(m["content"] for m in messages if isinstance(m.get("content"), str))
    return tfidf_vector(text)  # or use the same embedder as RetrievalFusion
```

### 5.3 Dream Phase 0

Insert a new Dream phase before Phase 1 (fact proposal):
- Scan recently closed episodes
- Generate episode summaries (if not already done)
- Extract tone notes and verbatim quotes
- These become inputs to Phase 1 fact proposal

---

## Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Episode data model + backfill + persistence | ✅ Done |
| 2 | Episode-aware context loading + flag | ✅ Done |
| 3 | Topic boundary detection + /topic + auto-detect | ✅ Done |
| 4 | Dream integration + agent-driven retrieval | ✅ Done |
| 5 | Legacy path removal | ⏳ Not started |

## 6. File Changes Summary

| File | Change |
|------|--------|
| `session/manager.py` | Add `Episode` dataclass, `episodes` field to `Session`, `add_message` sets `episode_id`, `get_episode_history()` method, episode lifecycle methods |
| `agent/context.py` | New `build_active_episode_block()` method, changed assembly order in `build_messages()` |
| `agent/context_assembler.py` | Add `episode_raw_history` to `ASSEMBLY_ORDER`, include in `assemble()` |
| `agent/loop.py` | Episode boundary detection in message processing, `_save_episode_checkpoint()`, `_detect_topic_shift()` |
| `agent/autocompact.py` | Update `_archive()` to handle episode-aware trimming instead of raw tail-slicing |
| `agent/memory.py` / `Dream` | Add Phase 0: episode close processing, episode summary generation, tone notes |
| `agent/working_memory.py` | Add `current_episode_id` to working memory |
| `agent/topic_detection.py` | [NEW] Phase 3 — Jaccard similarity topic shift detection |
| `agent/tools/close_episode.py` | [NEW] Phase 3 — `CloseEpisodeTool` for agent-driven episode close |
| `agent/tools/episode_context.py` | [NEW] Phase 4 — `EpisodeContextTool` for full episode transcript retrieval |
| `command/builtin.py` | Phase 3 — `/topic` command handler + registration + help spec |
| `agent/agent_turn_pipeline.py` | Phase 3 — `_maybe_auto_detect_topic_shift()`; Phase 4 — `_build_context_insufficiency_hint()` |
| `agent/memory.py` | Phase 4 — Dream Phase 0 `_phase0_episode_summaries()`, optional `sessions` parameter |
| `config/schema.py` | Phase 2 — `enable_episode_context` field |
| `docs/episode-session-redesign.md` | This document |

### 6.1 No-Change Files

These subsystems are **not affected** by this redesign:
- **MessageBus / channels:** Episodes are a session-level concern; channels don't need to know
- **Tool registry:** Only `session_search` tool changes
- **Facts store:** Facts still derive from Dream, now with episode context
- **Nearline memory:** Already has `EpisodeRecord` — we backfill it from session episodes
- **Security / SSRF / sandbox:** Unrelated
- **WebUI:** Session list display could surface episode labels, but not required for MVP
- **Bridge / WhatsApp:** Session behavior is transparent to bridge clients

---

## 7. Migration Path

### Phase 1: Foundation (episode data model only)

1. Add `Episode` dataclass and `episodes` field to `Session`
2. On session load, create a single episode for all existing messages (backfill)
3. `add_message()` sets `episode_id` on each new message
4. All existing code still works — episodes are inert metadata

**Risk:** None. Episodes are append-only metadata with no behavioral changes.

### Phase 2: Episode-Aware Context Loading

1. `build_messages()` loads `active_episode_raw_history` instead of full `session.get_history()`
2. Add `build_active_episode_block()` to `ContextBuilder`
3. Update `ContextAssemblerV2.ASSEMBLY_ORDER`
4. Change `ContextBudgetManager.apply()` to prefer trimming reference_blocks over episode_raw_history

**Risk:** Medium. Changes context assembly order — needs careful testing.
Mitigation: feature flag `enable_episode_context` (default off in Phase 2).

### Phase 3: Topic Boundary Detection

1. Implement implicit topic detection (embedding similarity)
2. Implement `/topic` command
3. Agent-driven episode close via `close_episode()` tool
4. Add `_detect_topic_shift()` to `agent/loop.py`

**Risk:** Low-Medium. Topic detection is advisory — false positives just create a new episode (harmless).
False negatives mean a long single episode (same as no episodes — graceful degradation).

### Phase 4: Dream Integration + Agent-Driven Retrieval *(implemented)*

1. **Enhanced episode summaries**: `_analyze_episode_tone()` — heuristic tone
   classification (inquisitive, terse, emphatic, detailed, casual, neutral);
   `_extract_key_quotes()` — longest non-procedural user messages; both stored
   in `_episode_summaries` metadata.
2. **Dream Phase 0**: `Dream._phase0_episode_summaries()` — scans session files
   for closed episodes and generates `llm_summary` field combining label,
   preview, tone, and quotes. Runs during pre-flight before Phase 1.
3. **`episode_context` tool**: `EpisodeContextTool` (ContextAware) — retrieves
   full raw message history for a given episode ID, formatted as a readable
   transcript with timestamps.
4. **Context insufficiency hint**: `_build_context_insufficiency_hint()` — when
   active episode has 20+ messages and only a subset is in context, injects an
   internal event suggesting the agent use `episode_context` tool.
5. **Enhanced context rendering**: `build_closed_episode_summaries_block()` now
   includes tone notes and key quotes in the reference context block.

**Risk:** Low. Each feature independently disable-able via `enable_episode_context`.

### Phase 5: Legacy Path Removal

1. Remove `get_history()` call path entirely (replace with episode loading)
2. Remove `continuity_checkpoint` (replaced by episode metadata + summaries)
3. Simplify `AutoCompact` to be episode-aware (trim by entire episodes, not message tail)

**Risk:** High. Only after all Phases 1-4 are stable in production.

---

## 8. Key Design Decisions

### 8.1 Episodes Are NOT Nearline `EpisodeRecord`

The existing `EpisodeRecord` is a **long-term memory artifact** — a summarized,
frozen representation of a past conversation used for user profiling and identity.
Session episodes are **working units** — raw, mutable, and directly tied to message
indices in the session.

**Bridge at Phase 4:** When a session episode closes, Dream generates an
`EpisodeRecord` for nearline memory from it.

### 8.2 Topic Embedding Should Be Cheap

No LLM calls for topic embedding. Use a deterministic, local method:
- TF-IDF on episode text (cheap, deterministic, no dependencies)
- Or reuse the same text embedder that `RetrievalFusion` uses
- The goal is not perfect topic detection — it's to detect *obvious* topic shifts

### 8.3 Raw Message Loading Is Budget-Bounded

The `replay_token_budget()` mechanism already exists. The change is in **priority**:
episode raw messages get the first claim on budget, everything else gets what's left.

```python
budget = context_window - max_completion - 1024
episode_tokens = estimate_episode_tokens(active_episode)
episode_raw = load_episode_messages(active_episode, max_tokens=budget)
remaining = budget - actual_tokens(episode_raw)
reference_blocks = load_reference_blocks(max_tokens=remaining)
```

### 8.4 Topic Shift Detection Threshold

Start conservative: only auto-detect very clear shifts (>0.7 cosine distance
between rolling window embeddings). For everything else, rely on explicit signals
(user says "换个话题", `/topic` command, agent explicit close).

### 8.5 Episode Boundaries Are NOT Locked

Messages in a session can be reassigned to different episodes if topic detection
retrospectively determines the boundary was wrong. Episode metadata is advisory,
not canonical. The `episode_id` on messages can be updated via a background
reconciliation pass.

---

## 9. Questions for Implementation

1. **Topic detection algorithm:** What embedding method? TF-IDF is cheapest but
   least accurate. Reuse `RetrievalFusion`'s embedder for consistency? (Suggestion:
   start with a simple single-call to the same embedder, not TF-IDF — consistency
   matters more than speed at this scale.)

2. **Episode summary generation timing:** At close time inline (blocking turn)
   or defer to Dream? (Suggestion: inline for the 2-3 sentence summary, Dream
   for full processing. The inline pass should take <500ms with a local model
   call or template-based extraction.)

3. **`/topic` command already exists?** Check if there's a command handler that
   could be reused.

4. **Backfill strategy for existing sessions:** When Phase 1 ships, existing
   sessions have no episode metadata. Create one episode wrapping all messages?
   Or create episodes retroactively via Dream? (Suggestion: one catch-all episode,
   then let Dream split it over time.)

5. **WebUI impact:** Should the session list show episode labels? Should there be
   an "episode timeline" view? (Out of scope for MVP, but worth designing for.)

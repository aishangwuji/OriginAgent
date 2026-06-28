"""Lightweight topic-shift detection for episode management.

Uses Jaccard similarity on content words — no external dependencies,
no LLM calls. Fast enough to run on every user message.
"""

from __future__ import annotations

import re

_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "nor", "not", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "no", "only", "own", "same", "this", "that", "these", "those", "i",
    "me", "my", "myself", "we", "our", "ours", "you", "your", "yours",
    "he", "him", "his", "she", "her", "hers", "it", "its", "they", "them",
    "their", "what", "which", "who", "whom", "how", "when", "where", "why",
    "about", "up", "out", "if", "than", "just", "because", "very",
    "also", "well", "here", "there", "then", "now", "get", "got",
    "like", "make", "made", "going", "go", "goes", "went", "come",
    "came", "take", "took", "use", "used", "say", "said", "tell",
    "told", "ask", "asked", "know", "knew", "think", "thought",
    "want", "wanted", "need", "needed", "let", "really", "actually",
    "yes", "no", "ok", "okay", "sure", "right", "well", "oh", "ah",
})

_ZH_STOPWORDS: frozenset[str] = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "为什么", "因为", "所以", "但是", "可以", "这个", "那个",
    "时候", "知道", "觉得", "如果", "虽然", "然后", "还是", "已经", "把",
    "被", "让", "给", "对", "从", "跟", "比", "为", "与",
})

def content_words(text: str) -> frozenset[str]:
    """Extract meaningful content words from text, filtering stopwords.

    Handles both English and Chinese text. English words shorter than 2
    characters are excluded as noise. CJK characters are split individually,
    since Chinese text has no word-delimiting spaces.
    """
    words: set[str] = set()

    # Process Latin / ASCII tokens (split on punctuation, lowercase).
    cleaned = re.sub(r"[^\w\s]", " ", text)
    for token in cleaned.split():
        token = token.strip().lower()
        if not token:
            continue
        if all(ord(c) < 128 for c in token):
            # ASCII word — filter stopwords and very short tokens.
            if token not in _STOPWORDS and len(token) > 2:
                words.add(token)

    # Process CJK characters individually.
    for ch in text:
        if "一" <= ch <= "鿿" and ch not in _ZH_STOPWORDS:
            words.add(ch)

    return frozenset(words)


def jaccard_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two sets of content words.

    Returns 1.0 if either set is empty (can't detect shift with no content).
    """
    if not a or not b:
        return 1.0
    union = len(a | b)
    if union == 0:
        return 1.0
    return len(a & b) / union


def detect_topic_shift(
    current_message: str,
    recent_messages: list[str],
    threshold: float = 0.15,
) -> bool:
    """Detect if *current_message* represents a topic shift from *recent_messages*.

    Args:
        current_message: The user's new message.
        recent_messages: Previous user messages from the active episode
            (at least 1, ideally 2-5).
        threshold: Jaccard similarity below this value is considered a shift.
            Default 0.15 works well for natural conversation.

    Returns:
        True if the topic appears to have shifted.
    """
    current = content_words(current_message)
    if not current:
        return False

    combined: set[str] = set()
    for msg in recent_messages:
        combined |= set(content_words(msg))

    if not combined:
        return False

    sim = jaccard_similarity(current, frozenset(combined))
    return sim < threshold


def rolling_topic_embedding(messages: list[str]) -> frozenset[str]:
    """Build a combined content-word set from a list of messages.

    This serves as a lightweight "topic fingerprint" for an episode.
    """
    combined: set[str] = set()
    for msg in messages:
        combined |= set(content_words(msg))
    return frozenset(combined)

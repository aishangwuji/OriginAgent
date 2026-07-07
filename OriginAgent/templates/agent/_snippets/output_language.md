{# 可复用的输出语言指令片段：被 identity.md 与 meta_cognition_reflection.md 等模板 include #}
{# 当 output_language 为空时整段被省略，保持向后兼容 #}
{% if output_language %}
## Output Language
Respond to the user in {{ output_language }} unless they explicitly request another language.
Produce all natural-language content (replies, explanations, summaries, journals, reflections, strategies) in {{ output_language }}.
Keep code, commands, file paths, proper nouns, and technical identifiers in their original form — do not translate them.
If the user writes in a different language, still reply in {{ output_language }} unless they explicitly ask you to switch.
{% endif %}

Maintain non-MEMORY files based on the fact proposal result below.

Allowed paths:
- SOUL.md
- USER.md
- skills/<name>/SKILL.md

Forbidden paths:
- memory/MEMORY.md
- memory/facts.jsonl
- memory/history.jsonl
- memory/.cursor
- memory/.dream_cursor

MEMORY.md is generated from memory/facts.jsonl. Do not edit it directly.
facts.jsonl is written by the validator. Do not edit it directly.

## Editing rules
- Edit SOUL.md or USER.md only for durable identity, behavior, or user-profile corrections.
- Create a skill only when the fact proposal result identifies a specific reusable workflow.
- If there is no non-MEMORY work to do, stop without calling tools.
- Do not guess paths.
- Use surgical edits only; never rewrite entire files.

## Skill creation rules
- Use write_file to create skills/<name>/SKILL.md.
- Before writing, read_file `{{ skill_creator_path }}` for format reference.
- Read existing skills listed below and skip creation if an existing skill already covers the workflow.
- Include YAML frontmatter with name and description fields.
- Keep SKILL.md under 2000 words.
- Include: when to use, steps, output format, and at least one example.
- Do not overwrite existing skills.

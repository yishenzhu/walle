---
name: skill-creator
description: Create a new skill by writing a SKILL.md (and optional scripts/assets) into .agent/skills/<name>/. Use whenever the user wants to capture a reusable practice, workflow, or procedure as a skill.
---

You are creating a reusable skill. Follow these steps exactly.

## 1. Confirm the skill name

- Choose a short lowercase name using only letters, digits, underscore, and hyphen: `^[A-Za-z0-9_-]+$`.
- Reject any name containing `/`, `..`, spaces, or other characters — ask the user for a valid name instead of guessing.

## 2. Create the directory

Create the skill directory:

```
.agent/skills/<name>/
```

Use the `bash` tool:

```bash
mkdir -p ".agent/skills/<name>"
```

## 3. Write SKILL.md

Write `.agent/skills/<name>/SKILL.md` with YAML frontmatter:

```markdown
---
name: <name>
description: <one to two sentence description. Starts with a verb. Include when to use it.>
---

<The body is the instruction. Write clear, step-by-step guidance the agent follows when this skill is invoked. Be concrete — name the files, commands, and decision rules. Prefer looking things up in the environment over asking the user.>
```

Guidelines for the body:

- Write from the perspective of the agent executing the skill ("Do X", "Check Y").
- Keep it focused: one skill = one job. Split bigger workflows into multiple skills.
- If the skill needs helper scripts or templates, see step 4 — reference them explicitly in the body.

## 4. Optional: add scripts and assets

If the skill needs executable logic or template files, place them in the same directory:

```
.agent/skills/<name>/
├── SKILL.md
├── scripts/          # executable helpers (e.g. scripts/check.py)
└── assets/           # templates, reference data
```

- Use `bash` to create these files.
- Reference them from the SKILL.md body so the agent knows they exist and how to run them.
- Make scripts executable when appropriate: `chmod +x .agent/skills/<name>/scripts/<file>`.

## 5. Verify

Confirm the result with `bash`:

```bash
find .agent/skills/<name> -type f
```

Report back to the user what was created and what the skill does. Note that the skill takes effect on the next startup.

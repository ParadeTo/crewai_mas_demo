---
name: skill-creator
description: >
  Create new skills and save them to the project's shared skills directory.
  Use this skill when users want to turn a workflow, SOP, or repeatable
  multi-step process into a permanent reusable skill that future sessions
  can invoke reliably.

  Activate when: user says "保存为技能" / "create a skill for..." /
  "把这个流程存下来" / "以后都这样做" / "make this repeatable" / "turn this
  into a skill". Also activate proactively when a complex multi-step workflow
  is described in conversation and standardized, reliable execution would
  clearly benefit the user.

  Do NOT activate for: simple facts or preferences (use memory-save),
  one-time tasks, or things that don't need to run consistently every time.
allowed-tools:
  - Read
  - Write
---

# Skill Creator

A skill for capturing workflows and turning them into reusable skills.

The process: understand what the user wants → draft a quality SKILL.md → save it to the sandbox → register it so future sessions can use it.

## Creating a skill

### Capture Intent

Start by understanding the user's intent. The current conversation might already contain a workflow the user wants to capture (e.g., they say "turn this into a skill"). If so, extract answers from the conversation history first — the tools used, the sequence of steps, corrections the user made, input/output formats observed. The user may need to fill the gaps, and should confirm before proceeding.

1. What should this skill enable the assistant to do?
2. When should this skill trigger? (what user phrases/contexts)
3. What's the expected output format?

### Interview and Research

Proactively ask questions about edge cases, input/output formats, success criteria, and dependencies. Come prepared with context to reduce burden on the user.

### Write the SKILL.md

Based on the user interview, fill in these components:

- **name**: Skill identifier (kebab-case, e.g. `analyze-hk-stock`)
- **description**: When to trigger, what it does. This is the primary triggering mechanism — include both what the skill does AND specific contexts for when to use it. Note: Claude has a tendency to "undertrigger" skills — to not use them when they'd be useful. To combat this, make the description a little "pushy". Instead of "A skill for analyzing stocks", write "Use this skill when the user asks to analyze Hong Kong stocks, research HK companies, or evaluate HK investment opportunities. Activate whenever 港股 or HK stock analysis is needed."
- **allowed-tools**: Minimal list — only tools actually needed
- **the skill body**

#### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (name, description required)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Files used in output (templates, icons, fonts)
```

#### Progressive Disclosure

Skills use a three-level loading system:
1. **Metadata** (name + description) - Always in context (~100 words)
2. **SKILL.md body** - In context whenever skill triggers (<500 lines ideal)
3. **Bundled resources** - As needed (unlimited)

Keep SKILL.md under 500 lines. If approaching the limit, add a layer of hierarchy with pointers to reference files.

#### Writing Style

Explain to the model *why* things are important rather than stacking MUST/NEVER. Use theory of mind — make the skill general, not narrow to specific examples. If you find yourself writing ALWAYS or NEVER in all caps, that's a yellow flag: reframe and explain the reasoning instead. A model that understands *why* will handle edge cases correctly; one that just follows rules will fail on anything unexpected.

**Examples pattern:**
```markdown
## Commit message format
**Example:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

**Output format pattern:**
```markdown
## Report structure
Use this template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

---

## Saving to the sandbox (project-specific)

This skill runs as a Sub-Crew inside AIO-Sandbox. All file operations go through MCP tools — do not write to the local filesystem directly.

### Step 1: Check for existing skill

Read `/mnt/skills/load_skills.yaml` to check if a skill with the same name already exists.

If it exists: ask the user — overwrite / create as `-v2` / cancel.
Reason: silent overwrite would destroy the user's existing SOP with no way to recover.

### Step 2: Write SKILL.md

```
mkdir /mnt/skills/{skill-name}/
write /mnt/skills/{skill-name}/SKILL.md  ← the drafted content
```

### Step 3: Register in load_skills.yaml

Read the current `/mnt/skills/load_skills.yaml`, then append:

```yaml
  - name: {skill-name}
    path: ./{skill-name}
    type: task        # spawns Sub-Crew in AIO-Sandbox
    enabled: true
```

Write the updated file back.

### Step 4: Verify

Read `/mnt/skills/{skill-name}/SKILL.md` and `/mnt/skills/load_skills.yaml` to confirm both files are written correctly and the YAML is valid.

Reason: file writes via MCP don't throw exceptions on failure — read-back verification is the only reliable confirmation.

### Step 5: Return result

```json
{
  "errcode": 0,
  "errmsg": "success",
  "skill_name": "{skill-name}",
  "path": "/mnt/skills/{skill-name}/SKILL.md",
  "trigger": "one-line summary of when this skill activates"
}
```

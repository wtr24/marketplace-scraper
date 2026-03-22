# Scraper Skills System — Design Spec
**Date:** 2026-03-22
**Project:** marketplace-scraper (Vinted · eBay · Depop)
**Status:** Approved

---

## Overview

4 project-local Claude Code skills stored in `.claude/commands/` that wire together into an orchestrator-first workflow. Every task flows through the orchestrator, which refines the prompt, decomposes the task, manages agent teams, and auto-triggers supporting skills at the right moments.

---

## Skills

| Skill | File | Trigger |
|---|---|---|
| `scraper-orchestrator` | `.claude/commands/scraper-orchestrator.md` | Manual — every task |
| `scraper-research` | `.claude/commands/scraper-research.md` | Auto (orchestrator) or manual |
| `scraper-memory` | `.claude/commands/scraper-memory.md` | Auto (on phase complete) |
| `context-optimizer` | `.claude/commands/context-optimizer.md` | Auto (context threshold) |

---

## Architecture

```
User raw input
      │
      ▼
scraper-orchestrator
  ├── Step 1: Prompt Optimizer
  │     - Infers intent from context (git history, CLAUDE.md, memory)
  │     - Rewrites as precise task brief
  │     - Shows rewrite → user approves or corrects
  ├── Step 2: Task Decomposition
  │     - Breaks into atomic sub-tasks
  │     - Scores each: complexity / unknowns / parallelisable?
  │     - Decides team composition
  ├── Step 3: Context Health Check
  │     - Estimates current context load
  │     - If degraded → triggers context-optimizer first
  ├── Step 4: Spawn Agent Team
  │     - Researcher agents (if unknowns) ← scraper-research
  │     - Planner agent ← writes implementation plan
  │     - Executor agent(s) ← parallel where independent
  │     - Each agent: isolated context, only relevant files
  └── Step 5: Synthesise + Memory
        - Combines agent outputs
        - Triggers scraper-memory on completion
```

---

## Skill 1: `scraper-orchestrator`

**Role:** Master entry point. Takes raw user input, optimises it, decomposes it, builds an agent team, coordinates execution, synthesises output, triggers memory update.

**Prompt Optimizer behaviour:**
- Reads `CLAUDE.md` global spec, recent git log, and memory files before rewriting
- Fills in ambiguity using project context (e.g. "proxy thing" → identifies current proxy implementation)
- Shows rewrite explicitly, waits for approval before proceeding
- If user corrects, re-refines once more

**Task Decomposition algorithm:**
- Each sub-task must be: atomic, independently verifiable, assignable to one agent
- Score per sub-task: `Complexity (1-3) × Unknown Factor (1-2) × Parallelisable (bool)`
- High-unknown sub-tasks → spawn researcher first, block executor until complete
- Parallelisable sub-tasks → spawn simultaneously via Agent tool

**Context Health Check:**
- After every agent synthesis, estimate token usage
- If > 60% of context window → trigger `context-optimizer` before next spawn
- If > 85% → force checkpoint and recommend fresh session

**Agent isolation:**
- Each agent receives: sub-task description, relevant file paths only, no session history
- Agents communicate results back via structured output, not conversation

---

## Skill 2: `scraper-research`

**Role:** Deep research into anti-bot evasion for Vinted/eBay/Depop. Surfaces techniques, packages, builds a scored algorithm, generates test plan.

**Parallel research agents (Step 1):**
- Agent A — Detection analysis: What each target site uses (TLS fingerprint, JS challenges, behavioural analysis, IP reputation scoring, CAPTCHA type)
- Agent B — Evasion techniques: Current best-in-class methods + Python/Node packages (playwright-stealth, curl-impersonate, fingerprint-suite, etc.)
- Agent C — Proxy landscape: Residential vs datacenter vs rotating, free vs paid, survival rate per site

**Gap analysis (Step 2):**
- Compares research findings against current repo implementation
- Outputs: "You have X, missing Y, Z is outdated"
- Prioritised by impact

**Evasion Algorithm (Step 3):**
```
VintedEvasionScore = (ProxyScore × 0.35) + (FingerprintScore × 0.35)
                   + (BehaviourScore × 0.20) + (TimingScore × 0.10)
```
- Produces ranked list of changes with expected impact per site
- Same formula applied per site with site-specific weights

**Plugin surfacing (Step 4):**
- For each recommended technique → best package, version, install command, known caveats
- Flags packages that conflict with existing requirements.txt

**Test plan (Step 5):**
- Per-site verification test: success rate, time-to-block, listings captured
- Regression test: ensure existing working scrapers not broken by changes

---

## Skill 3: `scraper-memory`

**Role:** Updates `CLAUDE.md` after significant task completion. Two-layer structure.

### Layer 1 — Global Spec (top of CLAUDE.md, living document)

Updated only when: strategy changes, new site added, major problem resolved or opened.

```markdown
## Project Goal
[What this scraper is trying to achieve — success metrics]

## Current Strategy
[Primary engine per site, anti-detection approach]

## Sites Status
| Site | Engine | Status | Last Issue |

## Open Problems
- [ ] item
```

### Layer 2 — Session Log (append-only)

Appended after every significant task.

```markdown
## Session: YYYY-MM-DD — [task name]

### What was done
### Outcome (per site/engine with ✅/❌/⚠️)
### What worked
### What to improve
### Watch out for
### Next
```

**Behaviour:**
- Reads git diff + session context to draft entries
- Never rewrites existing history — append only
- Shows proposed changes, waits for approval before writing
- Updates Layer 1 only if task changed strategy or site status

---

## Skill 4: `context-optimizer`

**Role:** Detects and fixes context rot. Keeps context high-signal throughout long sessions.

**Relevance Decay Scoring algorithm:**
```
Score = (Recency × 0.4) + (Relevance × 0.4) + (Uniqueness × 0.2)

Recency:    completed task = 0.1  |  active task = 1.0
Relevance:  directly related to current task = 1.0  |  tangential = 0.3
Uniqueness: already summarised elsewhere = 0.1  |  novel info = 1.0

Score < 0.3  → prune (replace with 1-line summary)
Score 0.3–0.6 → compress (key facts only)
Score > 0.6  → keep as-is
```

**Output:**
1. Context health report — score breakdown, what's rotting, estimated token usage
2. Compressed checkpoint — all completed work in compact handoff block
3. Reset recommendation if health < 40%

**Auto-trigger thresholds (set by orchestrator):**
- > 60% context load → run optimizer
- > 85% context load → force checkpoint, recommend fresh session

---

## File Locations

```
C:\scraper\
├── .claude\
│   └── commands\
│       ├── scraper-orchestrator.md
│       ├── scraper-research.md
│       ├── scraper-memory.md
│       └── context-optimizer.md
├── CLAUDE.md                    ← created/managed by scraper-memory
└── docs\
    └── superpowers\
        └── specs\
            └── 2026-03-22-scraper-skills-design.md
```

---

## Success Criteria

- [ ] `/scraper-orchestrator` takes raw input, shows refined prompt, decomposes task, spawns agents, synthesises result
- [ ] `/scraper-research` produces gap analysis + ranked evasion algorithm + plugin list for this repo
- [ ] `/scraper-memory` writes structured CLAUDE.md entries with two-layer structure after each task
- [ ] `/context-optimizer` scores context health and produces compressed checkpoint
- [ ] All 4 skills work standalone AND wired through orchestrator

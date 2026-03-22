# Scraper Skills System — Design Spec
**Date:** 2026-03-22
**Project:** marketplace-scraper (Vinted · eBay · Depop)
**Status:** Approved

---

## Overview

4 project-local Claude Code skills stored in `.claude/commands/` that wire together into an orchestrator-first workflow. Every task flows through the orchestrator, which refines the prompt, decomposes the task, manages agent teams, and directs supporting skills at the right moments.

**Important:** Claude Code skills are prompt files — they cannot programmatically invoke each other. "Auto-triggering" means the orchestrator's instructions explicitly direct the model to run the named skill as the next step in its output. There is no hidden runtime mechanism; the orchestrator skill text instructs the model to say "now run /context-optimizer" or "now run /scraper-memory" at the appropriate point.

---

## Skills

| Skill | File | Trigger | Mode |
|---|---|---|---|
| `scraper-orchestrator` | `.claude/commands/scraper-orchestrator.md` | Manual — every task | Interactive |
| `scraper-research` | `.claude/commands/scraper-research.md` | Directed by orchestrator, or manual | Non-interactive when orchestrated |
| `scraper-memory` | `.claude/commands/scraper-memory.md` | Directed by orchestrator, or manual | Non-interactive when orchestrated |
| `context-optimizer` | `.claude/commands/context-optimizer.md` | Directed by orchestrator, or manual | Non-interactive when orchestrated |

**Standalone vs orchestrated mode:**
- Standalone (manual invocation): each skill shows proposed output and waits for user approval before writing anything
- Orchestrated (directed by orchestrator): skills skip the approval gate and execute directly, since the orchestrator step already had the user's intent confirmed at the prompt-refinement stage

---

## Architecture

```
User raw input
      │
      ▼
scraper-orchestrator
  ├── Step 1: Prompt Optimizer
  │     - Reads CLAUDE.md global spec, recent git log, memory files
  │     - Rewrites as precise task brief
  │     - Shows rewrite → user approves or corrects (only approval gate)
  ├── Step 2: Task Decomposition
  │     - Breaks into atomic sub-tasks
  │     - Scores each: complexity / unknowns / parallelisable?
  │     - Decides team composition
  ├── Step 3: Context Health Check
  │     - Estimates context load (see heuristic below)
  │     - If degraded → directs model to run /context-optimizer
  ├── Step 4: Spawn Agent Team
  │     - If unknowns → directs model to run /scraper-research first
  │     - Planner agent ← writes implementation plan
  │     - Executor agent(s) ← parallel where independent
  │     - Each agent: isolated context, only relevant files
  └── Step 5: Synthesise + Memory
        - Combines agent outputs into coherent result
        - Directs model to run /scraper-memory
```

---

## Skill 1: `scraper-orchestrator`

**Role:** Master entry point. Takes raw user input, optimises it, decomposes it, builds an agent team, coordinates execution, synthesises output, directs memory update.

### Step 1: Prompt Optimizer

- Reads `CLAUDE.md` global spec, `git log --oneline -10`, and memory files before rewriting
- Fills in ambiguity using project context (e.g. "proxy thing" → identifies current proxy implementation from scheduler.py and proxy pool)
- Shows rewrite explicitly — this is the **only user approval gate** in the orchestrator flow
- If user corrects, re-refines once more then proceeds

### Step 2: Task Decomposition

- Each sub-task must be: atomic, independently verifiable, assignable to one agent
- Score per sub-task: `Complexity (1-3) × Unknown Factor (1-2)`
- High-unknown sub-tasks (score ≥ 4) → direct /scraper-research first, block executor until complete
- Independent sub-tasks → spawn via Agent tool simultaneously

### Step 3: Context Health Check

**Context load heuristic** (no native API available — estimated from observable signals):
```
EstimatedLoad =
  (number of files read this session × 200 tokens avg)
  + (number of tool calls × 150 tokens avg)
  + (number of assistant messages × 300 tokens avg)
  / 200,000 (Claude Sonnet context window)

If EstimatedLoad > 0.60 → direct model to run /context-optimizer
If EstimatedLoad > 0.85 → force checkpoint, output recommendation to start fresh session
```
This is a conservative estimate. When in doubt, err toward triggering the optimizer.

### Step 4: Agent Isolation

- Each agent receives: sub-task description + explicit list of relevant file paths + no session history
- Agents return structured output (status, findings, files changed, next steps)
- Orchestrator synthesises all outputs before proceeding to Step 5

### Step 5: Directed Memory Update

At completion, orchestrator outputs: `"Task complete. Running /scraper-memory to log outcomes."`
The model then follows the scraper-memory skill in non-interactive mode.

---

## Skill 2: `scraper-research`

**Role:** Deep research into anti-bot evasion for Vinted/eBay/Depop. Surfaces techniques, packages, builds a scored algorithm, generates test plan.

### Step 1: Parallel Research Agents

Three agents spawned simultaneously:
- **Agent A** — Detection analysis: What each target site uses (TLS fingerprint, JS challenges, behavioural analysis, IP reputation scoring, CAPTCHA type)
- **Agent B** — Evasion techniques: Current best-in-class methods + Python/Node packages (playwright-stealth, curl-impersonate, fingerprint-suite, etc.)
- **Agent C** — Proxy landscape: Residential vs datacenter vs rotating, free vs paid, survival rate per site

### Step 2: Gap Analysis

- Compares Agent A+B findings against current repo implementation (reads scrapers/, sites/, scheduler.py)
- Outputs table: `| Technique | Have it? | Quality | Priority to improve |`
- Prioritised by estimated impact on ban rate

### Step 3: Evasion Algorithm

Per-site scoring formula with weights derived from Agent A's detection findings for that site:

```
EvasionScore(site) = (ProxyScore × W_proxy)
                   + (FingerprintScore × W_fp)
                   + (BehaviourScore × W_beh)
                   + (TimingScore × W_timing)

Where W_proxy + W_fp + W_beh + W_timing = 1.0

Weights are derived per site based on what Agent A found:
- If site primarily uses IP reputation → W_proxy = 0.5, others share remaining 0.5
- If site primarily uses TLS/browser fingerprint → W_fp = 0.5, others share 0.5
- Default fallback weights: W_proxy=0.35, W_fp=0.35, W_beh=0.20, W_timing=0.10
```

Produces ranked list of changes with expected score improvement per site.

### Step 4: Plugin Surfacing

For each recommended technique:
- Best package name, version, pip/npm install command
- Known caveats (maintenance status, platform compatibility)
- Flags any conflict with existing `requirements.txt`

### Step 5: Test Plan

Per-site verification: success rate target, time-to-block baseline, listings captured metric.
Regression check: confirm existing passing scrapers still work after changes.

---

## Skill 3: `scraper-memory`

**Role:** Updates `CLAUDE.md` after task completion. Two-layer structure.

### What counts as a "significant task"

Layer 2 entry is written when ANY of:
- A scraper bug was fixed or a new feature was added (git diff is non-empty)
- A scrape was run and produced measurable results (success or failure)
- A new technique, package, or approach was tested

Layer 1 (global spec) is updated when ANY of:
- Primary engine for a site changed
- A new site was added or removed
- A problem was resolved that was in Open Problems
- A new persistent problem was discovered

### Layer 1 — Global Spec (top of CLAUDE.md)

```markdown
## Project Goal
[What this scraper is trying to achieve — success metrics]

## Current Strategy
[Primary engine per site, anti-detection approach]

## Sites Status
| Site | Engine | Status | Last Issue |
|------|--------|--------|------------|

## Open Problems
- [ ] item
```

### Layer 2 — Session Log (append-only below Layer 1)

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
- Reads `git diff HEAD~1`, recent errors from session context, scraper_results if accessible
- In standalone mode: shows proposed CLAUDE.md changes, waits for approval before writing
- In orchestrated mode: writes directly without approval gate
- Never rewrites existing Layer 2 history — append only

---

## Skill 4: `context-optimizer`

**Role:** Detects and fixes context rot. Keeps context high-signal throughout long sessions.

### Relevance Decay Scoring

```
Score = (Recency × 0.4) + (Relevance × 0.4) + (Uniqueness × 0.2)

Recency:    completed task/topic = 0.1  |  active task = 1.0
Relevance:  directly related to current task = 1.0  |  tangential = 0.3  |  unrelated = 0.0
Uniqueness: already summarised or repeated elsewhere = 0.1  |  novel info = 1.0

Score < 0.3  → prune: replace with single summary line
Score 0.3–0.6 → compress: keep key facts, discard rationale
Score > 0.6  → keep as-is
```

### Output

1. **Context health report** — estimated load %, score breakdown per topic area, what's rotting
2. **Compressed checkpoint** — all completed work condensed into a compact handoff block (target: <500 tokens)
3. **Reset recommendation** — if estimated load > 85%, suggests starting fresh session carrying only the checkpoint

### Trigger thresholds (directed by orchestrator)

- Estimated load > 60% → run optimizer, continue in current session
- Estimated load > 85% → force checkpoint output, recommend fresh session start

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

**Individual skills:**
- [ ] `/scraper-orchestrator` takes raw input, shows refined prompt, decomposes task, spawns agents, synthesises result, and directs /scraper-memory at completion
- [ ] `/scraper-research` produces: gap analysis table, per-site evasion score with derived weights, ranked change list, plugin list with install commands
- [ ] `/scraper-memory` writes two-layer CLAUDE.md: Layer 1 updated only on strategy change, Layer 2 appended after each significant task
- [ ] `/context-optimizer` outputs: health report with estimated load %, compressed checkpoint under 500 tokens, reset recommendation when load > 85%

**Inter-skill wiring (orchestrator-directed flow):**
- [ ] When orchestrator encounters high-unknown sub-task, it directs /scraper-research before spawning executors
- [ ] When orchestrator estimated context load exceeds 60%, it directs /context-optimizer before next agent spawn
- [ ] When orchestrator completes final synthesis, it directs /scraper-memory in non-interactive mode
- [ ] All 4 skills work correctly in both standalone (interactive) and orchestrated (non-interactive) modes

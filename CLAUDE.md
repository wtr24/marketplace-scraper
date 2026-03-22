# Marketplace Scraper — Project Memory

## Project Goal
Build the most reliable, undetectable marketplace scraper for Vinted/eBay/Depop.
Success = zero ban rate, <5s per scrape, 99% listing capture rate, Discord alerts only for vintage Patagonia Synchilla/Snap-T fleeces the user actually wants.

## Current Strategy
- Primary: Playwright + free proxy rotation (ProxyPool auto-fetches from 4 sources)
- eBay: BeautifulSoup (static)
- Anti-detection: UA rotation, TLS spoofing, random delays, stealth mode, __NEXT_DATA__ extraction
- Alerts: Discord webhook → keyword filter → (classifier coming) → notify

## Sites Status
| Site   | Engine     | Status     | Last Issue                        |
|--------|------------|------------|-----------------------------------|
| Vinted | Playwright | ⚠️ flaky   | IP bans — proxy pool added        |
| eBay   | BS4        | ✅ stable  | —                                 |
| Depop  | Playwright | ⚠️ fragile | Selector rot                      |

## Open Problems
- [ ] Depop selector rot (depop.py)
- [ ] Proxy startup latency (~40s validation on boot)
- [ ] Vinted still occasionally rate-limited despite proxies
- [ ] Fleece classifier not yet built — Discord still keyword-only

---

## ⚡ NEXT SESSION — DO THESE IN ORDER

### 1. Build the 4 project skills (specs approved, no plan needed — just build)
Spec: `docs/superpowers/specs/2026-03-22-scraper-skills-design.md`
Create these 4 files in `.claude/commands/`:
- `scraper-orchestrator.md` — master prompt optimizer + agent team router
- `scraper-memory.md` — updates this CLAUDE.md after tasks
- `context-optimizer.md` — relevance decay scoring, context pruning
- `scraper-research.md` — anti-bot research + plugin surfacing

### 2. Execute the fleece classifier plan
Plan: `docs/superpowers/plans/2026-03-22-fleece-classifier.md`
Run with: `superpowers:subagent-driven-development`
9 tasks: scaffold → seed scripts → labeller API → labeller UI → inference module → scheduler integration → training script

### 3. Label fleeces (after classifier is built)
Open `http://192.168.0.18:3003/labeller`
Swipe 568 Synchilla/Snap-T listings — J = don't want, L = want, S = skip
Need ~250 WANT labels minimum before training

### 4. Train + deploy model (after labelling)
```bash
pip install -r classifier/requirements-training.txt
python -m classifier.train
# if val_acc >= 80%:
git add classifier/fleece_classifier.onnx && git commit -m "model: fleece classifier v1" && git push
```
Watchtower auto-deploys to NAS.

---

## Session Log

### Session: 2026-03-22 — Infrastructure + Classifier Design

**What was done:**
- Fixed Discord webhook avatar (replaced DJ placeholder with Patagonia logo, pushed to main)
- Designed + spec'd 4-skill orchestrator system (scraper-orchestrator, scraper-memory, context-optimizer, scraper-research)
- Designed + spec'd fleece image classifier (EfficientNet-B0, Tinder labeller UI, ONNX inference)
- Wrote full implementation plan for classifier (9 tasks, TDD)
- Built agent progress bar plugin (SubagentStart/Stop hooks → statusline)

**Outcome:**
- ✅ Discord: Patagonia logo deploying via Watchtower
- ✅ Specs committed: `docs/superpowers/specs/`
- ✅ Plan committed: `docs/superpowers/plans/`
- ✅ Progress bar: `~/.claude/hooks/agent-progress-tracker.js` + statusline updated
- ⏳ Skills: designed but not built yet
- ⏳ Classifier: planned but not implemented yet

**What worked:**
- Parallel agents (3 at once) for archive scrape + PyTorch research + DB check — fast and clean
- Spec review loop caught 2 critical bugs before implementation (async/gitignore)
- DB already has 568 Synchilla listings with 100% image URL coverage — no cold start needed

**Watch out for:**
- `data/` is gitignored globally — classifier images won't accidentally get committed
- `classifier/fleece_classifier.onnx` must NOT be gitignored — it's the deployment artefact
- `train.py` uses `os.environ.get("TEMP")` for checkpoint (Windows-safe)
- ONNX output node name is `"logits"` — must match export and inference

**Next:** Build skills → Execute classifier plan → Label → Train → Deploy

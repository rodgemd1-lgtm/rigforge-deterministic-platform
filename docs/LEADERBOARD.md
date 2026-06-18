# RIGForge — False-Done-Caught Leaderboard

*How well does each verification strategy catch an AI agent that lies about "done"?*

Scored against the seeded, offline forgery suite (12 forged claims across
tampered-artifact, forged-signature, swapped-artifact, dropped-gate, unsigned-tamper, and
skipped-spec-criterion classes). Every number is computed from real verify verdicts — reproduce
it in one command:

```bash
rigforge benchmark --leaderboard
```

| Verification strategy | False-done-caught rate | Caught / total |
|---|---|---|
| naive integrity | **0%** | 0/12 |
| signed (RIGForge core) | **67%** | 8/12 |
| spec-bound (RIGForge) | **100%** | 12/12 |

**Read it top to bottom:**

- **naive integrity** — only checks the packet's self-hash. An attacker who tampers an artifact
  and re-forges the hash sails right through. This is what "the build passed" usually means, and
  it catches **nothing**.
- **signed (RIGForge core)** — adds an HMAC signature bound to the original hash. Now tampering,
  forged signatures, swapped artifacts, and dropped gates are all caught. It still accepts a
  validly-signed claim that simply *skipped a required criterion*.
- **spec-bound (RIGForge)** — also binds the spec's acceptance criteria. A claim that's perfectly
  signed but didn't do the required work is **rejected**. This is the layer no observability or
  eval tool has.

Honest by construction: `honest wrongly blocked` is **0** for every strategy — none of these
catch rates come at the cost of false alarms on real work.

> Want your tool / approach on the board? Open a PR adding a strategy to
> `rigforge/leaderboard.py` — it must score against the same suite, from real verdicts.

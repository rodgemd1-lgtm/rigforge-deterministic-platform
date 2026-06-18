# RIGForge — Threat Model

RIGForge answers one question honestly: **"did the agent actually produce the
artifact it claims, and has anyone changed it since?"** It is a *proof-of-integrity*
layer, not a sandbox, not a code auditor, and not a substitute for review. This
document is deliberately blunt about what it does and does not defend, because a
security tool that oversells its guarantees is worse than none.

## Trust model

| Party | Trusted? | Notes |
|-------|----------|-------|
| The **signing key** | Trusted root | Whoever holds it can mint valid proofs. Keep it off the agent's machine. |
| The **verifier** (you / CI) | Trusted | Runs `verify` with the key; makes the accept/reject call. |
| The **agent** | **Untrusted** | May lie, tamper, forge hashes, drop gates. This is the adversary. |
| Anyone **between** seal and verify | Untrusted | Can tamper the artifact/packet in transit or at rest. |

The core guarantee is standard HMAC: a `ProofPacket` is signed over the artifact
hashes + run metadata with a secret key. Tampering anything the signature covers
invalidates it, and the signature cannot be recomputed without the key.

## What RIGForge catches (and the benchmark proves)

Each is a scenario in `rigforge benchmark`, scored on real `verify_*` verdicts:

- **Tampered artifact** — file changed after sealing, packet hash re-forged to hide it → signature fails.
- **Forged signature** — a fabricated signature the attacker couldn't have computed → fails.
- **Swapped artifact** — a different file substituted, hash re-pinned → signature fails.
- **Dropped gate** — a failed gate flipped to "passed" and re-hashed → signature fails (gates are signed).
- **Unsigned tamper** — tampered + re-hashed but never signed → rejected by the require-signature policy.

The benchmark's **100%** catch rate is a *correctness* result, not a probabilistic
score: an HMAC bound to the original hashes is cryptographically unforgeable without
the key, so these forgeries **must** fail. That's the point — and you can re-run it.

## What RIGForge does NOT defend against (out of scope, by design)

- **A compromised signing key.** If the agent or attacker obtains the key, they can
  mint valid proofs for anything. Key custody (a secrets manager, a CI-only key the
  agent never sees) is your responsibility. RIGForge assumes the key is secret.
- **An agent that simply never seals.** RIGForge proves *sealed* work; it can't force
  an agent to seal. "Every merge must carry a valid proof" is a *policy* you enforce
  in CI / branch protection — a layer above this tool.
- **Malicious-but-passing code.** If the agent writes a backdoor and the gates (tests,
  lint) genuinely pass over it, the proof is honest about what ran. RIGForge proves
  *integrity and provenance*, not *correctness or safety* of the code itself.
- **Weak gates.** A proof is only as meaningful as the gates it seals. Sealing a phase
  whose only gate is `echo ok` proves very little. Garbage gates in, garbage proof out.
- **Supply-chain / dependency compromise**, host compromise, or side channels. Same as
  any signing scheme — the root of trust is the key and the machine that holds it.

## Honest posture

RIGForge moves "the agent said done" from *unfalsifiable* to *cryptographically
checkable*, and makes dropping a gate or tampering an artifact **detectable** instead
of silent. It does not make your agent honest, your tests good, or your code safe. Use
it as the integrity spine under your own review and CI policy — not as a replacement
for them.

Found a gap in this model? Open an issue — adversarial scrutiny of the trust
boundaries is exactly the feedback we want.

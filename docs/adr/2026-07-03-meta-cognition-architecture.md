# ADR: Meta-Cognition Subsystem Architecture

**Date:** 2026-07-03
**Status:** Accepted
**Author:** System (via expert review remediation)

## Context

The meta-cognition subsystem is decomposed across 10 files
(runtime, reflector, regulator, coordinator, models, patterns,
triggers, audit, redact, evolution_bridge) totaling ~3,354 lines.
An external expert review flagged this as over-fragmentation:
understanding "meta-cognition" requires jumping across many files.

## Decision

Keep the 10-file decomposition but expose a single Facade
(`OriginAgent.agent.meta_cognition.MetaCognitionFacade`) as
the ONLY import target for external consumers.

### Dependency Graph

```
MetaCognitionFacade (public API)
├── MetaCognitionCoordinator (lifecycle owner)
│   ├── MetaCognitionRuntime (trigger collection)
│   ├── MetaCognitionReflector (turn-end artifacts)
│   └── MetaCognitionRegulator (throttle/depth)
├── meta_cognition_triggers (builders)
├── meta_cognition_models (contracts)
├── meta_cognition_patterns (consolidation)
├── meta_cognition_evolution_bridge (evolution signals)
├── meta_cognition_audit (append-only ledger)
└── meta_cognition_redact (shared helpers)
```

External consumers ONLY import `MetaCognitionFacade`.
Internal files may import each other directly.

## Consequences

- **Positive:** New contributors start from one file instead of 10.
  The facade documents what the subsystem DOES, not how it's split.
- **Positive:** Refactoring internal files has zero blast radius on
  external consumers (they only see the facade).
- **Negative:** Facade adds ~60 lines of delegation code.

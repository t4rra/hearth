# Hearth Technical Overview

## Purpose

This document summarizes the main implementation hurdles encountered in Hearth and the key engineering learnings reflected in the current codebase.

## Architecture at a Glance

Hearth is organized into clear subsystems:

- `hearth/core`: settings and OPDS client behavior.
- `hearth/converters`: conversion abstraction and backend integrations (KCC, Calibre).
- `hearth/sync`: Kindle transport abstraction and sync orchestration.
- `hearth/gui`: PyQt UI pages and worker-thread execution for long operations.
- `tests`: focused coverage for traversal, sync state logic, device I/O paths, and failure handling.

This separation is central to reliability: each unstable boundary (network, conversion tools, device transport) is isolated behind a dedicated interface.

## Technical Hurdles and Learnings

### 1) Kindle transport variability (USB vs MTP)

Hurdle:
Modern Kindles do not always expose a simple mounted filesystem. Some require MTP sessions and platform-specific tooling.

Implementation response:

- `KindleDevice` supports USB and MTP flows under one API.
- MTP path uses a persistent libmtp backend and transport probing.
- Fallback behavior exists when MTP sessions are unavailable.

Learning:
Device access should be abstracted from business logic. The sync layer should not care about transport details.

### 2) MTP session instability and platform quirks

Hurdle:
MTP can be transient and brittle, especially on macOS depending on libmtp/tool availability.

Implementation response:

- Sticky probe windows to reduce false disconnects.
- Shared backend/session reuse to avoid churn.
- Auto-install option for missing libmtp backend on macOS.
- Candidate path retries for remote Hearth folder operations.

Learning:
For MTP, robustness comes from conservative retries, state caching, and non-destructive fallbacks, not from a single probe attempt.

### 3) OPDS feed shape inconsistency

Hurdle:
Different servers vary in navigation/acquisition link patterns, pagination, and metadata quality.

Implementation response:

- Recursive traversal with visited-set loop guards.
- Navigation vs acquisition classification via link metadata.
- Paginated feed following for complete book loading.
- Heuristics for extracting human-readable title/series metadata.

Learning:
OPDS handling must be tolerant and heuristic-driven. Strict assumptions create brittle behavior.

### 4) Authentication continuity across browse and download

Hurdle:
A common integration bug is successful feed browsing but failed protected downloads due to auth mismatch.

Implementation response:

- One session object carries auth settings for both feed retrieval and file downloads.
- Supports `none`, `basic`, and `bearer` auth modes.

Learning:
Session continuity is required so all OPDS operations behave consistently for authenticated catalogs.

### 5) Ambiguous file types during download

Hurdle:
Extension and payload content can disagree, especially for ZIP-based assets.

Implementation response:

- Download path inspects signatures and archive contents.
- EPUB/container indicators and comic indicators are checked before selecting conversion route.
- Declared format and content signatures are combined to infer best extension.

Learning:
Extension-only routing is unsafe for mixed comic/ebook pipelines. Content-aware inference reduces conversion failures.

### 6) External converter discovery and runtime diagnostics

Hurdle:
KCC/Calibre may be installed in different locations, command names vary, and false positives are possible.

Implementation response:

- KCC command discovery prioritizes known CLI entry points and validates candidates.
- Runtime KCC diagnostics include command, dependency, and archive-tool readiness.
- 7z/7zz compatibility handling is built into KCC runtime environment preparation.
- Calibre discovery includes platform-specific fallback paths.

Learning:
Tooling integration needs runtime validation and explicit diagnostics, not silent assumptions.

### 7) Idempotent sync and stale-state correction

Hurdle:
Without durable state, repeated syncs re-do work and manual on-device deletions leave incorrect UI state.

Implementation response:

- On-device metadata (`.hearth_metadata.json`) tracks desired sync intent and observed device state.
- Skip logic avoids re-downloading/re-converting already-synced titles.
- Startup reconciliation updates metadata when files were manually removed on device.
- Explicit re-sync and delete flows are supported.

Learning:
Reliable sync is a state machine problem, not just a file-copy problem.

### 8) User-visible metadata and filename quality

Hurdle:
Raw server metadata can produce problematic filenames and poor Kindle title display.

Implementation response:

- Filename sanitization and truncation logic for safe, readable names.
- Post-conversion metadata override attempt via `ebook-meta` when available.

Learning:
Final user quality depends on output normalization, not only successful transport.

### 9) Long-running workflow UX

Hurdle:
Sync operations combine network I/O, conversion, and device I/O, which can take significant time.

Implementation response:

- Worker-thread execution for non-blocking GUI behavior.
- Progress overlays and detailed sync logs.
- Status badges for desired/on-device/syncing/not-synced states.

Learning:
Operational transparency is a core requirement for trust in sync tools.

## Testing Learnings

The tests emphasize realistic failure boundaries:

- OPDS traversal and metadata parsing edge cases.
- Auth/session correctness.
- USB/MTP write-path behavior and fallbacks.
- Sync skip/deletion/re-sync state transitions.
- Failure handling for copy/metadata paths.
- Optional live integration write test gated by environment flags.

Resulting insight:
Combining deterministic unit tests with opt-in hardware integration tests gives strong coverage for both logic and real-device uncertainty.

## Summary

Hearth's implementation demonstrates a practical pattern for media sync tools:

- isolate unstable boundaries,
- maintain explicit sync state,
- validate external dependencies at runtime,
- and make long operations observable to users.

These choices directly address the hardest parts of OPDS-to-Kindle synchronization in real-world environments.

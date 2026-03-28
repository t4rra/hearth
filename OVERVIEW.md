# Hearth Overview

## What Hearth Is

Hearth is a desktop application for macOS that keeps a Kindle in sync with an OPDS library server. It is built around a simple workflow: browse your OPDS catalog, choose what you want, and make sure the Kindle contains those books in Kindle-compatible formats.

## High-Level Functionality

Hearth provides four core capabilities:

- Connect to an OPDS server and browse library collections/books.
- Detect and access a connected Kindle (USB mass storage or MTP-backed access).
- Convert incompatible source formats to Kindle-friendly output automatically.
- Sync selected books while tracking on-device status and user intent.

## User-Facing Flow

1. Configure OPDS and Kindle settings.
2. Load OPDS collections in the Library tab.
3. See status badges indicating whether titles are already on device.
4. Select books or entire collection branches to sync.
5. Hearth downloads, converts when needed, and transfers files to the Kindle.
6. Hearth updates on-device metadata so future syncs are faster and consistent.

## Sync Model

Hearth uses one-way sync:

- OPDS server is the source of truth for available content.
- Kindle is the destination device.
- Local/on-device metadata is used to avoid repeated work and preserve selected intent.

This enables idempotent behavior: already-synced items are skipped unless the user explicitly forces a re-sync.

## Conversion Model

Hearth supports mixed media libraries:

- Comics/manga are routed through Kindle Comic Converter (KCC).
- Standard ebooks are routed through Calibre ebook-convert.

The app also performs lightweight content detection so ambiguous downloads (for example ZIP payloads) are routed to the appropriate conversion path.

## Device and Library Management

Beyond syncing, Hearth includes:

- A Kindle Files browser for viewing/downloading/deleting files on the device.
- A Settings tab for OPDS auth, Kindle transport preferences, and conversion options.

## Product Intent

Hearth is designed to make Kindle library management feel like syncing a media player:

- browse from server structure,
- clearly see what is on device,
- sync only what is needed,
- and keep state stable even when users manually change files on the Kindle.

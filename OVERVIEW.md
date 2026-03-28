# Hearth: Kindle Library Sync Application

## Executive Summary

**Hearth** is a macOS desktop application that syncs your Kindle library like an iPod, connecting to OPDS (Open Publication Distribution System) servers to manage your ebook collection. The application provides automatic format conversion, intelligent device detection, and intuitive browsing with real-time sync status badges.

### Core Value Proposition

- **One-way sync** from OPDS server (source of truth) to Kindle device
- **Automatic format conversion** for both ebooks and comics
- **Smart device integration** for USB and MTP-based Kindle devices
- **Status tracking** with visual badges showing device state
- **Collection management** with granular sync controls

---

## Architecture Overview

### High-Level System Design

```
┌─────────────────────────────────────────────────────────┐
│                   GUI Layer (PyQt6)                      │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Settings Page │ Converter Page │ Sync Page │ Files │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────▼────────┐  ┌──────▼──────┐  ┌───────▼─────────┐
│  Sync Manager  │  │ OPDS Client │  │ Device Manager  │
│                │  │             │  │                 │
│ • Coordination │  │ • Feed parse │  │ • USB detection │
│ • Sync flow    │  │ • Auth (3x)  │  │ • MTP handling  │
│ • Metadata mgmt│  │ • Collections│  │ • Metadata I/O  │
└────────────────┘  └─────────────┘  └─────────────────┘
        │                                      │
        │                  ┌──────────────────┘
        │                  │
┌───────▼────────────────┬─▼─────────────────┐
│   Converter Manager    │  Kindle Device    │
│                        │  (Hardware I/O)   │
│ • Format routing       │                   │
│ • Metadata detection   │ • libmtp wrapper  │
│ • Fallback handling    │ • Metadata.json   │
└───────┬────────────────┴────────────────────┘
        │
        ├──────────────────────┬────────────────┐
        │                      │                │
    ┌───▼───┐         ┌────────▼──┐     ┌──────▼──┐
    │  KCC  │         │  Calibre  │     │ libmtp/ │
    │ Comic │         │  EBook    │     │   USB   │
    │Convert│         │  Convert  │     │ Backend │
    └───────┘         └───────────┘     └─────────┘
```

### Module Structure

```
hearth/
├── core/
│   ├── config.py           # Settings persistence & validation (Pydantic)
│   └── opds_client.py      # OPDS feed parsing & collection browsing
│
├── converters/
│   ├── base.py             # Abstract Converter interface
│   ├── kcc.py              # KCC wrapper (comics: CBZ, CBR, etc.)
│   ├── calibre.py          # Calibre wrapper (ebooks: EPUB, PDF, etc.)
│   └── manager.py          # Format detection & conversion routing
│
├── sync/
│   ├── kindle_device.py    # USB & libmtp device interface
│   └── manager.py          # Sync orchestration & metadata management
│
└── gui/
    ├── main_window.py      # Application shell & page navigation
    ├── settings_page.py    # OPDS config & conversion preferences
    ├── converter_page.py   # Manual format conversion UI
    ├── sync_page.py        # Collections browser & sync controls
    └── kindle_files_page.py # File browser on connected Kindle
```

---

## Core Components & Functionality

### 1. OPDS Client (`hearth/core/opds_client.py`)

**Purpose**: Fetch and parse ebook collections from OPDS servers.

**Key Responsibilities**:

- Parse Atom/RSS feeds using `feedparser`
- Extract book metadata (title, author, download URLs, covers)
- Support nested collections (hierarchical browsing)
- Handle three authentication methods:
  - **None** (public OPDS)
  - **HTTP Basic** (username/password)
  - **Bearer token** (API tokens)

**Key Design Decisions**:

- Single `requests.Session` maintains auth state across all requests
- Authenticated downloads use the same session credentials (no credential reuse bugs)
- Title extraction prioritizes multiple metadata sources (fallback chain)
- Identifier detection prevents treating internal URNs as human-readable titles

**Challenges Solved**:

- OPDS feeds vary widely in metadata structure; robust extraction requires checking multiple fields
- Some servers return duplicate books at different hierarchy levels; client normalizes by OPDS ID
- Authentication state must persist across feed navigation

---

### 2. Converter Pipeline (`hearth/converters/`)

Hearth converts ebooks to Kindle-compatible formats via two specialized converters:

#### **KCC Comic Converter** (`kcc.py`)

**Supported Formats**: CBZ, CBR, CB7, CZT (compressed comics/manga)

**Workflow**:

1. Detect file as comic (by extension + metadata analysis)
2. Route to KCC with manga mode detection (RTL vs. LTR)
3. Apply device profile (currently optimized for Kindle Scribe Gen 1)
4. Generate MOBI/AZW3

**Device Profile (Kindle Scribe Gen 1)**:

- High-quality conversion
- Automatic margin removal
- Optimized for 10.2" display
- RTL (right-to-left) manga support

**Critical Issues Solved**:

**Issue 1: KCC Command Discovery**

- **Problem**: Multiple ways to invoke KCC (CLI binary, Python module, macOS Kerberos false positive)
- **Root Cause**: KCC can be installed as `kcc-c2e` (binary) or via `python -m comic2ebook` (module)
- **False Positive**: macOS ships `/usr/bin/kcc` (Kerberos); must reject this
- **Solution**:
  - Discovery order: Check CLI binaries (`kcc-c2e`, `kcc-c2e.py`, `comic2ebook`) → Python modules → kcc shim
  - Validate by running command and parsing help output
  - Reject Kerberos by checking output for "heimdal" or "kerberos" (case-insensitive)
  - Cache found command for performance
  - Added user-configurable `kcc_c2e_path` setting to override discovery

**Issue 2: Conversion Failures Despite Version Detection Passing**

- **Problem**: Module fallback (`python -m comic2ebook`) would pass version validation but fail at conversion time
- **Root Cause**: Different environments, PATH issues, environment isolation problems
- **Solution**:
  - CLI binaries preferred over Python module invocation
  - Validation checks actual conversion command, not just version check
  - Better error messages show which command was attempted

#### **Calibre EBook Converter** (`calibre.py`)

**Supported Formats**: EPUB, PDF, TXT, DOC, DOCX, MOBI

**Workflow**:

1. Detect as non-comic (or if KCC fails to handle)
2. Route to Calibre ebook-convert
3. Apply Kindle-optimized settings
4. Generate MOBI/AZW3

**Kindle Scribe Gen 1 Settings**:

- Output format: AZW3 (modern Kindle format)
- No unsupported `--paper-size` option (Calibre removed in newer versions)
- Custom margin handling
- Proper encoding (UTF-8)

**Critical Issues Solved**:

**Issue 1: Unsupported Calibre Options**

- **Problem**: Some Calibre versions don't support `--paper-size` option
- **Solution**: Removed option; modern Calibre handles sizing automatically

**Issue 2: Format Detection Ambiguity**

- **Problem**: Some files can be converted by either converter (e.g., EPUB can be a comic)
- **Root Cause**: OPDS metadata may be sparse or misleading
- **Solution**: Content profile detection reads file metadata:
  - Check file extension first
  - Inspect EPUB/CBZ internals for content hints
  - Analyze title/description for manga/comic keywords
  - Route to appropriate converter

#### **Converter Manager** (`manager.py`)

**Responsibilities**:

- Coordinat the two converters
- Detect file content profile (comic vs. ebook)
- Route format conversions to appropriate converter
- Handle conversion failures and fallbacks

**Key Pattern**:

```python
if is_comic and can_convert_as_comic:
    use_kcc()
elif can_convert_as_ebook:
    use_calibre()
else:
    fail_with_error()
```

---

### 3. Kindle Device Interface (`hearth/sync/kindle_device.py`)

**Purpose**: Abstract USB and MTP device access into unified interface.

**Supported Device Types**:

- USB Mass Storage (older Kindles)
- MTP (Kindle Scribe, newer models)
- Auto-mounted paths via `/Volumes` on macOS

#### **MTP Integration: Major Technical Hurdle**

Newer Kindles (including Scribe Gen 1) use **Media Transfer Protocol (MTP)** instead of USB Mass Storage. This required building a custom libmtp wrapper.

**Challenge 1: libmtp Availability**

- **Problem**: No native MTP support on macOS; requires third-party tools
- **Solution implemented**:
  - Check system libmtp via ctypes (`ctypes.util.find_library`)
  - Fall back to multiple helper tools if libmtp unavailable:
    - `go-mtpfs` (preferred, bundled in `go-mtpx` package)
    - `simple-mtpfs`
    - `jmtpfs`
  - Optional auto-install of helper tools

**Challenge 2: Session Persistence**

- **Problem**: Each MTP operation would disconnect/reconnect, forcing device unplugging
- **Root Cause**: Previous approach created new ctypes session per command
- **Solution**:
  - Implemented persistent in-process libmtp session via `LIBMTP_Open_Raw_Device_Uncached`
  - Reuse device handle across operations
  - Class-level shared backend prevents redundant sessions
  - Explicit `close()` method for cleanup vs. auto-release on destruction

**Challenge 3: libmtp Build Variance**

- **Problem 1**: Error stack clearing function has two names:
  - `LIBMTP_clear_errorstack` (standard)
  - `LIBMTP_Clear_Errorstack` (variant)
- **Solution**: Check both names, use whichever exists
- **Problem 2**: macOS libmtp builds crash when freeing listing buffers
- **Solution**:
  - Environment variable `HEARTH_MTP_DESTROY_LISTINGS` controls destruction
  - Defaults to `False` on macOS (avoids allocator aborts)
  - Fully destroys only on Linux/other platforms
- **Problem 3**: Some builds crash on upload descriptor destruction
- **Solution**: Similar environment variable `HEARTH_MTP_DESTROY_UPLOAD_DESC`

**Challenge 4: API Inconsistency**

- **Problem**: Some libmtp versions don't support `LIBMTP_Get_Folder_List` or return empty results on Kindle Scribe
- **Root Cause**: libmtp implementation varies; newer Kindles structure file storage differently
- **Solution**:
  - Implemented recursive file tree traversal via `LIBMTP_Get_Files_And_Folders`
  - Deferred recursion pattern (iterative depth-first search)
  - Probes up to 3 attempts with small delays to handle transient MTP state
  - Caches snapshots (20-second reuse window) to minimize repeated queries

**Challenge 5: ctypes Structure Parsing**

- **Problem 1**: `_LIBMTPFile` field order must match libmtp exactly
  - Correct order: `filesize`, `modificationdate`, `filetype` (not ID-first)
- **Problem 2**: Vendor/product are `char *` pointers, not static arrays
- **Solution**: Define ctypes structures matching exact C layout; mismatches corrupt all following fields

**Challenge 6: Device Selection**

- **Problem**: System may have multiple MTP devices (Kindle, phone, etc.)
- **Solution**:
  - Detect by vendor ID (`0x1949` = Amazon) or name matching
  - Prioritize Kindle by USB product/vendor identifiers
  - Log each detected device for debugging

**Challenge 7: Write Operations**

- **Problem**: ctypes folder creation or file send sometimes fails on macOS
- **Root Cause**: Interaction with specific libmtp versions or system state
- **Solution**: Fallback to libmtp CLI tools:
  - Try ctypes path first (faster)
  - If fails, use `mtp-connect --newfolder` (folder creation)
  - Or use `mtp-connect --sendfile` (file upload)
  - Graceful degradation maintains functionality

#### **Metadata Persistence** (`KindleMetadata` dataclass)

Hearth maintains a metadata file (`.hearth_metadata.json`) on the Kindle to reliably match books:

```json
{
  "book-id": {
    "title": "Book Title",
    "author": "Author Name",
    "opds_id": "unique-id-from-opds",
    "original_format": "epub",
    "kindle_format": "mobi",
    "sync_date": "2024-01-15T10:30:00",
    "desired_sync": true,
    "on_device": true,
    "sync_status": "on_device"
  }
}
```

**Key fields**:

- `opds_id`: Unique identifier from OPDS server (enables change detection)
- `desired_sync`: Whether user wants this book synced (affected by re-sync/delete actions)
- `on_device`: Last-known presence on device
- `sync_status`: Current state (`on_device`, `not_synced`, `pending`)

**Startup Reconciliation**:

- On launch, compares metadata against actual device files
- Detects manual deletions (metadata entry but file missing)
- Updates `on_device` and `sync_status` to reflect reality

---

### 4. Sync Manager (`hearth/sync/manager.py`)

**Responsibilities**:

- Orchestrate OPDS → Kindle sync workflow
- Download books from OPDS server
- Detect content type and route to converters
- Upload to Kindle device
- Track metadata and sync state
- Implement "skip already-synced" optimization

**Sync Workflow**:

```
SELECT books to sync FROM collections
│
├─→ FOR EACH book:
│   ├─→ Check metadata: already on device?
│   │   └─→ If yes: SKIP (already synced)
│   │
│   ├─→ DOWNLOAD book from OPDS
│   ├─→ DETECT content profile (comic vs. ebook)
│   ├─→ CONVERT to Kindle format
│   ├─→ SEND to Kindle device
│   └─→ UPDATE metadata on device
│
└─→ DISPLAY sync summary
```

**Skip Already-Synced Items**:

- Checks metadata: `on_device=True` and `sync_status="on_device"`
- If already present, logs "Skipping {title} (already synced)" and returns success
- Saves bandwidth and time on re-runs of same sync
- Visible in UI as "WANTED · ON DEVICE" with checkmark

**Re-Sync & Delete**:

- **Re-Sync**: Mark `desired_sync=False`, then re-run sync (re-downloads, converts, sends)
- **Delete**: Remove file from device and metadata entry
- Context menu (right-click) provides both operations

---

### 5. GUI Application (`hearth/gui/`)

**Architecture**: PyQt6-based multi-page interface

#### **Settings Page** (`settings_page.py`)

- OPDS server URL configuration
- Authentication setup (type, credentials)
- Kindle device detection (USB, MTP auto-detect)
- MTP backend tool selection
- Conversion preferences (device profile, manga mode)

#### **Sync Page** (`sync_page.py`)

- Collections browser (OPDS hierarchy)
- All Books view (flat list)
- Status badges: "✓ ON DEVICE(s)", "NOT INSTALLED", "PENDING SYNC"
- Collection-level sync count: "[installed/total]"
- Filter options: All, Not Installed, Installed
- Right-click context menu: Re-sync & Delete actions
- Real-time sync log with progress updates

#### **Converter Page** (`converter_page.py`)

- Manual format conversion UI
- Select input file and output format
- Override device profile / manga mode
- Progress display and log

#### **Kindle Files Page** (`kindle_files_page.py`)

- Browse actual files on connected Kindle
- File tree view organized by directory
- Refresh triggers MTP file listing

**Key UI Decisions**:

- Status badges at multiple levels (per-book, per-collection) show sync state
- Real-time sync log prevents user uncertainty during long operations
- Context menu provides advanced actions (re-sync, delete) without cluttering main UI
- Separate Converter page allows testing conversion without device connection

---

## Critical Technical Hurdles & Solutions

### Hurdle 1: MTP Device Access & Session Management

**The Problem**:
Kindle Scribe and newer models use MTP instead of USB Mass Storage. macOS has no native MTP support, requiring ctypes bindings to system libmtp.

**Sub-Issues Encountered**:

1. **Session thrashing**: Per-operation connections forced device replug
2. **API inconsistencies**: Different libmtp builds, different Kindle file structures
3. **Allocator crashes**: macOS builds corrupt memory when destroying listing buffers
4. **Command discovery**: Multiple ways to invoke MTP tools, macOS false positives

**Solutions Implemented**:

- ✅ Persistent in-process libmtp session (persistent handle across operations)
- ✅ ctypes structure definitions matching exact libmtp C layout
- ✅ Recursive file traversal fallback when standard APIs return empty
- ✅ Environment variables to tune buffer destruction behavior
- ✅ Fallback to CLI tools (`mtp-connect`) for write failures
- ✅ Device discovery by USB vendor/product ID prioritizing Amazon

**Outcome**: Reliable MTP access with graceful degradation.

---

### Hurdle 2: KCC Comic Converter Discovery & Validation

**The Problem**:
KCC can be installed multiple ways (CLI binary, Python module, system shim) with conflicting implementations. macOS ships a Kerberos tool also named `kcc` causing false positives.

**Sub-Issues Encountered**:

1. **False positive**: Version detection passes for Kerberos but conversion fails
2. **Module issues**: Python module fallback doesn't work reliably in practice
3. **Discovery order**: Binary discovery didn't validate before returning

**Solutions Implemented**:

- ✅ Discovery priority: Validate CLI binaries → Python modules → kcc shim (with Kerberos rejection)
- ✅ Validation runs actual command and checks output for "comic" or "kindle"
- ✅ Rejection checks for "heimdal" or "kerberos" (case-insensitive)
- ✅ User-configurable `kcc_c2e_path` setting for custom installations
- ✅ Better error messages show which command was attempted

**Outcome**: Reliable KCC discovery with clear failure diagnostics.

---

### Hurdle 3: Content Profile Detection for Mixed Formats

**The Problem**:
OPDS metadata is often incomplete or ambiguous. Some files could be comics or ebooks, and routing them to the wrong converter fails silently.

**Solutions Implemented**:

- ✅ Multi-stage detection: extension → file internals → metadata keywords
- ✅ EPUB inspection for detected content hints (images vs. text ratio)
- ✅ Metadata passed through sync pipeline for routing hints
- ✅ Fallback: Try KCC first (comic), then Calibre (ebook) if needed

**Outcome**: Robust format detection and automatic fallback conversion.

---

### Hurdle 4: Calibre Version Compatibility

**The Problem**:
Calibre evolved; older options like `--paper-size` no longer exist in newer versions.

**Solutions Implemented**:

- ✅ Removed unsupported options
- ✅ Target modern Calibre defaults (AZW3 with automatic sizing)
- ✅ Tested with recent Calibre versions

**Outcome**: Works across Calibre versions.

---

### Hurdle 5: Metadata Reliability Across Device Changes

**The Problem**:
Users manually delete books on Kindle; metadata gets stale. Previous syncs show books as "WANTED" even though they're gone.

**Solutions Implemented**:

- ✅ Startup reconciliation: Compare metadata against actual device files
- ✅ Update `on_device` and `sync_status` based on reality
- ✅ Skip already-synced books (optimization + consistency)
- ✅ Re-sync and delete operations update metadata atomically

**Outcome**: Reliable book presence tracking even after manual device changes.

---

## Installation & Deployment

### System Requirements

- macOS 10.14+
- Python 3.11+
- Homebrew (package manager)
- Calibre (ebook conversion)
- KCC (comic conversion)
- libmtp or MTP helper tools (for MTP devices)

### Quick Setup

```bash
# Install dependencies
brew install python@3.11 calibre go-mtpfs

# Clone and install
git clone <repo> && cd hearth
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run
python main.py
```

### Configuration Flow

1. **First Run**: Creates `~/.config/hearth/settings.json`
2. **Settings Page**: User configures OPDS server, auth, device
3. **Auto-Detect**: Attempts to recognize connected Kindle
4. **Collections**: Browse OPDS and sync

---

## Testing Strategy

**Test Coverage**: 24+ comprehensive unit tests

**Key Test Areas**:

- Comic/ebook format detection
- Converter manager routing
- Mock conversion workflows
- Demo file validation (actual CBZ/EPUB test files)
- KCC command discovery (7 dedicated tests)
- MTP device operations (isolated from live device)
- Sync manager state transitions
- Metadata persistence

**Testing Philosophy**:

- Mock-based tests avoid touching actual Kindle device
- No network calls (OPDS testing uses fixtures)
- File I/O uses temporary directories
- Conversion tests verify output without running actual converter binaries on CI

---

## Performance Considerations

### MTP Snapshot Caching

- File listing on device cached for 20 seconds
- Reduces repeated MTP queries during UI refresh
- Configurable via class variables

### Persistent Sessions

- OPDS `requests.Session` reused across operations
- libmtp device handle held for duration of sync
- Connection pooling reduces latency

### Lazy Loading

- Collections loaded on demand (not entire OPDS tree upfront)
- Device file tree loaded only when Kindle Files tab accessed
- Conversion routing detects content profile once, reuses result

---

## Common Developer Tasks

### Adding a New Converter

1. Inherit from `base.ConversionFormat` and `base.Converter`
2. Implement `can_convert()` and `convert()` methods
3. Register in `ConverterManager.__init__`
4. Add format routing logic

### Debugging MTP Issues

1. Check `/var/log/system.log` for kernel MTP errors
2. Set `HEARTH_MTP_DEBUG=1` to enable libmtp logging
3. Test with `mtp-detect` and `mtp-connect` CLI tools
4. Verify device vendor ID with `system_profiler SPUSBDataType`

### Testing Converter Changes

1. Use `Converter.convert(input_path, format)` directly
2. Keep demo files in `DEMO Files/` for regression testing
3. Run `pytest tests/test_conversion.py` for suite
4. Manual converter testing via Converter Page UI

---

## Future Considerations

### Potential Enhancements

- **Two-way sync**: Detect library changes on device
- **Multiple OPDS servers**: Switch between sources
- **Conversion profiles**: Save custom device/format preferences
- **Linux support**: Extend beyond macOS
- **Batch operations**: Queue multiple syncs, schedule automation

### Known Limitations

- macOS only (Windows/Linux would need MTP backend adjustments)
- Single OPDS server per session
- No offline sync (always checks OPDS for current state)
- Conversion requires external tools (Calibre, KCC)

---

## Acronyms & Terminology

| Term        | Definition                                                                   |
| ----------- | ---------------------------------------------------------------------------- |
| **OPDS**    | Open Publication Distribution System (standard for ebook server feeds)       |
| **MTP**     | Media Transfer Protocol (modern device communication, used by Kindle Scribe) |
| **USB MSC** | USB Mass Storage Class (older device access method)                          |
| **MOBI**    | Amazon's legacy ebook format                                                 |
| **AZW3**    | Amazon's modern ebook format (KF8)                                           |
| **CBZ**     | Comic Book Zip (compressed comic archive)                                    |
| **KCC**     | Kindle Comic Converter (tool for converting comics)                          |
| **libmtp**  | Open-source library for MTP device access                                    |
| **Calibre** | Popular open-source ebook management tool                                    |

---

## References & Resources

- [OPDS Specification](https://specs.opds.io/)
- [libmtp Documentation](https://github.com/libmtp/libmtp)
- [Calibre Developer Docs](https://manual.calibre-ebook.com/)
- [KCC GitHub](https://github.com/ciromattia/kcc)
- [PyQt6 Documentation](https://www.riverbankcomputing.com/software/pyqt/)

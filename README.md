# Hearth - Kindle Library Sync Application

## Overview

Hearth is a macOS application that syncs your Kindle library like an iPod, connecting to an OPDS server to manage your ebook collection. It provides automatic format conversion and intuitive browsing of OPDS collections with visual badges showing what's already on your device.

## Features Implemented

### Core Features

- ✅ One-way sync from OPDS server to Kindle, with server as source of truth
- ✅ Browse OPDS collections and individual books with device status badges
- ✅ Sync individual books or entire collections to Kindle
- ✅ Auto-convert books to Kindle-compatible formats (MOBI, AZW3)
- ✅ Keep metadata file on Kindle for reliable book matching
- ✅ macOS support with cross-platform architecture

### Format Conversion

- ✅ **Comic Conversion**: KCC (Kindle Comic Converter) wrapper for CBZ, CBR, CB7, CZT
  - Supports high/medium/low quality presets
  - Automatic margin removal for Kindle Scribe Gen 1
- ✅ **EBook Conversion**: Calibre ebook-convert wrapper for EPUB, MOBI, PDF, TXT, DOC, DOCX
  - Optimized settings for Kindle Scribe Gen 1
  - Support for A4 paper size and custom margins

### Kindle Device Support

- ✅ USB Mass Storage detection and mounting
- ✅ MTP protocol detection on macOS via USB device probing
- ✅ Auto-mount attempts using `go-mtpx`, `go-mtpfs`, `simple-mtpfs`, or `jmtpfs`
- ✅ Optional auto-install of MTP backend tools on macOS
- ✅ Automatic device detection on macOS
- ✅ Books organized in dedicated "Hearth" folder under Documents

### OPDS Authentication

- ✅ No auth (public OPDS)
- ✅ HTTP Basic auth (username/password)
- ✅ Bearer token auth
- ✅ Authenticated OPDS download requests use the same session credentials

### User Interface

- ✅ Settings page for OPDS server configuration
- ✅ Converter page for on-demand format conversion
- ✅ Sync page with Collections view (primary) and All Books view
- ✅ Status badges showing "✓ ON DEVICE" for installed books
- ✅ Collection-level sync status showing [installed/total] count
- ✅ Filter options (All, Not Installed, Installed)
- ✅ Real-time sync log with progress updates

### Testing

- ✅ 24 comprehensive unit tests covering:
  - Comic format detection and handling
  - EBook format detection and handling
  - Converter manager functionality
  - Mock conversion workflows
  - Demo file validation (CBZ and EPUB)
  - Format conversion enumeration
  - Integration tests for complete workflows

## Project Structure

```
hearth/
├── hearth/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings management
│   │   └── opds_client.py         # OPDS feed parsing & collection support
│   ├── converters/
│   │   ├── __init__.py
│   │   ├── base.py                # Base converter interface
│   │   ├── kcc.py                 # Comic converter (KCC wrapper)
│   │   ├── calibre.py             # EBook converter (Calibre wrapper)
│   │   └── manager.py             # Converter manager & coordinator
│   ├── gui/
│   │   ├── __init__.py
│   │   ├── main_window.py         # Main application window
│   │   ├── settings_page.py       # Settings configuration UI
│   │   ├── converter_page.py      # Manual conversion UI
│   │   └── sync_page.py           # OPDS browsing & sync UI
│   └── sync/
│       ├── __init__.py
│       ├── kindle_device.py       # Kindle device interface
│       └── manager.py             # Sync coordination
├── tests/
│   ├── __init__.py
│   └── test_conversion.py         # Comprehensive test suite
├── main.py                        # Application entry point
├── requirements.txt               # Python dependencies
└── SPECS.md                      # Project specifications
```

## Installation & Setup

### Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt
```

Required packages:

- `PyQt6==6.7.0` - GUI framework
- `requests==2.31.0` - HTTP client for OPDS
- `feedparser==6.1.0` - OPDS feed parsing
- `python-magic==0.4.27` - File type detection
- `pydantic==2.6.0` - Data validation
- `attrs==23.2.0` - Class utilities
- `colorama==0.4.6` - Colored terminal output

### External Requirements

**macOS:**

- Calibre (for ebook-convert): `brew install calibre`
- KCC (for comic conversion): installed via `requirements.txt`
  - if missing at runtime, Hearth can attempt `pip install kcc-comic2ebook`
  - disable runtime install attempt with `HEARTH_AUTO_INSTALL_KCC=0`
- Optional MTP backend for modern Kindles:
  - `go-mtpx` (preferred when installed)
  - or `go-mtpfs`, `simple-mtpfs`, `jmtpfs`
  - Hearth can auto-install one backend when enabled in Settings

### Running the Application

```bash
python main.py
```

## Usage Guide

### Initial Configuration

1. **Settings Tab:**
   - Enter your OPDS server URL (e.g., `http://opds.example.com`)
   - Select OPDS auth mode (`none`, `basic`, or `bearer`)
   - Enter credentials/token when auth is enabled
   - Select or browse for your Kindle device mount path
   - Optionally use **Auto-Detect** for USB/MTP Kindle discovery
   - Optionally choose preferred MTP backend tool
   - Configure conversion options (quality presets, margin removal)
   - Toggle auto-conversion and original file retention

2. **Check Connection:**
   - Click "Check Connection" to verify OPDS and Kindle access

### Browsing & Syncing

1. **Collections View (Default):**
   - OPDS collections are displayed in a tree structure
   - Each collection shows: `[installed/total books]` in the badge
   - Individual books show green "✓ ON DEVICE" badge if already synced
   - Books not on device show no badge

2. **Syncing:**
   - Select books/collections from the tree view
   - Click "Sync Selected" to sync to your Kindle
   - Status updates appear in the "Sync Log" at bottom
   - Books are automatically converted and organized in `Documents/Hearth/` on device

3. **Filter (All Books View):**
   - Switch to "All Books" view for flat list
   - Filter by: All Books, Not Installed, Installed
   - Select multiple books and sync them
   - Startup reconciliation keeps the local sync metadata aligned with real Kindle files; if a previously synced book was manually removed on device, Hearth clears its "wanted" state on next startup

### Format Conversion

1. **Manual Conversion:**
   - Go to "Converter" tab
   - Select input file (CBZ, EPUB, PDF, etc.)
   - Choose output format (MOBI, AZW3, EPUB)
   - Click "Convert" and monitor progress in output log

2. **Automatic Conversion:**
   - Enable "Auto-convert to MOBI format" in Settings
   - Synced books are automatically converted if needed

## Development

### Running Tests

```bash
cd /Users/easun/Documents/Code/hearth
python3 -c "from tests.test_conversion import run_tests; run_tests()"
```

Test Coverage:

- Comic conversion detection and workflows (KCC)
- EBook conversion detection and workflows (Calibre)
- Converter manager functionality
- Format conversion result handling
- OPDS collection and book retrieval
- Metadata management
- Demo file validation
- Integration workflows

### Adding New Features

1. **New Converter Type:**
   - Extend `BaseConverter` in `hearth/converters/base.py`
   - Implement `can_convert()`, `convert()`, `get_supported_formats()`
   - Register in `ConverterManager`

2. **New OPDS Features:**
   - Extend `OPDSClient` in `hearth/core/opds_client.py`
   - Add methods for new feed types or operations

3. **UI Enhancements:**
   - Modify relevant page in `hearth/gui/`
   - Use PyQt6 widgets and layouts
   - Update `SyncWorker` for async operations if needed

## macOS Specifics

### Calibre Installation

```bash
brew install calibre
# Command will be at: /opt/homebrew/bin/ebook-convert (M1 Macs)
#                 or: /Applications/calibre.app/Contents/MacOS/ebook-convert
```

### KCC Installation

```bash
pip install kcc-comic2ebook
```

### Kindle Detection

- USB drives mounted in `/Volumes/`
- Kindle Scribe Gen 1 optimizations built-in
- Books organized in: `<mount>/documents/Hearth/`

## Configuration Files

Hearth settings are stored in:

```
~/.config/hearth/settings.json
```

Example settings file:

```json
{
  "opds_url": "http://opds-server.example.com",
  "opds_auth_type": "none",
  "opds_username": "",
  "opds_password": "",
  "opds_token": "",
  "kindle_mount_path": "/Volumes/Kindle",
  "mtp_auto_mount": true,
  "mtp_mount_tool": "auto",
  "sync_enabled": true,
  "auto_convert": true,
  "conversion_settings": {
    "comic_format": "MOBI",
    "ebook_format": "MOBI",
    "comic_quality": "high",
    "remove_margins": true
  },
  "keep_originals": true,
  "metadata_file": ".hearth_metadata.json"
}
```

## Metadata

Hearth maintains a metadata file on your Kindle at:

```
<Kindle Mount>/documents/Hearth/.hearth_metadata.json
```

This stores:

- Book title, author, OPDS ID
- Original and Kindle formats
- Sync date
- Local file path

## Known Limitations & TODO

1. **MTP Mounting:** Detection is automatic, but mounting still depends on an installed backend CLI tool
2. **Collection Sync:** Bulk collection sync in progress (currently book-by-book)
3. **Network Streaming:** OPDS feeds are cached locally (no streaming support yet)
4. **Kindle Formats:** Currently optimized for MOBI; AZW3 support ready but not tested extensively

## Architecture Decisions

- **PyQt6**: Lightweight GUI framework with excellent macOS support
- **OPDS via HTTP**: Standard library support with feedparser
- **External Tools**: Best-in-class conversion tools (KCC, Calibre) called via subprocess
- **Metadata on Device**: Ensures sync reliability without cloud dependency
- **Modular Converters**: Easy to add new format support via plugin-like architecture
- **Collections-First UI**: Respects OPDS server organization for better discovery

## Future Enhancements

- [ ] Bi-directional sync (track device reads on server)
- [ ] Cover art display in collections
- [ ] Advanced filtering (by author, date, series)
- [ ] Wishlist/reading list support
- [ ] Series detection and grouping
- [ ] Batch operations on selected collections
- [ ] Sync profiles (different settings per collection)
- [ ] Statistics dashboard
- [ ] Export/import sync history

## Support & Troubleshooting

### Kindle Not Detected

1. Ensure Kindle is connected via USB
2. Check Settings > Kindle mount path is correct
3. For MTP devices, may need to configure go-mtpfs

### Conversion Failures

1. Verify calibre is installed: `which ebook-convert`
2. Verify KCC is installed: `pip list | grep kcc`
3. Check conversion log for specific error messages
4. Try manual conversion via Converter tab first

### OPDS Connection Issues

1. Verify OPDS server URL is correct and accessible
2. Check network connectivity
3. Look for error messages in sync log
4. Test URL directly in browser first

## License & Credits

Built with:

- PyQt6 for GUI
- Calibre's ebook-convert for ebook conversion
- KCC for comic conversion
- Feedparser for OPDS parsing

Inspired by iTunes syncing model for ebooks.

# Sync Skip and Deletion Feature

## Overview

This feature prevents re-downloading, re-converting, and re-sending books that are already synced to your Kindle. It also provides UI controls to re-sync or delete books from your Kindle.

## How It Works

### Skip Already-Synced Items

When you run a sync:

1. **Check existing metadata**: Before downloading a book, Hearth checks if it's already on your Kindle
2. **Skip download/convert/send**: If the book is present with `on_device=True` and `sync_status="on_device"`, the sync is skipped
3. **Return success**: The sync returns `True` (indicating success) without performing any operations
4. **Log message**: A message logs "Skipping {title} (already synced)" to inform the user

#### Example

- You check 10 books to sync
- 3 of them are already on your Kindle
- Only 7 books are downloaded, converted, and sent
- All 10 checksmarks turn green with "WANTED · ON DEVICE"

### Startup Metadata Reconciliation

At startup, Hearth compares metadata entries against actual files currently present on Kindle:

1. **Read metadata and device file list**: Hearth loads `.hearth_metadata.json` and enumerates files in `Documents/Hearth`
2. **Detect manual device deletions**: If a metadata entry points to a filename that is no longer on Kindle, Hearth treats it as removed
3. **Clear stale wanted state**: The entry is updated to `desired_sync=false`, `on_device=false`, and `sync_status="not_synced"`
4. **Persist updates**: Metadata is saved only when a reconciliation change is required

This prevents books that were manually deleted on Kindle from continuing to appear as "WANTED" in the UI.

### Delete from Kindle

To delete a book from your Kindle:

1. **Right-click on an installed book** in the library tree
2. **Select "Delete from Kindle"**
3. **Confirm the action**
4. Hearth will:
   - Mark the book as `marked_for_deletion=True` in metadata
   - Delete the file from the Kindle's Hearth folder
   - Remove the book metadata entry
   - Refresh the tree to show updated status

### Re-Sync a Book

To force re-sync a book that's already on your Kindle:

1. **Right-click on an installed book** in the library tree
2. **Select "Re-Sync to Kindle"**
3. **Confirm the action**
4. Hearth will:
   - Download the book again
   - Re-convert to the target format
   - Re-send to the Kindle
   - Update the sync date and metadata

## Implementation Details

### Files Modified

#### `hearth/sync/kindle_device.py`

- Added `marked_for_deletion` field to `KindleMetadata` dataclass
- Updated `save_metadata()` to persist the new field
- Added `delete_file_from_kindle()` method to remove files from Kindle

#### `hearth/sync/manager.py`

- Updated `sync_book()` to accept `force_resync` parameter
- Added skip logic: checks if book is already on device before downloading
- Added `force_resync_book()` convenience method
- Added `mark_book_for_deletion()` method
- Added `delete_marked_books()` method

#### `hearth/gui/sync_page.py`

- Added `QMenu` import for context menus
- Enabled context menu on the collections tree
- Added `on_tree_context_menu()` handler for right-click context menu
- Added `resync_book()` handler with confirmation dialog
- Added `delete_book()` handler with confirmation dialog
- Both handlers refresh the tree after operation to show updated status

### Metadata Persistence

The new `marked_for_deletion` field is saved to the Kindle's metadata file (`.hearth_metadata.json`):

```json
{
  "book-id": {
    "title": "Book Title",
    "author": "Author Name",
    "opds_id": "book-id",
    "original_format": "epub",
    "kindle_format": "mobi",
    "sync_date": "2024-03-27T...",
    "local_path": "/kindle/book-id.mobi",
    "desired_sync": true,
    "on_device": true,
    "sync_status": "on_device",
    "marked_for_deletion": false
  }
}
```

## Behavior Changes

### Before This Feature

- Checking the same book for sync multiple times would re-download, re-convert, and re-send it
- No way to control/delete already-synced items from the GUI
- Books would accumulate on the Kindle

### After This Feature

- Checking the same book twice only syncs once (if checked in different sync operations)
- Easy one-click deletion of books from Kindle
- One-click re-sync for books that need updating
- Better control over Kindle library

## Testing

Comprehensive test coverage in `tests/test_sync_skip_deletion.py`:

- `test_sync_book_skips_already_synced`: Verify already-synced books are skipped
- `test_sync_book_force_resync`: Verify force_resync bypasses skip
- `test_sync_book_new_book_not_skipped`: Verify new books are still synced
- `test_mark_book_for_deletion`: Verify marking for deletion works
- `test_delete_marked_books`: Verify deletion process completes
- `test_force_resync_book`: Verify convenience method works
- `test_kindlemetadata_marked_for_deletion_field`: Verify metadata field exists

All 7 tests pass.

## User Experience Improvements

### Before

- User selects 10 books to sync
- Waits for 10 books to download, convert, and send (even if 3 already exist)
- No way to manage Kindle library from app

### After

- User selects 10 books to sync
- Only 7 actually download/convert/send (3 already exist, skipped automatically)
- Can see which books are on device with green badge
- Can right-click any device-synced book to delete or re-sync
- Faster sync operations, less network usage, more control

## Example Workflow

1. First sync session:
   - Select books A, B, C for sync
   - All three download, convert, send (~5 minutes)
2. Second sync session:
   - Select books A, B, C, D, E for sync
   - A, B, C are skipped (already on device)
   - Only D, E download, convert, send (~2 minutes)
   - User gets "WANTED · ON DEVICE" badge for A, B, C
3. Delete book B:
   - Right-click book B → "Delete from Kindle"
   - Confirm deletion
   - Book B file removed from Kindle
   - Metadata updated
4. Re-sync book A:
   - Right-click book A → "Re-Sync to Kindle"
   - Confirm re-sync
   - Book A re-downloaded and re-sent
   - Sync date updated in metadata

## Error Handling

If a delete operation fails (file not found, permission denied, etc.):

- User sees error dialog "Failed to delete {book title}"
- Metadata is still updated (marked_for_deletion=True)
- Manual deletion can be attempted via Kindle file manager

If Kindle disconnects during a delete:

- Delete operation fails gracefully
- User is prompted to reconnect and try again
- No partial state is left behind

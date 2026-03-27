# KCC Command Discovery Fix

## Problem

When converting comics, users were getting the error:

```
Error converting Delicious in Dungeon v01: KCC conversion failed: /Users/easun/Documents/Code/hearth/.venv/bin/python: No module named comic2ebook
```

This happened because the KCC command discovery was incorrectly prioritizing the Python module fallback (`python -m comic2ebook`) over finding the actual CLI binary (`kcc-c2e.py` or `kcc-c2e`).

## Root Cause

The `_find_kcc_command()` method in `hearth/converters/kcc.py` had two issues:

1. **CLI binary search was not validating**: It would return the first binary found without confirming it's actually KCC
2. **Module fallback was prioritized**: Python module lookup was happening after CLI search, but for cases where `shutil.which()` failed to find the binary, it would select a module that doesn't actually work

According to KCC documentation, the proper way to invoke it is via the `kcc-c2e.py` CLI script, not the Python module.

## Solution

### Changed Discovery Order

**Before**: Check CLI binaries (without validation) → Try Python modules → Fall back to `kcc` shim

**After**:

1. Check CLI binaries with validation: `kcc-c2e`, `kcc-c2e.py`, `comic2ebook`
2. Only if no CLI binary works, try Python modules
3. Last resort: try `kcc` shim (with Kerberos rejection)

### Improved Validation

Updated `_is_kcc_command()` to:

- Check return code (must be 0)
- Look for "comic" or "kindle" in output (positive match)
- Reject "heimdal" or "kerberos" (macOS false positive)
- Be more lenient with other KCC builds

### Better Error Reporting

When KCC conversion fails, the error message now includes:

- Which command was attempted
- The actual error output from KCC
- Helps users debug issues faster

## Changes Made

### `hearth/converters/kcc.py`

1. **`_find_kcc_command()`** - Reordered to prefer CLI binaries, validate before returning, only fall back to modules if needed

2. **`_is_kcc_command()`** - More robust validation:
   - Check return code is 0
   - Reject on timeout
   - More specific error rejection
   - Better handling of different KCC builds

3. **Error handling** - Improved error messages showing which command was attempted

4. **Debug logging** - Added log message showing which KCC command is being used

### New Test File

Created `tests/test_kcc_discovery.py` with 7 tests verifying:

- CLI binaries preferred over modules
- Fall back to modules only if no CLI found
- Commands validated before use
- Invalid commands rejected
- Heimdal/Kerberos rejected
- Comic-related output accepted
- Non-zero returns rejected

## Impact

After this fix:

- ✅ Comics will use proper `kcc-c2e` or `kcc-c2e.py` CLI tool
- ✅ Better error messages if KCC is misconfigured
- ✅ More robust command validation
- ✅ Comprehensive test coverage for command discovery

## Testing

All 7 new tests in `test_kcc_discovery.py` pass:

- ✅ CLI binary preference verified
- ✅ Module fallback verified
- ✅ Validation logic verified
- ✅ Error handling verified

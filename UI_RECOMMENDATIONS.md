# UI Issues From Testing

Status legend: DONE, PARTIAL, NOT DONE

- [DONE] Start user on "Library" tab, not "Settings"
  - Implemented by changing tab order and label in `hearth/gui/main_window.py`.
- Kindle file browser
  - [DONE] By default collapse all folders but leave "Hearth" expanded
    - Implemented in `hearth/gui/kindle_files_page.py`.
  - [DONE] Give options to drag files out (download to computer) or delete from Kindle
    - Implemented with Download/Delete toolbar actions in
      `hearth/gui/kindle_files_page.py`.
- Library Tab (fka "Sync" tab)
  - [DONE] Expand the name column to be wider
  - [DONE] Add "Last Synced" column showing date of last successful sync to Kindle
  - [DONE] Add "On Device" column showing checkmark if book is on Kindle
  - [DONE] Add "Actions" column with buttons to "Re-Sync" or "Delete from Kindle"
    - Context-menu actions are still present as fallback.
  - [PARTIAL] Present books in a grid with cover thumbnails instead of a list, and allow sorting by title/author/date added/etc.
    - [DONE] Allow switching between list and grid view
      - Implemented List/Grid toggle and cover tile view in
        `hearth/gui/sync_page.py`.
    - [PARTIAL] Cover thumbnails are shown in grid (OPDS image where
      available, generated fallback cover otherwise). Sorting controls are not
      yet exposed in UI.
  - [DONE] Add a loading popup/spinner on initial loading + syncing, and disable UI interactions until loading is complete. Also add progress bar for syncing if it takes more than a few seconds.
    - Implemented with in-window blocking overlay and determinate sync progress
      in `hearth/gui/sync_page.py`.
    - Overlay now adapts card/scrim/text styling from the active Qt palette to
      follow system light/dark mode.
- Settings Tab
  - Comic converter
    - [DONE] Add option to specify device - but if Kindle is connected on boot auto-detect and use that
      - Implemented in `hearth/gui/settings_page.py` with device profile selector
        (KS/KPW/KV/KOA) persisted in conversion settings schema.
    - [DONE] Add option for LTR/RTL, an option to either automatically detect manga based on metadata, or to manually specify all as LTR/RTL
      - Implemented with reading direction mode selector (auto/ltr/rtl) and
        manga auto-detect toggle in settings, wired through converter routing
        in `hearth/converters/manager.py` and KCC invocation.
- Syncing
  - [DONE] Provide popup on sync completion showing summary of what was synced, what failed, and any errors encountered. Also provide an option to eject Kindle.
    - Eject behavior releases Hearth's active Kindle connection/session.

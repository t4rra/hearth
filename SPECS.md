# Hearth

Sync your Kindle like an iPod, connecting to an OPDS server to sync your library.

## Features

- One-way sync from OPDS server to Kindle, with the server as the source of truth
- Sync both individual books and whole collections to the Kindle,
- Auto convert books to Kindle format (MOBI) if needed when syncing to the device
- Keep a Hearth metadata file on the Kindle to reliably match books between the device and OPDS server
- Macos support, but built with cross-platform compatibility in mind
- Simple GUI library used, with little performance impact
- A settings page allowing users to configure their OPDS server URL, conversion options (both comics and non-comics), and other preferences you see fit

## Issues

- Newer Kindle models use MTP instead of USB Mass Storage, which may require additional setup to access the file system.
  - [Found a package wrapping go-mtpfs that may help with this](https://github.com/ganeshrvel/go-mtpx)
  - I will be testing this with a Scribe Gen 1, which only supports MTP, so I will be able to ensure it works with that model at least.
- Kindle doesn't support EPUB, so Hearth will need to convert books to its own.
  - It should be using KCC (Kindle Comic Converter) for comics, which is open source and has a [command line interface](https://github.com/ciromattia/kcc?tab=readme-ov-file#standalone-kcc-c2epy-usage) 
  - it should default to the settings best for a Kindle Scribe Gen 1 for now
- For non-comic formats, I can use the [calibre ebook-convert](https://manual.calibre-ebook.com/generated/en/ebook-convert.html) command line tool, which supports a wide range of formats and is widely used in the ebook community. It can be installed separately and called from Hearth when needed.
  - on mac, the command line tool can be accessed at /Applications/calibre.app/Contents/MacOS/ebook-convert
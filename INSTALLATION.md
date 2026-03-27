# Hearth - Installation Guide for macOS

This guide provides step-by-step instructions to install and run Hearth on macOS, assuming you don't have Python or other dependencies pre-installed.

## Prerequisites

- macOS 10.14 or later
- An active internet connection
- Administrator access to install software
- A Kindle device (USB-connected or MTP-enabled)

## Installation Steps

### Step 1: Install Homebrew (Package Manager)

Homebrew is a package manager for macOS that makes it easy to install tools and software.

1. Open Terminal (press `Cmd + Space`, type "Terminal", press Enter)
2. Paste this command and press Enter:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

3. Follow the prompts to complete the installation
4. If you see warnings about PATH, run the suggested commands
5. Verify installation by typing: `brew --version`

### Step 2: Install Python 3

1. In Terminal, run:

```bash
brew install python@3.11
```

2. Verify the installation:

```bash
python3 --version
```

This should show version 3.11.x or higher.

### Step 3: Install Calibre (EBook Conversion)

Calibre is required for converting EPUB and other ebook formats to Kindle-compatible formats (MOBI, AZW3).

1. In Terminal, run:

```bash
brew install calibre
```

2. Verify by checking: `ebook-convert --version`

### Step 4: Install KCC (Comic Converter)

KCC (Kindle Comic Converter) is required for converting CBZ (comic) files to Kindle format.

1. Install via pip:

```bash
pip3 install KindleComicConverter
```

2. If this fails, try the alternative method using Homebrew:

```bash
brew tap scott0107/personal
brew install kcc
```

### Step 5: Install MTP Backend (Recommended for modern Kindle)

Newer Kindles (including Scribe) often use MTP and do not appear in `/Volumes`
until an MTP backend mounts them.

Install at least one backend tool (Hearth will auto-detect whichever is available):

```bash
# Option 1 (preferred when available)
brew install go-mtpfs

# Option 2
brew install simple-mtpfs

# Option 3
brew install jmtpfs
```

If you have `go-mtpx` from the Go package ecosystem, Hearth can use it too.

You can skip manual backend setup entirely by enabling
"Auto-install MTP backend" in Hearth Settings.

### Step 6: Clone or Download Hearth

Choose one of these options:

#### Option A: Clone with Git (Recommended)

```bash
git clone https://github.com/yourusername/hearth.git
cd hearth
```

#### Option B: Download ZIP file

1. Visit the Hearth repository page
2. Click "Code" → "Download ZIP"
3. Extract the ZIP file to your desired location
4. In Terminal, navigate to the extracted folder: `cd ~/Downloads/hearth-main`

### Step 7: Create a Virtual Environment (Recommended)

A virtual environment keeps Hearth's dependencies separate from your system Python.

1. Navigate to the Hearth directory:

```bash
cd ~/path/to/hearth
```

2. Create a virtual environment:

```bash
python3 -m venv venv
```

3. Activate it:

```bash
source venv/bin/activate
```

You should see `(venv)` at the start of your Terminal prompt.

### Step 8: Install Python Dependencies

With the virtual environment activated (you see `(venv)` in the prompt):

```bash
pip install -r requirements.txt
```

This installs:

- PyQt6 (GUI framework)
- requests (HTTP library)
- feedparser (OPDS feed parser)
- python-magic (file type detection)
- pydantic (data validation)

### Step 9: Configure Hearth

First run creates the configuration file:

```bash
python main.py
```

This will:

1. Launch the Hearth GUI
2. Create `~/.config/hearth/settings.json` configuration file
3. Create `Documents/Hearth/` folder on your Kindle device (when connected)

### Step 10: First-Time Setup in GUI

1. When Hearth opens, go to **Settings** tab
2. Enter your OPDS Server URL (e.g., `https://opds.example.com`)
3. If needed, set OPDS authentication:
   - `none` for public OPDS catalogs
   - `basic` and enter username/password
   - `bearer` and enter token
4. For Kindle:
   - Try **Auto-Detect** first (works for USB and MTP)
   - Keep "Auto-mount MTP Kindle" enabled for MTP devices
   - Enable "Auto-install MTP backend" to avoid manual setup
   - Optionally choose a specific MTP tool backend
5. Click **Save Settings**

## Running Hearth

### Day-to-Day Usage

After initial setup, starting Hearth is simple:

#### With Virtual Environment (Recommended):

```bash
cd ~/path/to/hearth
source venv/bin/activate
python main.py
```

#### Without Virtual Environment:

```bash
cd ~/path/to/hearth
python3 main.py
```

### Creating a Shortcut (Optional)

Create a shell script to start Hearth with one click:

1. Create a file `start_hearth.sh` in your Hearth directory:

```bash
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python main.py
```

2. Make it executable:

```bash
chmod +x start_hearth.sh
```

3. Now you can run Hearth by double-clicking `start_hearth.sh` or typing `./start_hearth.sh` in Terminal

## Troubleshooting

### Python Command Not Found

If `python3` doesn't work after installing Homebrew Python:

```bash
which python3
```

If this returns nothing, add to `~/.zshrc`:

```bash
export PATH="/usr/local/opt/python@3.11/libexec/bin:$PATH"
```

Then reload: `source ~/.zshrc`

### Calibre Not Found

If ebook conversion fails:

```bash
which ebook-convert
```

If not found, try installing via the GUI:

```bash
brew install --cask calibre
```

### KCC Not Found

If comic conversion fails, install from source:

```bash
pip install --upgrade KindleComicConverter
```

Or verify it's in your path:

```bash
which kcc.py
```

### Virtual Environment Issues

Reset and recreate:

```bash
cd ~/path/to/hearth
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Kindle Not Detected

1. Connect Kindle via USB
2. On Kindle, select "Connect" option
3. In Hearth Settings, click **Auto-Detect**
4. If your Kindle is MTP-only and not in `/Volumes`, install one MTP backend
   (`go-mtpfs`, `simple-mtpfs`, `jmtpfs`, or `go-mtpx`) and retry
5. If needed, manually set the mount path once the backend mounts it

### Permission Denied Errors

If you get permission errors when accessing the Kindle:

```bash
# Check permissions
ls -l /Volumes/Kindle

# If needed, fix permissions
sudo chmod -R 755 /Volumes/Kindle
```

## System Requirements

- **Disk Space**: ~2 GB for installation and converted files
- **RAM**: 2 GB minimum, 4 GB recommended
- **Python**: 3.8 or higher (3.11 recommended)
- **Calibre**: Latest version
- **KCC**: Latest version

## macOS Version Compatibility

- **Supported**: macOS 10.14 (Mojave) and later
- **Tested On**: macOS 11 (Big Sur), 12 (Monterey), 13 (Ventura), 14 (Sonoma)

## Uninstallation

To completely remove Hearth:

```bash
# Remove the Hearth directory
rm -rf ~/path/to/hearth

# Remove configuration (optional)
rm -rf ~/.config/hearth

# Remove Python dependencies (if using virtual environment)
# - No additional cleanup needed, just delete the directory
```

To uninstall dependencies system-wide:

```bash
# Remove Calibre
brew uninstall calibre

# Remove KCC
pip uninstall KindleComicConverter

# Remove Homebrew Python (optional)
brew uninstall python@3.11
```

## Getting Help

If you encounter issues:

1. Check this troubleshooting section
2. Review the README.md for feature documentation
3. Check system logs:

```bash
# Python errors
python3 main.py 2>&1 | head -50

# Calibre logs
ebook-convert --debug-pipelines
```

## Next Steps

After installation:

1. Read [README.md](README.md) for feature overview
2. Configure your OPDS server URL in Settings
3. Connect your Kindle device
4. Browse collections and sync books

Enjoy managing your digital library with Hearth!

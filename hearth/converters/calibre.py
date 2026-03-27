"""EBook converter using Calibre's ebook-convert."""
import subprocess
import shutil
from pathlib import Path
from typing import Optional
import platform

from .base import BaseConverter, ConversionFormat, ConversionResult


class CalibreConverter(BaseConverter):
    """Converter for ebook formats using Calibre's ebook-convert."""
    
    # Supported input formats
    SUPPORTED_FORMATS = ['.epub', '.mobi', '.azw', '.azw3', '.pdf', '.txt', '.html', '.doc', '.docx']
    
    def __init__(self, output_dir: Optional[Path] = None, keep_original: bool = True):
        super().__init__(output_dir, keep_original)
        self.ebook_convert_path = self._find_ebook_convert()
    
    def _find_ebook_convert(self) -> Optional[str]:
        """Find the ebook-convert command."""
        system = platform.system()
        
        # Try to find in PATH first
        result = shutil.which('ebook-convert')
        if result:
            return result
        
        # Platform-specific paths
        if system == 'Darwin':  # macOS
            macos_path = '/Applications/calibre.app/Contents/MacOS/ebook-convert'
            if Path(macos_path).exists():
                return macos_path
            # Also try /opt/homebrew for M1 Macs
            homebrew_path = '/opt/homebrew/bin/ebook-convert'
            if Path(homebrew_path).exists():
                return homebrew_path
        
        elif system == 'Windows':
            windows_paths = [
                r'C:\Program Files\Calibre2\ebook-convert.exe',
                r'C:\Program Files (x86)\Calibre2\ebook-convert.exe'
            ]
            for path in windows_paths:
                if Path(path).exists():
                    return path
        
        elif system == 'Linux':
            linux_path = '/opt/calibre/ebook-convert'
            if Path(linux_path).exists():
                return linux_path
        
        return None
    
    def _check_calibre_installed(self) -> bool:
        """Check if ebook-convert is available."""
        if not self.ebook_convert_path:
            self._log_progress("Warning: Calibre not found. Install with: brew install calibre (macOS)")
            return False
        return True
    
    def can_convert(self, input_path: Path) -> bool:
        """Check if file is a supported ebook format."""
        return input_path.suffix.lower() in self.SUPPORTED_FORMATS
    
    def get_supported_formats(self) -> list[str]:
        """Return list of supported input formats."""
        return self.SUPPORTED_FORMATS
    
    def convert(self, input_path: Path, output_format: ConversionFormat = ConversionFormat.MOBI) -> ConversionResult:
        """Convert ebook to specified format using Calibre."""
        self._log_progress(f"Starting ebook conversion: {input_path.name}")
        
        if not input_path.exists():
            return ConversionResult(False, error=f"Input file not found: {input_path}")
        
        if not self.can_convert(input_path):
            return ConversionResult(False, error=f"Unsupported ebook format: {input_path.suffix}")
        
        if not self._check_calibre_installed():
            return ConversionResult(False, error="Calibre ebook-convert not found on system")
        
        # Prepare output
        output_name = input_path.stem + "." + output_format.value
        output_path = self.output_dir / output_name
        
        try:
            # Build calibre command
            cmd = [self.ebook_convert_path, str(input_path), str(output_path)]
            
            # Add Kindle-specific options for Scribe Gen 1
            if output_format == ConversionFormat.MOBI:
                cmd.extend([
                    '--output-profile=kindle_scribe',
                    '--paper-size=a4',
                    '--margin-top=0.2',
                    '--margin-bottom=0.2',
                    '--margin-left=0.2',
                    '--margin-right=0.2'
                ])
            
            self._log_progress(f"Running ebook-convert: {' '.join(cmd)}")
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                return ConversionResult(False, error=f"Calibre conversion failed: {error_msg}")
            
            if not output_path.exists():
                return ConversionResult(False, error="Output file was not created by calibre")
            
            self._log_progress(f"Ebook conversion successful: {output_path.name}")
            
            # Remove original if requested
            if not self.keep_original:
                input_path.unlink()
                self._log_progress(f"Removed original: {input_path.name}")
            
            return ConversionResult(True, output_path=output_path)
        
        except subprocess.TimeoutExpired:
            return ConversionResult(False, error="Calibre conversion timed out after 5 minutes")
        except Exception as e:
            return ConversionResult(False, error=f"Unexpected error during ebook conversion: {str(e)}")

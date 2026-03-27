"""Comic converter using KCC (Kindle Comic Converter)."""
import subprocess
import shutil
from pathlib import Path
from typing import Optional
import tempfile

from .base import BaseConverter, ConversionFormat, ConversionResult


class KCCConverter(BaseConverter):
    """Converter for comic formats using Kindle Comic Converter."""
    
    # Supported comic formats
    SUPPORTED_FORMATS = ['.cbz', '.cbr', '.cb7', '.cbt', '.zip', '.rar']
    
    def __init__(self, output_dir: Optional[Path] = None, keep_original: bool = True, 
                 quality: str = "high", remove_margins: bool = True):
        super().__init__(output_dir, keep_original)
        self.quality = quality
        self.remove_margins = remove_margins
        self._check_kcc_installed()
    
    def _check_kcc_installed(self) -> bool:
        """Check if KCC is installed and accessible."""
        try:
            result = subprocess.run(['kcc', '--version'], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            self._log_progress("Warning: KCC not found. Install with: pip install kcc-comic2ebook")
            return False
    
    def can_convert(self, input_path: Path) -> bool:
        """Check if file is a supported comic format."""
        return input_path.suffix.lower() in self.SUPPORTED_FORMATS
    
    def get_supported_formats(self) -> list[str]:
        """Return list of supported input formats."""
        return self.SUPPORTED_FORMATS
    
    def convert(self, input_path: Path, output_format: ConversionFormat = ConversionFormat.MOBI) -> ConversionResult:
        """Convert comic to Kindle format using KCC."""
        self._log_progress(f"Starting comic conversion: {input_path.name}")
        
        if not input_path.exists():
            return ConversionResult(False, error=f"Input file not found: {input_path}")
        
        if not self.can_convert(input_path):
            return ConversionResult(False, error=f"Unsupported comic format: {input_path.suffix}")
        
        # Prepare output
        output_name = input_path.stem + "." + output_format.value
        output_path = self.output_dir / output_name
        
        try:
            # Build KCC command
            cmd = ['kcc', '-p', 'KS', '-o', str(output_path)]
            
            # Add quality settings
            if self.quality == "high":
                cmd.extend(['-u', '1.0'])
            elif self.quality == "medium":
                cmd.extend(['-u', '0.8'])
            
            # Add margin removal
            if self.remove_margins:
                cmd.append('-m')
            
            cmd.append(str(input_path))
            
            self._log_progress(f"Running KCC: {' '.join(cmd)}")
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout
                return ConversionResult(False, error=f"KCC conversion failed: {error_msg}")
            
            if not output_path.exists():
                return ConversionResult(False, error="Output file was not created by KCC")
            
            self._log_progress(f"Comic conversion successful: {output_path.name}")
            
            # Remove original if requested
            if not self.keep_original:
                input_path.unlink()
                self._log_progress(f"Removed original: {input_path.name}")
            
            return ConversionResult(True, output_path=output_path)
        
        except subprocess.TimeoutExpired:
            return ConversionResult(False, error="KCC conversion timed out after 5 minutes")
        except Exception as e:
            return ConversionResult(False, error=f"Unexpected error during comic conversion: {str(e)}")

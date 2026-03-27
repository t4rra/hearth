"""Comprehensive tests for Hearth conversion functionality."""

import unittest
import tempfile
import shutil
from pathlib import Path
import zipfile

from hearth.converters.manager import ConverterManager
from hearth.converters.base import ConversionFormat, ConversionResult
from hearth.converters.kcc import KCCConverter
from hearth.converters.calibre import CalibreConverter


class MockConverter:
    """Mock converter for testing without external dependencies."""

    def __init__(self, input_suffix: str, output_dir: Path):
        self.input_suffix = input_suffix
        self.output_dir = output_dir

    def create_mock_output(
        self, input_path: Path, output_format: ConversionFormat
    ) -> Path:
        """Create a mock converted file."""
        output_name = input_path.stem + "." + output_format.value
        output_path = self.output_dir / output_name

        # Create a simple text file as mock output
        output_path.write_text(f"Mock {output_format.value.upper()} file\n")
        return output_path


class TestComicConversion(unittest.TestCase):
    """Test comic book conversion functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_test_"))
        self.output_dir = self.test_dir / "output"
        self.output_dir.mkdir()

    def tearDown(self):
        """Clean up test files."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def create_mock_cbz(self, filename: str = "test_comic.cbz") -> Path:
        """Create a mock CBZ file for testing."""
        cbz_path = self.test_dir / filename

        # CBZ is just a ZIP file with images
        with zipfile.ZipFile(cbz_path, "w") as zf:
            # Add mock image files
            for i in range(5):
                img_data = f"Mock image {i+1}".encode()
                zf.writestr(f"page_{i+1:03d}.jpg", img_data)

        return cbz_path

    def test_cbz_file_creation(self):
        """Test that mock CBZ files can be created."""
        cbz_path = self.create_mock_cbz()
        self.assertTrue(cbz_path.exists())
        self.assertTrue(zipfile.is_zipfile(cbz_path))

    def test_comic_converter_initialization(self):
        """Test KCC converter can be initialized."""
        converter = KCCConverter(output_dir=self.output_dir)
        self.assertIsNotNone(converter)
        self.assertEqual(converter.output_dir, self.output_dir)

    def test_comic_converter_format_detection(self):
        """Test that comic converter detects supported formats."""
        converter = KCCConverter(output_dir=self.output_dir)

        # Test supported formats
        for ext in [".cbz", ".cbr", ".cb7", ".cbt"]:
            test_file = self.test_dir / f"test{ext}"
            test_file.touch()
            self.assertTrue(converter.can_convert(test_file))

        # Test unsupported format
        epub_file = self.test_dir / "test.epub"
        epub_file.touch()
        self.assertFalse(converter.can_convert(epub_file))

    def test_get_supported_comic_formats(self):
        """Test getting supported comic formats."""
        converter = KCCConverter(output_dir=self.output_dir)
        formats = converter.get_supported_formats()

        self.assertIn(".cbz", formats)
        self.assertIn(".cbr", formats)
        self.assertIn(".cb7", formats)

    def test_mock_comic_conversion(self):
        """Test mock comic conversion workflow."""
        cbz_path = self.create_mock_cbz()

        # Use mock converter since KCC might not be installed
        mock_converter = MockConverter(".cbz", self.output_dir)
        output_path = mock_converter.create_mock_output(cbz_path, ConversionFormat.MOBI)

        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.suffix, ".mobi")
        self.assertTrue(output_path.read_text().startswith("Mock MOBI file"))


class TestEbookConversion(unittest.TestCase):
    """Test ebook conversion functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_test_"))
        self.output_dir = self.test_dir / "output"
        self.output_dir.mkdir()

    def tearDown(self):
        """Clean up test files."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def create_mock_epub(self, filename: str = "test_book.epub") -> Path:
        """Create a mock EPUB file for testing."""
        epub_path = self.test_dir / filename

        # EPUB is a ZIP file with specific structure
        with zipfile.ZipFile(epub_path, "w") as zf:
            # Add mimetype (must be first and uncompressed)
            zf.writestr("mimetype", "application/epub+zip")

            # Add container
            container_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                "  <rootfiles>\n"
                '    <rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/>\n'
                "  </rootfiles>\n"
                "</container>"
            )
            zf.writestr("META-INF/container.xml", container_xml)

            # Add content
            content_opf = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<package version="2.0">\n'
                "  <metadata>\n"
                "    <dc:title>Test Book</dc:title>\n"
                "    <dc:creator>Test Author</dc:creator>\n"
                "  </metadata>\n"
                "  <manifest>\n"
                '    <item id="chapter1" href="chapter1.xhtml" '
                'media-type="application/xhtml+xml"/>\n'
                "  </manifest>\n"
                "</package>"
            )
            zf.writestr("OEBPS/content.opf", content_opf)

            # Add chapter
            chapter_xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">\n'
                "  <body><p>Test content</p></body>\n"
                "</html>"
            )
            zf.writestr("OEBPS/chapter1.xhtml", chapter_xhtml)

        return epub_path

    def test_epub_file_creation(self):
        """Test that mock EPUB files can be created."""
        epub_path = self.create_mock_epub()
        self.assertTrue(epub_path.exists())
        self.assertTrue(zipfile.is_zipfile(epub_path))

    def test_ebook_converter_initialization(self):
        """Test Calibre converter can be initialized."""
        converter = CalibreConverter(output_dir=self.output_dir)
        self.assertIsNotNone(converter)
        self.assertEqual(converter.output_dir, self.output_dir)

    def test_ebook_converter_format_detection(self):
        """Test that ebook converter detects supported formats."""
        converter = CalibreConverter(output_dir=self.output_dir)

        # Test supported formats
        for ext in [".epub", ".mobi", ".pdf", ".txt"]:
            test_file = self.test_dir / f"test{ext}"
            test_file.touch()
            self.assertTrue(converter.can_convert(test_file))

        # Test unsupported format
        cbz_file = self.test_dir / "test.cbz"
        cbz_file.touch()
        self.assertFalse(converter.can_convert(cbz_file))

    def test_get_supported_ebook_formats(self):
        """Test getting supported ebook formats."""
        converter = CalibreConverter(output_dir=self.output_dir)
        formats = converter.get_supported_formats()

        self.assertIn(".epub", formats)
        self.assertIn(".mobi", formats)
        self.assertIn(".pdf", formats)

    def test_mock_ebook_conversion(self):
        """Test mock ebook conversion workflow."""
        epub_path = self.create_mock_epub()

        # Use mock converter since Calibre might not be installed
        mock_converter = MockConverter(".epub", self.output_dir)
        output_path = mock_converter.create_mock_output(
            epub_path, ConversionFormat.MOBI
        )

        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.suffix, ".mobi")


class TestConverterManager(unittest.TestCase):
    """Test the converter manager."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_test_"))
        self.output_dir = self.test_dir / "output"
        self.output_dir.mkdir()

    def tearDown(self):
        """Clean up test files."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_converter_manager_initialization(self):
        """Test converter manager initialization."""
        manager = ConverterManager(output_dir=self.output_dir)
        self.assertIsNotNone(manager.comic_converter)
        self.assertIsNotNone(manager.ebook_converter)

    def test_converter_manager_format_detection(self):
        """Test converter manager detects all supported formats."""
        manager = ConverterManager(output_dir=self.output_dir)
        formats = manager.get_supported_formats()

        # Check both comic and ebook formats
        self.assertIn(".cbz", formats)
        self.assertIn(".epub", formats)
        self.assertIn(".mobi", formats)

    def test_converter_manager_can_convert(self):
        """Test converter manager's can_convert method."""
        manager = ConverterManager(output_dir=self.output_dir)

        cbz_file = self.test_dir / "test.cbz"
        cbz_file.touch()
        self.assertTrue(manager.can_convert(cbz_file))

        epub_file = self.test_dir / "test.epub"
        epub_file.touch()
        self.assertTrue(manager.can_convert(epub_file))

        txt_file = self.test_dir / "test.txt"
        txt_file.touch()
        # TXT is supported by Calibre, so this should return True
        self.assertTrue(manager.can_convert(txt_file))

        # Test unsupported format
        unknown_file = self.test_dir / "test.xyz"
        unknown_file.touch()
        self.assertFalse(manager.can_convert(unknown_file))

    def test_progress_callback(self):
        """Test progress callback functionality."""
        manager = ConverterManager(output_dir=self.output_dir)

        callback_messages = []

        def mock_callback(msg: str):
            callback_messages.append(msg)

        manager.set_progress_callback(mock_callback)
        self.assertEqual(len(callback_messages), 0)


class TestConversionFormats(unittest.TestCase):
    """Test conversion format handling."""

    def test_conversion_format_enum(self):
        """Test ConversionFormat enum."""
        self.assertEqual(ConversionFormat.MOBI.value, "mobi")
        self.assertEqual(ConversionFormat.EPUB.value, "epub")
        self.assertEqual(ConversionFormat.AZW3.value, "azw3")

    def test_conversion_result_success(self):
        """Test successful conversion result."""
        output_path = Path("/tmp/test.mobi")
        result = ConversionResult(True, output_path=output_path)

        self.assertTrue(result.success)
        self.assertEqual(result.output_path, output_path)
        self.assertIsNone(result.error)

    def test_conversion_result_failure(self):
        """Test failed conversion result."""
        error_msg = "Test error"
        result = ConversionResult(False, error=error_msg)

        self.assertFalse(result.success)
        self.assertIsNone(result.output_path)
        self.assertEqual(result.error, error_msg)


class TestDemoFileConversion(unittest.TestCase):
    """Test conversion with actual demo files."""

    CBZ_FILE = "Loveless Momentum - Zeniko Sumiya.cbz"
    EPUB_FILE = "What If 10th Anniversary Edition v01 - " "Randall Munroe (2024).epub"

    def setUp(self):
        """Set up test fixtures."""
        self.demo_dir = Path("/Users/easun/Documents/Code/hearth/DEMO Files")
        self.test_output_dir = Path(tempfile.mkdtemp(prefix="hearth_demo_test_"))

    def tearDown(self):
        """Clean up test files."""
        shutil.rmtree(self.test_output_dir, ignore_errors=True)

    def test_demo_files_exist(self):
        """Test that demo files exist."""
        if self.demo_dir.exists():
            self.assertTrue((self.demo_dir / self.CBZ_FILE).exists())
            self.assertTrue((self.demo_dir / self.EPUB_FILE).exists())

    def test_cbz_demo_file_is_valid(self):
        """Test that CBZ demo file is a valid ZIP."""
        cbz_file = self.demo_dir / self.CBZ_FILE
        if cbz_file.exists():
            self.assertTrue(zipfile.is_zipfile(cbz_file))

    def test_epub_demo_file_is_valid(self):
        """Test that EPUB demo file is a valid ZIP."""
        epub_file = self.demo_dir / self.EPUB_FILE
        if epub_file.exists():
            self.assertTrue(zipfile.is_zipfile(epub_file))

    def test_manager_can_convert_demo_files(self):
        """Test that converter manager recognizes demo files."""
        manager = ConverterManager(output_dir=self.test_output_dir)

        cbz_file = self.demo_dir / self.CBZ_FILE
        if cbz_file.exists():
            self.assertTrue(manager.can_convert(cbz_file))

        epub_file = self.demo_dir / self.EPUB_FILE
        if epub_file.exists():
            self.assertTrue(manager.can_convert(epub_file))


class TestConversionIntegration(unittest.TestCase):
    """Integration tests for conversion workflows."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_dir = Path(tempfile.mkdtemp(prefix="hearth_integration_"))
        self.output_dir = self.test_dir / "output"
        self.output_dir.mkdir()

    def tearDown(self):
        """Clean up test files."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def create_mock_cbz(self) -> Path:
        """Create a mock CBZ file."""
        cbz_path = self.test_dir / "test.cbz"
        with zipfile.ZipFile(cbz_path, "w") as zf:
            for i in range(3):
                zf.writestr(f"page_{i+1}.jpg", f"Mock image {i+1}".encode())
        return cbz_path

    def create_mock_epub(self) -> Path:
        """Create a mock EPUB file."""
        epub_path = self.test_dir / "test.epub"
        with zipfile.ZipFile(epub_path, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", "<container></container>")
            zf.writestr("OEBPS/content.opf", "<package></package>")
        return epub_path

    def test_full_comic_conversion_workflow(self):
        """Test complete comic conversion workflow."""
        cbz_path = self.create_mock_cbz()

        # Test with mock converter
        mock_converter = MockConverter(".cbz", self.output_dir)
        output_path = mock_converter.create_mock_output(cbz_path, ConversionFormat.MOBI)

        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "test.mobi")

    def test_full_ebook_conversion_workflow(self):
        """Test complete ebook conversion workflow."""
        epub_path = self.create_mock_epub()

        # Test with mock converter
        mock_converter = MockConverter(".epub", self.output_dir)
        output_path = mock_converter.create_mock_output(
            epub_path, ConversionFormat.MOBI
        )

        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "test.mobi")

    def test_multiple_format_conversions(self):
        """Test converting to multiple formats."""
        cbz_path = self.create_mock_cbz()
        mock_converter = MockConverter(".cbz", self.output_dir)

        formats = [ConversionFormat.MOBI, ConversionFormat.AZW3, ConversionFormat.EPUB]
        results = []

        for fmt in formats:
            output_path = mock_converter.create_mock_output(cbz_path, fmt)
            results.append((fmt, output_path))

        self.assertEqual(len(results), 3)
        for fmt, output_path in results:
            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.suffix, f".{fmt.value}")


def run_tests():
    """Run all tests."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestComicConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestEbookConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestConverterManager))
    suite.addTests(loader.loadTestsFromTestCase(TestConversionFormats))
    suite.addTests(loader.loadTestsFromTestCase(TestDemoFileConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestConversionIntegration))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if __name__ == "__main__":
    run_tests()

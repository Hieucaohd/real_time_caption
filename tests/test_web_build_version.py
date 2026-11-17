"""Verify the build tag survives embedding in a real Windows executable."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('web_exe_builder', Path(__file__).resolve().parents[1] / 'packaging/build_web_exe.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@unittest.skipUnless(os.name == 'nt' and importlib.util.find_spec('PyInstaller'), 'Windows and PyInstaller required')
class WebBuildVersionTests(unittest.TestCase):
    def test_tag_embedded_in_executable_matches_output_name(self):
        from PyInstaller.utils.win32 import versioninfo
        tag = 'v1.2.3'
        with tempfile.TemporaryDirectory(prefix='rtc version ') as folder:
            folder = Path(folder)
            resource = folder / 'version-info.txt'
            builder.write_version_info(tag, resource)
            args = builder.pyinstaller_args(tag, resource)
            name = args[args.index('--name') + 1]
            self.assertEqual(name, 'RealTimeCaptionWeb-' + tag)
            self.assertEqual(args[args.index('--version-file') + 1], str(resource))
            executable = folder / (name + '.exe')
            shutil.copy2(sys.executable, executable)
            info = versioninfo.load_version_info_from_text_file(str(resource))
            versioninfo.write_version_info_to_executable(str(executable), info)
            actual = versioninfo.read_version_info_from_executable(str(executable))
            strings = {item.name: item.val for table in actual.kids[0].kids for item in table.kids}
            self.assertEqual(strings['FileVersion'], tag)
            self.assertEqual(strings['ProductVersion'], tag)
            self.assertEqual(strings['OriginalFilename'], executable.name)
            self.assertEqual(actual.ffi.fileVersionMS, (1 << 16) | 2)
            self.assertEqual(actual.ffi.fileVersionLS, 3 << 16)

    def test_unsafe_or_unrepresentable_tags_are_rejected(self):
        for tag in ('../escape', 'v65536.1.2', 'v1.0', 'v1.2.3.exe', 'v01.2.3', 'v1.2.3-beta'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                builder.build_identity(tag)

    def test_four_part_version_matches_windows_numeric_version(self):
        self.assertEqual(builder.build_identity('v1.2.3.65535'), ('RealTimeCaptionWeb-v1.2.3.65535', (1, 2, 3, 65535)))


if __name__ == '__main__':
    unittest.main()

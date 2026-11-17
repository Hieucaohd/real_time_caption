"""Run the actual batch workflow in disposable repos, with packaging tools stubbed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt' and shutil.which('git'), 'Windows batch/Git required')
class WebBuildBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rtc build ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.env = {**os.environ, 'RTC_BUILD_NO_PAUSE': '1', 'RTC_TEST_FAIL': '',
                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory',
                    'GIT_CONFIG_VALUE_0': str(self.root)}
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Build Test')
        self.git('config', 'user.email', 'build-test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'tag.gpgsign', 'false')
        self.script = Path(__file__).resolve().parents[1] / 'build_web_exe.bat'
        shutil.copyfile(self.script, self.root / 'build_web_exe.bat')
        (self.root / '.gitignore').write_text('.venv/\n__pycache__/\nbuild-observed.json\n', encoding='utf-8')
        (self.root / 'web').mkdir()
        (self.root / 'web' / 'package.json').write_text('{}', encoding='utf-8')
        (self.root / 'pip.py').write_text(
            "import os, sys\nsys.exit(1 if os.environ.get('RTC_TEST_FAIL') == 'pip' else 0)\n", encoding='utf-8')
        (self.root / 'packaging').mkdir()
        (self.root / 'packaging' / 'build_web_exe.py').write_text(
            "import json, os, pathlib, subprocess, sys\n"
            "def git(*args): return subprocess.check_output(['git', *args], text=True).strip()\n"
            "pathlib.Path('build-observed.json').write_text(json.dumps({"
            "'branch':git('branch','--show-current'), 'tag':git('describe','--exact-match','--tags'),"
            "'commit':git('rev-parse','HEAD'), 'args':sys.argv[1:]}))\n"
            "sys.exit(1 if os.environ.get('RTC_TEST_FAIL') == 'packaging' else 0)\n", encoding='utf-8')
        tools = Path(self.temp.name) / 'tools'
        tools.mkdir()
        (tools / 'npm.cmd').write_text(
            '@echo off\nif "%RTC_TEST_FAIL%"=="npm-%~1" exit /b 1\nexit /b 0\n', encoding='ascii')
        self.env['PATH'] = str(tools) + os.pathsep + self.env['PATH']
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(self.root / '.venv')], check=True, capture_output=True)
        self.git('add', '.')
        self.git('commit', '-m', 'Fixture')
        self.commit = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, env=self.env, text=True, stderr=subprocess.STDOUT).strip()

    def build(self, fail='', version='1.2.3'):
        return subprocess.run([os.environ['COMSPEC'], '/d', '/c', 'build_web_exe.bat', '--with-translation'],
            cwd=self.root, env={**self.env, 'RTC_TEST_FAIL': fail}, input=version + '\n', capture_output=True, text=True, timeout=45)

    def test_build_uses_annotated_tag_branch_and_returns_to_main(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        observed = json.loads((self.root / 'build-observed.json').read_text())
        self.assertEqual(observed['tag'], 'v1.2.3')
        self.assertEqual(observed['branch'], 'build/' + observed['tag'])
        self.assertEqual(observed['commit'], self.commit)
        self.assertEqual(observed['args'], ['--with-translation', '--build-tag', observed['tag']])
        self.assertIn('RealTimeCaptionWeb-' + observed['tag'] + '.exe', result.stdout)
        self.assertEqual(self.git('cat-file', '-t', 'refs/tags/' + observed['tag']), 'tag')
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_failed_build_steps_all_return_to_main(self):
        for number, stage in enumerate(('pip', 'npm-ci', 'npm-run', 'packaging')):
            with self.subTest(stage=stage):
                result = self.build(stage, version=f'1.0.{number}')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.git('branch', '--show-current'), 'main')
                self.assertIn('Returning to main', result.stdout)

    def test_dirty_worktree_is_preserved_without_creating_tag(self):
        (self.root / 'web' / 'package.json').write_text('{"draft":true}', encoding='utf-8')
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('tag', '--list'), '')
        self.assertEqual((self.root / 'web' / 'package.json').read_text(), '{"draft":true}')
        self.assertEqual(self.git('branch', '--show-current'), 'main')

    def test_cleanup_survives_main_containing_an_older_batch_file(self):
        (self.root / 'build_web_exe.bat').write_text('@echo off\nexit /b 99\n', encoding='ascii')
        self.git('add', 'build_web_exe.bat'); self.git('commit', '-m', 'Older main script')
        self.git('checkout', '-b', 'feature')
        shutil.copyfile(self.script, self.root / 'build_web_exe.bat')
        self.git('add', 'build_web_exe.bat'); self.git('commit', '-m', 'Updated script')
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertIn('exit /b 99', (self.root / 'build_web_exe.bat').read_text())

    def test_optional_prefix_and_four_part_version(self):
        result = self.build(version=' v2.3.4.5 ')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        observed = json.loads((self.root / 'build-observed.json').read_text())
        self.assertEqual(observed['tag'], 'v2.3.4.5')
        self.assertIn('RealTimeCaptionWeb-v2.3.4.5.exe', result.stdout)

    def test_invalid_input_never_creates_tag_or_executes_commands(self):
        for version in ('', '1.2', '01.2.3', '65536.0.0', '1.2.3-beta', '1.2.3" & echo bad>injected.txt & rem "'):
            with self.subTest(version=version):
                result = self.build(version=version)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.git('tag', '--list'), '')
                self.assertEqual(self.git('branch', '--show-current'), 'main')
                self.assertFalse((self.root / 'injected.txt').exists())

    def test_existing_tag_and_branch_are_not_overwritten(self):
        self.git('tag', '-a', 'v1.2.3', '-m', 'Existing release')
        original = self.git('rev-parse', 'refs/tags/v1.2.3')
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already exists', result.stdout)
        self.assertEqual(self.git('rev-parse', 'refs/tags/v1.2.3'), original)
        self.git('branch', 'build/v2.0.0')
        result = self.build(version='2.0.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('already exists', result.stdout)
        self.assertEqual(self.git('tag', '--list', 'v2.0.0'), '')
        self.assertEqual(self.git('branch', '--show-current'), 'main')
        self.assertFalse((self.root / 'build-observed.json').exists())


if __name__ == '__main__':
    unittest.main()

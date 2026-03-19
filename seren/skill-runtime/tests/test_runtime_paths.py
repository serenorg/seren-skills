from __future__ import annotations

import os
import sys
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seren_runtime
from seren_runtime import LegacyRuntimePathWarning, make_runtime_paths

SLUG = "coinbase-smart-dca-bot"


class RuntimePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.skill_root = self.tmp_path / "coinbase" / "smart-dca-bot"
        self.skill_root.mkdir(parents=True)
        self.original_cwd = Path.cwd()
        self.original_env = os.environ.copy()
        (
            self.resolve_config_path,
            self.resolve_env_path,
            self.resolve_runtime_dir,
            self.default_runtime_dir,
            self.load_skill_env,
            self.activate_runtime,
        ) = make_runtime_paths(SLUG, self.skill_root)

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)
        os.environ.clear()
        os.environ.update(self.original_env)
        self.tmp.cleanup()

    # --- default_runtime_dir ---

    def test_prefers_project_runtime_dir(self) -> None:
        project_root = self.tmp_path / "workspace"
        runtime_dir = project_root / ".seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        nested = project_root / "app" / "src"
        nested.mkdir(parents=True)
        os.chdir(nested)

        self.assertEqual(self.default_runtime_dir().resolve(), runtime_dir.resolve())

    def test_falls_back_to_shared_runtime_root(self) -> None:
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        expected = shared_root / "seren" / "skills-data" / SLUG
        self.assertEqual(self.default_runtime_dir(), expected)

    def test_appdata_wins_over_xdg(self) -> None:
        appdata = self.tmp_path / "appdata"
        xdg = self.tmp_path / "xdg"
        os.environ["APPDATA"] = str(appdata)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        os.chdir(self.tmp_path)

        with mock.patch.object(seren_runtime, "_is_windows", return_value=True):
            expected = appdata / "seren" / "skills-data" / SLUG
            self.assertEqual(self.default_runtime_dir(), expected)

    def test_xdg_wins_on_non_windows_even_if_appdata_is_set(self) -> None:
        appdata = self.tmp_path / "appdata"
        xdg = self.tmp_path / "xdg"
        os.environ["APPDATA"] = str(appdata)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        os.chdir(self.tmp_path)

        with mock.patch.object(seren_runtime, "_is_windows", return_value=False):
            expected = xdg / "seren" / "skills-data" / SLUG
            self.assertEqual(self.default_runtime_dir(), expected)

    # --- resolve_config_path ---

    def test_resolve_config_prefers_runtime_dir(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "config.json").write_text("{}", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        self.assertEqual(self.resolve_config_path(), runtime_dir / "config.json")

    def test_resolve_config_accepts_absolute_path(self) -> None:
        explicit = self.tmp_path / "my-config.json"
        explicit.write_text("{}", encoding="utf-8")

        self.assertEqual(self.resolve_config_path(str(explicit)), explicit)

    def test_resolve_config_relative_subpath_uses_cwd(self) -> None:
        nested = self.tmp_path / "workspace"
        nested.mkdir(parents=True)
        explicit = nested / "configs" / "config.json"
        explicit.parent.mkdir(parents=True)
        explicit.write_text("{}", encoding="utf-8")
        os.chdir(nested)

        self.assertEqual(self.resolve_config_path("configs/config.json"), explicit.resolve())

    def test_missing_config_returns_preferred_not_legacy(self) -> None:
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        expected = shared_root / "seren" / "skills-data" / SLUG / "config.json"
        self.assertEqual(self.resolve_config_path(), expected)

    # --- resolve_env_path ---

    def test_env_override_var_accepts_absolute_path(self) -> None:
        explicit = self.tmp_path / "custom.env"
        explicit.write_text("KEY=val\n", encoding="utf-8")
        os.environ["SEREN_SKILL_ENV_FILE"] = str(explicit)

        self.assertEqual(self.resolve_env_path(), explicit)

    def test_resolve_env_uses_shared_runtime_root(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / ".env").write_text("KEY=val\n", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        self.assertEqual(self.resolve_env_path(), runtime_dir / ".env")

    def test_load_skill_env_reads_runtime_env_file(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / ".env").write_text("SEREN_API_KEY=test-key\n", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.environ.pop("SEREN_API_KEY", None)
        os.chdir(self.tmp_path)

        loaded = self.load_skill_env()

        self.assertEqual(loaded, runtime_dir / ".env")
        self.assertEqual(os.environ["SEREN_API_KEY"], "test-key")

    # --- legacy fallback ---

    def test_legacy_fallback_warns_once(self) -> None:
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)
        (self.skill_root / ".env").write_text("KEY=val\n", encoding="utf-8")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            first = self.resolve_env_path()
            self.assertEqual(first, self.skill_root / ".env")
            self.assertEqual(len(caught), 1)
            self.assertIs(caught[0].category, LegacyRuntimePathWarning)

        with warnings.catch_warnings(record=True) as caught2:
            warnings.simplefilter("always")
            second = self.resolve_env_path()
            self.assertEqual(second, self.skill_root / ".env")
            self.assertEqual(len(caught2), 0)

    def test_legacy_warned_sets_are_per_skill(self) -> None:
        """Each make_runtime_paths call gets its own warned set."""
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        other_root = self.tmp_path / "other" / "skill"
        other_root.mkdir(parents=True)
        (other_root / ".env").write_text("KEY=val\n", encoding="utf-8")
        _, resolve_env_other, _, _, _, _ = make_runtime_paths("other-skill", other_root)
        (self.skill_root / ".env").write_text("KEY=val\n", encoding="utf-8")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.resolve_env_path()
            resolve_env_other()
            self.assertEqual(len(caught), 2)

    # --- resolve_runtime_dir ---

    def test_resolve_runtime_dir_from_config_path(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "config.json").write_text("{}", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        self.assertEqual(self.resolve_runtime_dir("config.json"), runtime_dir)

    def test_resolve_runtime_dir_default(self) -> None:
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        expected = shared_root / "seren" / "skills-data" / SLUG
        self.assertEqual(self.resolve_runtime_dir(), expected)

    def test_activate_runtime_switches_into_runtime_dir(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        project = self.tmp_path / "workspace"
        project.mkdir(parents=True)
        os.chdir(project)

        resolved = self.activate_runtime("config.json")

        self.assertEqual(resolved, runtime_dir / "config.json")
        self.assertEqual(Path.cwd().resolve(), runtime_dir.resolve())


    # --- load_skill_env ---

    def test_load_skill_env_override_replaces_existing_var(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / ".env").write_text("KEY=new-value\n", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.environ["KEY"] = "old-value"
        os.chdir(self.tmp_path)

        self.load_skill_env(override=True)

        self.assertEqual(os.environ["KEY"], "new-value")

    def test_load_skill_env_no_override_preserves_existing_var(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / ".env").write_text("KEY=new-value\n", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.environ["KEY"] = "old-value"
        os.chdir(self.tmp_path)

        self.load_skill_env(override=False)

        self.assertEqual(os.environ["KEY"], "old-value")

    def test_load_skill_env_returns_none_when_file_missing(self) -> None:
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.chdir(self.tmp_path)

        self.assertIsNone(self.load_skill_env())

    # --- activate_runtime ---

    def test_activate_runtime_loads_env(self) -> None:
        shared_root = self.tmp_path / "xdg"
        runtime_dir = shared_root / "seren" / "skills-data" / SLUG
        runtime_dir.mkdir(parents=True)
        (runtime_dir / ".env").write_text("SEREN_API_KEY=activated-key\n", encoding="utf-8")
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)
        os.environ.pop("SEREN_API_KEY", None)
        os.chdir(self.tmp_path)

        self.activate_runtime()

        self.assertEqual(os.environ["SEREN_API_KEY"], "activated-key")

    # --- project runtime dir ---

    def test_project_runtime_dir_returns_none_with_no_seren_ancestor(self) -> None:
        isolated = self.tmp_path / "isolated"
        isolated.mkdir()
        shared_root = self.tmp_path / "xdg"
        os.environ["XDG_CONFIG_HOME"] = str(shared_root)
        os.environ.pop("APPDATA", None)

        expected = shared_root / "seren" / "skills-data" / SLUG
        self.assertEqual(self.default_runtime_dir(start=isolated), expected)


if __name__ == "__main__":
    unittest.main()

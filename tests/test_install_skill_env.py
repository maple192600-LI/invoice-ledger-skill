from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import install_skill_env


class InstallSkillEnvTest(unittest.TestCase):
    def test_ledger_only_updates_setting_without_install_or_doctor(self) -> None:
        with TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.xlsx"
            with (
                patch.object(install_skill_env, "_saved_ledger", return_value=None),
                patch.object(
                    install_skill_env,
                    "configure_ledger",
                    return_value=(ledger, True),
                ) as configure_ledger,
                patch.object(install_skill_env, "build_install_plan") as build_install_plan,
                patch.object(install_skill_env, "_run") as run,
            ):
                code = install_skill_env.main(
                    ["--ledger-only", "--ledger", str(ledger)]
                )

            self.assertEqual(0, code)
            configure_ledger.assert_called_once_with(str(ledger))
            build_install_plan.assert_not_called()
            run.assert_not_called()

    def test_ledger_only_requires_explicit_ledger_location(self) -> None:
        self.assertEqual(2, install_skill_env.main(["--ledger-only"]))


if __name__ == "__main__":
    unittest.main()

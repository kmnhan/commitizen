import os
import shutil
from typing import Any, Dict, List, Optional

import questionary
import yaml
from packaging.version import Version

from commitizen import cmd, factory, out
from commitizen.__version__ import __version__
from commitizen.config import BaseConfig, JsonConfig, TomlConfig, YAMLConfig
from commitizen.cz import registry
from commitizen.defaults import config_files
from commitizen.exceptions import InitFailedError, NoAnswersError
from commitizen.git import get_latest_tag_name, get_tag_names, smart_open


class Init:
    def __init__(self, config: BaseConfig, *args):
        self.config: BaseConfig = config
        self.cz = factory.commiter_factory(self.config)

    def __call__(self):
        if self.config.path:
            out.line(f"Config file {self.config.path} already exists")
            return

        # No config for commitizen exist
        config_path = self._ask_config_path()
        if "toml" in config_path:
            self.config = TomlConfig(data="", path=config_path)
        elif "json" in config_path:
            self.config = JsonConfig(data="{}", path=config_path)
        elif "yaml" in config_path:
            self.config = YAMLConfig(data="", path=config_path)
        self.config.init_empty_config_content()

        values_to_add = {}
        values_to_add["name"] = self._ask_name()
        tag = self._ask_tag()
        values_to_add["version"] = Version(tag).public
        values_to_add["tag_format"] = self._ask_tag_format(tag)
        self._update_config_file(values_to_add)

        hook_types = questionary.checkbox(
            "What types of pre-commit hook you want to install? (Leave blank if you don't want to install)",
            choices=[
                questionary.Choice("commit-msg", checked=True),
                questionary.Choice("pre-push", checked=True),
            ],
        ).ask()
        if hook_types:
            if not self._install_pre_commit_hook(hook_types):
                raise InitFailedError(
                    "Installation failed. See error outputs for more information."
                )

        out.write("You can bump the version and create changelog running:\n")
        out.info("cz bump --changelog")
        out.success("The configuration are all set.")

    def _ask_config_path(self) -> str:
        name: str = questionary.select(
            "Please choose a supported config file: (default: pyproject.toml)",
            choices=config_files,
            default="pyproject.toml",
            style=self.cz.style,
        ).ask()
        return name

    def _ask_name(self) -> str:
        name: str = questionary.select(
            "Please choose a cz (commit rule): (default: cz_conventional_commits)",
            choices=list(registry.keys()),
            default="cz_conventional_commits",
            style=self.cz.style,
        ).ask()
        return name

    def _ask_tag(self) -> str:
        latest_tag = get_latest_tag_name()
        if not latest_tag:
            out.error("No Existing Tag. Set tag to v0.0.1")
            return "0.0.1"

        is_correct_tag = questionary.confirm(
            f"Is {latest_tag} the latest tag?", style=self.cz.style, default=False
        ).ask()
        if not is_correct_tag:
            tags = get_tag_names()
            if not tags:
                out.error("No Existing Tag. Set tag to v0.0.1")
                return "0.0.1"

            latest_tag = questionary.select(
                "Please choose the latest tag: ",
                choices=get_tag_names(),  # type: ignore
                style=self.cz.style,
            ).ask()

            if not latest_tag:
                raise NoAnswersError("Tag is required!")
        return latest_tag

    def _ask_tag_format(self, latest_tag) -> str:
        is_correct_format = False
        if latest_tag.startswith("v"):
            tag_format = r"v$version"
            is_correct_format = questionary.confirm(
                f'Is "{tag_format}" the correct tag format?', style=self.cz.style
            ).ask()

        if not is_correct_format:
            tag_format = questionary.text(
                'Please enter the correct version format: (default: "$version")',
                style=self.cz.style,
            ).ask()

            if not tag_format:
                tag_format = "$version"
        return tag_format

    def _search_pre_commit(self):
        return shutil.which("pre-commit") is not None

    def _exec_install_pre_commit_hook(self, hook_types: List[str]):
        if not hook_types:
            raise ValueError("At least 1 hook type should be provided.")
        cmd_str = "pre-commit install " + "".join(
            f"--hook-type {ty}" for ty in hook_types
        )
        c = cmd.run(cmd_str)
        if c.return_code != 0:
            out.error(f"Error running {cmd_str}. Outputs are attached below:")
            out.error(f"stdout: {c.out}")
            out.error(f"stderr: {c.err}")
            return False
        return True

    def _install_pre_commit_hook(self, hook_types: Optional[List[str]] = None) -> bool:
        pre_commit_config_filename = ".pre-commit-config.yaml"
        cz_hook_config = {
            "repo": "https://github.com/commitizen-tools/commitizen",
            "rev": f"v{__version__}",
            "hooks": [
                {"id": "commitizen"},
                {"id": "commitizen-branch", "stages": ["push"]},
            ],
        }

        config_data = {}
        if not os.path.isfile(pre_commit_config_filename):
            # .pre-commit-config.yaml does not exist
            config_data["repos"] = [cz_hook_config]
        else:
            with open(pre_commit_config_filename) as config_file:
                yaml_data = yaml.safe_load(config_file)
                if yaml_data:
                    config_data = yaml_data

            if "repos" in config_data:
                for pre_commit_hook in config_data["repos"]:
                    if "commitizen" in pre_commit_hook["repo"]:
                        out.write("commitizen already in pre-commit config")
                        break
                else:
                    config_data["repos"].append(cz_hook_config)
            else:
                # .pre-commit-config.yaml exists but there's no "repos" key
                config_data["repos"] = [cz_hook_config]

        with smart_open(pre_commit_config_filename, "w") as config_file:
            yaml.safe_dump(config_data, stream=config_file)

        if not self._search_pre_commit():
            out.error("pre-commit is not installed in current environement.")
            return False

        if hook_types is None:
            hook_types = ["commit-msg", "pre-push"]
        if not self._exec_install_pre_commit_hook(hook_types):
            return False

        out.write("commitizen pre-commit hook is now installed in your '.git'\n")
        return True

    def _update_config_file(self, values: Dict[str, Any]):
        for key, value in values.items():
            self.config.set_key(key, value)

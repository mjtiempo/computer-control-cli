"""The skill names every verb, and every refusal code the code can raise.

The skill is what an agent reads before it calls the CLI, so a verb or a code
that is not in it is a contract the model cannot honour. This check fails on
any drift between the two.
"""

import glob
import os
import re
import unittest

from computer_control_cli.cli import Cli  # pi-lens-ignore: reportMissingImports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skill", "computer-control", "SKILL.md")
PACKAGE = os.path.join(ROOT, "computer_control_cli")
SKILLS = [SKILL] + sorted(glob.glob(
    os.path.join(ROOT, ".opencode", "skills", "*", "SKILL.md")))

CODE_PATTERNS = (
    re.compile(r'ControlError\(\s*"([a-z0-9-]+)"'),
    re.compile(r'ERR\[([a-z0-9-]+)]'),
)


class SkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SKILL, encoding="utf-8") as handle:
            cls.skill = handle.read()

    def test_every_verb_is_documented(self):
        parser = Cli.build_parser()
        verbs = []
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                verbs.extend(choices)
        self.assertTrue(verbs)

        missing = [verb for verb in verbs
                   if not re.search(rf"`{re.escape(verb)}[` ]", self.skill)]
        self.assertEqual(missing, [], f"verbs missing from the skill: {missing}")

    @staticmethod
    def raised_codes() -> set:
        """Every refusal code the package can raise."""
        codes = set()
        for name in os.listdir(PACKAGE):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
                text = handle.read()
            for pattern in CODE_PATTERNS:
                codes.update(pattern.findall(text))

        codes.discard("code")   # the placeholder in the ERR[code] format line
        return codes

    def test_every_refusal_code_is_documented(self):
        codes = self.raised_codes()

        missing = sorted(code for code in codes
                         if f"ERR[{code}]" not in self.skill
                         and f"`{code}`" not in self.skill)
        self.assertEqual(missing, [],
                         f"refusal codes missing from the skill: {missing}")

    def test_every_code_a_skill_names_is_one_the_package_raises(self):
        codes = self.raised_codes()
        invented = []
        for path in SKILLS:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for code in sorted(set(re.findall(r"ERR\[([a-z0-9-]+)]", text))):
                if code != "code" and code not in codes:
                    invented.append(f"{os.path.relpath(path, ROOT)}: {code}")

        self.assertEqual(invented, [],
                         "a skill names a code the package cannot raise")


if __name__ == "__main__":
    unittest.main()

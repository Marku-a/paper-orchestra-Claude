# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Print a prompt constant from the repo verbatim, so agents reuse the repo's own prompts.

Usage (from the repo root):
    python scripts/render_prompt.py --list
    python scripts/render_prompt.py methods.prompts.outline_agent:outline_agent_system_prompt
    python scripts/render_prompt.py <module>:<NAME> --format key=value [key=value ...]
"""

import argparse
import glob
import importlib
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _modules():
    mods = []
    for pkg in ("methods/prompts", "autoraters/prompts"):
        for path in sorted(glob.glob(os.path.join(REPO_ROOT, pkg, "*.py"))):
            name = os.path.basename(path)[:-3]
            if name != "__init__":
                mods.append(f"{pkg.replace('/', '.')}.{name}")
    mods.append("utils.paper_banana_utils")
    return mods


def _constants(mod):
    return sorted(
        n for n, v in vars(mod).items() if n.isidentifier() and not n.startswith("_") and isinstance(v, str)
        and (not mod.__name__.endswith("paper_banana_utils") or n.endswith("_PROMPT"))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="<module>:<NAME>")
    parser.add_argument("--list", action="store_true", help="List all prompt constants.")
    parser.add_argument("--format", nargs="*", default=None, metavar="key=value",
                        help="Fill template placeholders; if a key is missing the raw template is printed.")
    args = parser.parse_args()

    if args.list:
        for m in _modules():
            mod = importlib.import_module(m)
            for n in _constants(mod):
                print(f"{m}:{n}")
        return 0

    if not args.target or ":" not in args.target:
        parser.error("give <module>:<NAME> or --list")
    m, name = args.target.split(":", 1)
    if m.endswith(".py"):
        m = m[:-3].replace("/", ".")
    value = getattr(importlib.import_module(m), name, None)
    if not isinstance(value, str):
        print(f"Error: {m}:{name} is not a string constant", file=sys.stderr)
        return 1
    if args.format:
        kwargs = dict(kv.split("=", 1) for kv in args.format if "=" in kv)
        try:
            value = value.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass  # missing key: print raw template
    sys.stdout.write(value)
    if not value.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

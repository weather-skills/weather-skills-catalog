# Weather skills catalog

Weather skills turn scientific weather-data pipelines into fixed, reviewable tools for agentic AI. An assistant fetches a forecast or an observation, clips it, aggregates it, and plots it by running checked-in skills in order. Each output file carries provenance — the skill, version, and arguments that produced it — so someone else can rerun or audit the chain. Skills are plain Python scripts. They run in any environment with a code-execution runtime and [uv](https://docs.astral.sh/uv/), and they read and write [CF-compliant](https://cfconventions.org/) Zarr, so one skill's output is the next skill's input.

This repository is that catalog. `main` is the canonical tree consumers install. The [`catalog`](https://github.com/weather-skills/weather-skills-catalog/tree/catalog) branch records each skill repository as a git submodule. Merging to `catalog` expands those submodules and publishes them on `main` as ordinary folders.

Early stage: interfaces, the standard dataset, and skill boundaries may change.

## Using the skills

Install from `main` of [weather-skills/weather-skills-catalog](https://github.com/weather-skills/weather-skills-catalog). Each provider stays in its own folder for the CLI. Publish also copies every skill to `skills/<provider>/<name>/` (for example `skills/weather-skills/clip-region`), which is the tree skillkit checks out.

### Command line

For ad-hoc use, with no agent, each provider folder is a CLI. `uvx` runs it without a permanent install:

```bash
# List the Rhiza weather skills
uvx --from "git+https://github.com/weather-skills/weather-skills-catalog#subdirectory=weather-skills" weather-skills

# Run one
uvx --from "git+https://github.com/weather-skills/weather-skills-catalog#subdirectory=weather-skills" weather-skills <skill> [args]

# Climate Hazards Center skills
uvx --from "git+https://github.com/weather-skills/weather-skills-catalog#subdirectory=chc-skills" chc-skills
```

Or install a provider once and invoke it directly:

```bash
uv tool install "git+https://github.com/weather-skills/weather-skills-catalog#subdirectory=weather-skills"
weather-skills                              # list
weather-skills <skill> [args]               # run one
```

Each skill's inline dependency block is resolved by `uv run` on that invocation.

### Agent skills

For an LLM agent, install the `SKILL.md` files with [skillkit](https://github.com/rohitg00/skillkit). `npx` runs the latest skillkit on demand:

```bash
# List what skillkit discovers in the catalog
npx skillkit install weather-skills/weather-skills-catalog --list

# Install every skill into the current project
npx skillkit install weather-skills/weather-skills-catalog --all --yes

# Install one skill
npx skillkit install weather-skills/weather-skills-catalog --skill=ecmwf-fetch
```

## Building a skill

Write the work in ordinary Python and depend on the [`weather-skills-core`](https://github.com/rhiza-research/weather-skills-core) package. `@weather_skill` turns a function into a CLI and an agent skill: it parses the shared flags, checks the standard-dataset contract, and stamps provenance on the output.

```bash
uv add "weather-skills-core @ git+https://github.com/rhiza-research/weather-skills-core"
```

A skill is a directory `skills/<name>/` with a `SKILL.md` and a script. The script declares inputs and returns a dataset or a figure; the decorator owns `--output`.

```python
from weather_skills_core import Dataset, weather_skill

@weather_skill(name="clip-region", version="0.1.0")
@weather_skill.argument("-i", "--input", type=Dataset("spatial"), required=True, dest="ds")
@weather_skill.argument("--bbox", required=True)
def clip_region(ds, output, bbox, **kwargs):
    return ds
```

The authoring guide — declaration, dimensions, units, and CLI flag names — is [`weather-skill-authoring`](https://github.com/rhiza-research/weather-skills-core/blob/main/skills/weather-skill-authoring/SKILL.md). The dataset contract is [`STANDARD_DATASET.md`](https://github.com/rhiza-research/weather-skills-core/blob/main/skills/weather-skill-authoring/references/STANDARD_DATASET.md).

## Contributing

New skills enter the catalog as a git submodule on the `catalog` branch. Open a pull request against `catalog` that adds your skill repository. Reviewers read the pin, and CI checks the submodule out and lints its skills with `weather-skills-core`. After the pull request merges, [Publish catalog](.github/workflows/publish-catalog.yml) expands every submodule into a normal folder and pushes that tree to `main`. Install and skillkit both read `main`. Pull requests that edit skill files directly on `main` will be overwritten by the next publish.

Your repository should use the same layout as the providers already in the catalog: skills under `skills/<name>/`, each with a `SKILL.md` and a script built on `weather-skills-core`. Keep tests in your repository; that is where skill behavior is tested before you propose a pin.

Use an HTTPS submodule URL so CI can clone a public repository. The folder name is the name consumers will see on `main`.

```bash
git fetch origin catalog
git checkout -b add-your-skills origin/catalog
git submodule add -b main https://github.com/YOU/your-skills.git your-skills
git commit -m "Add your-skills."
git push -u origin HEAD
gh pr create --base catalog --title "Add your-skills" --body "Pin YOUR/your-skills."
```

To move an existing pin, update the submodule to the commit you want reviewed and commit the new gitlink in a pull request to `catalog`.

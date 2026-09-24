# Weather skills

An AI-ready library for weather and climate data.

Use natural language to fetch, transform, and visualize weather data — with provenance you can audit.

Weather skills are composable tools that allow AI agents to support operational forecasting, scientific exploration of climate data, and the use of forecasts and weather data for specific applications. Each weather skill is expert-reviewed, and the results are backed by provenance, so AI helps you call the right tools and does not modify the underlying data. Initiated by Rhiza Research; a community effort will steward the catalog.

A pipeline fetches forecasts and observations, transforms them (clip, aggregate, convert), and visualizes maps and time series. Provenance is recorded at every step. From a natural-language request, the agent picks skills and runs them in order.

We are looking for beta testers. If you want to use weather skills for your application, write to [info@rhizaresearch.org](mailto:info@rhizaresearch.org).

## Getting started

Skills are simple Python scripts with descriptions that assist AI agents in calling those scripts. Run them in your terminal, add them to an agent, reach them through MCP, or use the hosted chat.

### As a CLI tool

For command-line use, with no install:

```bash
# List available skills
uvx --from git+https://github.com/weather-skills/weather-skills-catalog weather-skills

# Run one
uvx --from git+https://github.com/weather-skills/weather-skills-catalog weather-skills <skill> [args]
```

### As agent skills

For use by a local agent, install the `SKILL.md` files into your project with [skillkit](https://github.com/rohitg00/skillkit):

```bash
# List what skillkit discovers in the repo
npx skillkit install weather-skills/weather-skills-catalog --list

# Install all skills to the current project
npx skillkit install weather-skills/weather-skills-catalog --all --yes

# Install just a subset
npx skillkit install weather-skills/weather-skills-catalog --skill=ecmwf-fetch
```

### As an MCP

A hosted MCP will be coming soon.

### Weather Skills Chat

We are rolling out a hosted version of weather skills. [Request access](https://chat.weather-skills.org) if you are interested in being a beta tester.

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

The authoring guide — declaration, dimensions, units, and CLI flag names — is [`weather-skill-authoring`](https://github.com/rhiza-research/weather-skills-core/blob/main/docs/weather-skill-authoring/SKILL.md). The dataset contract is [`STANDARD_DATASET.md`](https://github.com/rhiza-research/weather-skills-core/blob/main/docs/weather-skill-authoring/references/STANDARD_DATASET.md).

## Contributing

New skills enter the catalog as a git submodule on the `catalog` branch. Open a pull request against `catalog` that adds your skill repository. Reviewers read the pin, and CI checks the submodule out and lints its skills with `weather-skills-core`. After the pull request merges, [Publish catalog](.github/workflows/publish-catalog.yml) replaces `skills/` on `main` with `skills/<provider>/<name>/` from the pinned repositories. Other files on `main` are merged and kept. Skillkit reads `main`. Edits under `skills/` on `main` are overwritten by the next publish.

Your repository should use the same layout as the providers already in the catalog: skills under `skills/<name>/`, each with a `SKILL.md` and a script built on `weather-skills-core`. Keep tests in your repository; that is where skill behavior is tested before you propose a pin.

Use an HTTPS submodule URL so CI can clone a public repository. The submodule folder name is the `<provider>` segment on `main`.

```bash
git fetch origin catalog
git checkout -b add-your-skills origin/catalog
git submodule add -b main https://github.com/YOU/your-skills.git your-skills
git commit -m "Add your-skills."
git push -u origin HEAD
gh pr create --base catalog --title "Add your-skills" --body "Pin YOUR/your-skills."
```

To move an existing pin, update the submodule to the commit you want reviewed and commit the new gitlink in a pull request to `catalog`.

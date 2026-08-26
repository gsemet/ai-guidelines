# Installation

`ai-guidelines` requires Python 3.10 or newer.

Install preferably as an isolated tool:

```bash
$ uv tool install ai-guidelines
```

Verify the installation:

```bash
$ guidelines --version
```

## Git requirement

Git must be on `PATH` for **remote** sources (GitHub, GitLab, generic Git, SSH). Local
file and folder sources need no Git.

## What gets installed

One console entry point, `guidelines`, plus the importable `ai_guidelines` package. See
{doc}`reference/api` for the public Python surface.

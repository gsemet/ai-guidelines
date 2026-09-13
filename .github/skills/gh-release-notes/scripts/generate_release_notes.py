#!/usr/bin/env python3
"""Generate and validate release notes through the GitHub Copilot CLI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

SKILL_NAME = "gh-release-notes"
DEFAULT_OUTPUT = Path("release-notes.md")
MAX_GIT_CONTEXT_LENGTH = 60_000
TOKEN_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
PERMITTED_HEADINGS = frozenset(
    {
        "## New Features",
        "## Enhancements",
        "## Bug Fixes",
        "## Breaking Changes",
        "## Examples",
        "## Documentation",
        "## Maintenance",
    }
)
TRACE_MARKERS = (
    "<function_call",
    "<thinking>",
    "<system_notification>",
    "assistant.reasoning",
    "function_calls",
    "to=bash.exec",
    "to=functions.exec",
)
MAINTENANCE_NOTES = (
    "## Maintenance\n\n"
    "This release contains maintenance and internal improvements. "
    "No user-facing behavior changed.\n"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate or validate release notes for the gh-release-notes skill."
    )
    parser.add_argument(
        "--from-ref",
        help="Starting Git ref. It is excluded: the range is from_ref..to_ref.",
    )
    parser.add_argument(
        "--to-ref",
        help="Ending Git ref. It is included: the range is from_ref..to_ref.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository to inspect (default: current directory).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Release-note output path (default: release-notes.md).",
    )
    parser.add_argument(
        "--maintenance-only",
        action="store_true",
        help="Write deterministic maintenance notes without invoking Copilot.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Copilot CLI model identifier; defaults to COPILOT_MODEL or the CLI default.",
    )
    parser.add_argument(
        "--validate",
        type=Path,
        metavar="FILE",
        help="Verify that an existing release-note file follows the output contract.",
    )
    return parser


def build_git_context(repo: Path, from_ref: str, to_ref: str) -> str:
    """Collect generic Git evidence for Copilot environments without Git access."""
    log = git_output(
        repo, "commit log", "log", "--format=%h %s", "--no-merges", f"{from_ref}..{to_ref}"
    )
    diff_stat = git_output(
        repo, "diff summary", "diff", "--stat", "--no-ext-diff", f"{from_ref}..{to_ref}"
    )
    diff = git_output(repo, "diff", "diff", "--no-ext-diff", "--unified=3", f"{from_ref}..{to_ref}")
    context = (
        f"Precomputed Git evidence for {from_ref}..{to_ref}:\n\n"
        f"Commit log:\n{log or '(no commits)'}\n\n"
        f"Diff summary:\n{diff_stat or '(empty)'}\n\n"
        f"Diff:\n{diff or '(empty)'}"
    )
    if len(context) <= MAX_GIT_CONTEXT_LENGTH:
        return context

    truncated = context[:MAX_GIT_CONTEXT_LENGTH]
    return (
        f"{truncated}\n\n[Git evidence truncated at {MAX_GIT_CONTEXT_LENGTH} characters; "
        "use the included summary and inspect the checked-out files when needed.]"
    )


def build_prompt(
    from_ref: str,
    to_ref: str,
    repo: Path,
    output: Path,
    git_context: str | None = None,
) -> str:
    """Build the small orchestration prompt; the skill owns release-note policy."""
    try:
        output_reference = output.relative_to(repo).as_posix()
    except ValueError:
        output_reference = str(output)

    prompt = (
        f"Use the /{SKILL_NAME} skill. Generate release notes for the exact Git range "
        f"{from_ref}..{to_ref} in {repo}. The skill is authoritative for analysis, "
        f"classification, wording, documentation, and Markdown format. Write the final "
        f"release-note Markdown directly to the shared repository file "
        f"{output_reference} using the file-writing tools. Its resolved path is {output}. "
        "The release workflow reads this exact file as its notes input; the Copilot "
        "response stream is discarded. Do not put release notes or a summary in your "
        "response, and do not modify any other files. Before writing, enforce the "
        "skill's final output contract: render every documentation URL as concise inline "
        "Markdown, never as a bare URL; exclude all internal CI, release automation, "
        "governance, contributor, agent, generator, Git-evidence, and maintainer content; "
        "and omit Maintenance whenever any user-facing section remains."
    )
    if git_context:
        prompt += (
            " Treat the following locally collected Git evidence as authoritative input; "
            "the starting ref is excluded and the ending ref is included.\n\n"
            f"<git-evidence>\n{git_context}\n</git-evidence>"
        )
    return prompt


def build_copilot_command(prompt: str, model: str | None) -> list[str]:
    """Build the non-interactive Copilot CLI command."""
    command = [
        "gh",
        "copilot",
        "--",
        "--prompt",
        prompt,
        "--silent",
        "--no-ask-user",
        "--no-auto-update",
        "--no-color",
        "--output-format",
        "text",
        "--disable-builtin-mcps",
        "--available-tools=read,create,edit,bash",
        "--allow-tool=read",
        "--allow-tool=write",
        "--allow-tool=shell(git:*)",
        "--allow-url=https://github.com",
        "--allow-url=https://ai-guidelines.readthedocs.io",
    ]
    if model:
        command.extend(["--model", model])
    return command


def resolve_path(path: Path, repo: Path) -> Path:
    """Resolve a path relative to the repository when it is not absolute."""
    return path if path.is_absolute() else repo / path


def run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a Git command in the target repository."""
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def git_output(repo: Path, description: str, *arguments: str) -> str:
    """Run Git and return its output, raising one consistent error on failure."""
    result = run_git(repo, *arguments)
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown Git error"
        raise ValueError(f"Unable to collect the release {description}: {detail}")
    return result.stdout.strip()


def validate_range(repo: Path, from_ref: str, to_ref: str) -> None:
    """Verify that the requested refs exist and form an ancestor range."""
    if from_ref == to_ref:
        raise ValueError("--from-ref and --to-ref must be different refs.")

    for ref in (from_ref, to_ref):
        result = run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown Git error"
            raise ValueError(f"Git ref {ref!r} is not available: {detail}")

    result = run_git(repo, "merge-base", "--is-ancestor", from_ref, to_ref)
    if result.returncode != 0:
        raise ValueError(f"Git ref {from_ref!r} is not an ancestor of {to_ref!r}.")


def range_has_commits(repo: Path, from_ref: str, to_ref: str) -> bool:
    """Return whether the requested Git range contains at least one commit."""
    count = git_output(repo, "commit count", "rev-list", "--count", f"{from_ref}..{to_ref}")
    return count != "0"


def write_maintenance_notes(output: Path) -> None:
    """Write the deterministic notes required for a maintenance-only release."""
    output.write_text(MAINTENANCE_NOTES, encoding="utf-8")


def run_skill_check(repo: Path) -> None:
    """Ensure the release-note skill is installed in the Copilot CLI."""
    result = subprocess.run(
        ["gh", "copilot", "--", "skill", "list"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise RuntimeError(f"Unable to list Copilot skills:\n{output.strip()}")
    if SKILL_NAME not in output:
        raise RuntimeError(f"Copilot skill {SKILL_NAME!r} is not installed.")


def require_copilot_token() -> None:
    """Fail early when no supported GitHub token environment variable exists."""
    if not any(os.environ.get(name) for name in TOKEN_ENV_VARS):
        names = ", ".join(TOKEN_ENV_VARS[:-1]) + f", or {TOKEN_ENV_VARS[-1]}"
        raise RuntimeError(f"Set one of {names} before running Copilot CLI.")


def run_copilot(repo: Path, prompt: str, model: str | None) -> None:
    """Run Copilot CLI and leave the generated Markdown in the shared output file."""
    result = subprocess.run(
        build_copilot_command(prompt, model),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}".strip()
        raise RuntimeError(f"Copilot CLI failed with exit code {result.returncode}:\n{output}")


def validate_output(output: Path) -> None:
    """Verify that generated release notes follow the output contract."""
    try:
        content = output.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Unable to read release-note output {output}: {error}") from error
    if not content.strip():
        raise RuntimeError(f"Copilot created an empty release-note file: {output}")

    lines = content.splitlines()
    first_line = lines[0] if lines else ""
    if first_line not in PERMITTED_HEADINGS:
        raise RuntimeError(
            f"Release-note output must start with one of the permitted section headings: {output}"
        )

    lowered = content.lower()
    leaked_markers = [marker for marker in TRACE_MARKERS if marker.lower() in lowered]
    if leaked_markers:
        markers = ", ".join(leaked_markers)
        raise RuntimeError(
            f"Release-note output contains Copilot trace markers ({markers}): {output}"
        )
    if "```" in content:
        raise RuntimeError(f"Release-note output must not contain a code fence: {output}")
    if any(line.startswith("# ") for line in lines):
        raise RuntimeError(f"Release-note output must not contain a title heading: {output}")
    headings = [line for line in lines if line.startswith("## ")]
    invalid_headings = [heading for heading in headings if heading not in PERMITTED_HEADINGS]
    if invalid_headings:
        raise RuntimeError(
            f"Release-note output contains an invalid section heading "
            f"{invalid_headings[0]!r}: {output}"
        )
    print("Release-note output file verified.")


def generate_release_notes(
    repo: Path,
    from_ref: str,
    to_ref: str,
    output: Path,
    model: str | None,
    maintenance_only: bool = False,
) -> None:
    """Generate release notes, then validate the resulting Markdown file."""
    repo = repo.resolve()
    output = resolve_path(output, repo).resolve()
    validate_range(repo, from_ref, to_ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    print(f"Generating release notes from {from_ref} (exclusive) to {to_ref} (inclusive).")
    print(f"Copilot model: {model or 'CLI default'}")
    if maintenance_only or not range_has_commits(repo, from_ref, to_ref):
        write_maintenance_notes(output)
        validate_output(output)
        print(f"Release notes written to {output}")
        return

    require_copilot_token()
    run_skill_check(repo)
    git_context = build_git_context(repo, from_ref, to_ref)
    run_copilot(
        repo,
        build_prompt(from_ref, to_ref, repo, output, git_context),
        model,
    )

    validate_output(output)
    print(f"Release notes written to {output}")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, generate notes, and return an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.validate is not None:
        if (args.from_ref is None) != (args.to_ref is None):
            parser.error("--validate requires both --from-ref and --to-ref for range validation")
        try:
            repo = args.repo.resolve()
            if args.from_ref is not None and args.to_ref is not None:
                validate_range(repo, args.from_ref, args.to_ref)
            validate_output(resolve_path(args.validate, repo))
        except (OSError, RuntimeError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0

    if args.from_ref is None or args.to_ref is None:
        parser.error("--from-ref and --to-ref are required unless --validate is used")

    try:
        generate_release_notes(
            repo=args.repo,
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            output=args.output,
            model=args.model or os.environ.get("COPILOT_MODEL"),
            maintenance_only=args.maintenance_only,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

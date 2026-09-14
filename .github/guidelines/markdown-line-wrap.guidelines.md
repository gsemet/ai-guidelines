# Rewrap Markdown

Apply these rules to user-facing Markdown such as documentation, README files,
changelogs, tutorials, and reports.
Follow higher-priority project rules for the target file before applying this guideline.

Do not apply these rules to agent configuration or agent-facing Markdown, including
`SKILL.md`, `AGENTS.md`, `CONSTITUTION.md`, `*.instructions.md`, `*.prompt.md`,
`*.agent.md`, `*.guideline.md`, `*.guidelines.md`, and files whose content is parsed
as agent instructions.

The goal is predictable, readable wrapping without changing meaning.

## Rules

### Rule 1 — Wrap prose sentence-first at 100 characters maximum

- Treat each sentence as one visual wrapping unit.
- Start every sentence on a new physical line, even when adjacent sentences fit on one line.
- Keep a sentence on one line only when it is no more than **100 columns** wide.
- When a sentence is longer, apply Rule 2 and keep every resulting prose line within 100 columns.
- Use soft Markdown line breaks within a paragraph; do not insert blank lines between its sentences.
- Exempt tables, code blocks, frontmatter, URLs in source links, and ASCII art from prose wrapping.
  See **Protected content** for the complete list.

### Rule 2 — Split long sentences at clause boundaries

- When a sentence exceeds 100 columns, split it across multiple lines.
- Prefer boundaries in this order: semicolons and colons, parenthetical or explanatory separators,
  commas, and coordinating conjunctions (`and`, `but`, `or`, `because`, `so`, `then`).
- When a colon or semicolon introduces a substantial expansion, end the line after the separator
  and put the expansion on the next line.
- Use one major clause per line where practical, preferring meaningful short lines over lines
  that pack multiple clauses together.
- Do not merge separate sentences as a result of clause-aware wrapping.
- If one clause itself exceeds 100 columns, use the nearest safe word boundary as a last resort.
- Do not split a sentence merely to make every line close to 100 columns.
- Never break inside an inline code span, inside a link `[...](...)`, or between a number and its unit.
- Do not rewrite or split sentences semantically; only add visual line breaks.

### Rule 3 — Replace em-dashes with `:` or parentheses (conservative policy)

Replace the em-dash character `—` (U+2014) only when it is a parenthetical or
clause separator in flowing prose.

| Em-dash usage | Replacement | Example |
|---|---|---|
| Parenthetical aside (paired) | parentheses | `text — aside — text` -> `text (aside) text` |
| Clause or explanation introducer | colon | `we did X — it failed` -> `we did X: it failed` |
| Range between numbers | en-dash `–` or `to` | `pages 5—10` -> `pages 5–10` |
| Leading list-item separator | **leave as-is** | `- **Term** — definition` stays unchanged |
| Inside protected content | **leave as-is** | never touched |

**Decision rule for parenthetical vs. colon:**

- If a dash pair brackets material that could be removed without breaking the sentence,
  use parentheses.
- If a single dash introduces an explanation, consequence, or expansion, use a colon.
- When uncertain, prefer parentheses because they make less semantic change.

Never replace em-dashes in inline code, fenced code, URLs, tables, YAML frontmatter,
headings, author quotes, verbatim citations, or the leading separator pattern in
definition-style list items (`- **Foo** — bar`).

## Companion formatting rules

Apply these rules in addition to the three core rules.

### Headings

- Use **ATX** style (`##`, `###`); never the underline (`====`, `----`) style.
- Put exactly one blank line before and after each heading, except at document boundaries.
- Do not skip heading levels (`##` then `####` is forbidden).
- Keep one H1 per document and use it for the title.

### Lists

- Put one blank line before and after every list block.
- Apply the sentence-first policy inside list items.
- Start each sentence in a list item on its own physical line; do not merge adjacent sentences merely because they fit.
- Split a long sentence in a list item at clause boundaries, then indent continuation lines to align with the item text.

### Code blocks

- Give every fenced code block a **language tag** (`` ```python ``, `` ```bash ``, `` ```yaml ``, etc.).
- If a tag is missing, add one only when the language is unambiguous; ask the user when it is not.
- Use plain fenced blocks by default.
- Use the MyST `{code-block}` directive only when the document already uses MyST syntax and the block needs a caption.
- Otherwise leave plain fences as-is.

### Admonitions

Detect the document's admonition flavour and stay consistent:

- **GitHub / GitLab**: `> [!NOTE]`, `> [!WARNING]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!CAUTION]`.
- **MyST**: `:::{note}` ... `:::`.
- **Plain blockquote**: `> **Note**: ...`.

If the document mixes flavours, use the first flavour as the default for new or rewritten admonitions.
Preserve existing mixed blocks; never convert an existing admonition format without an explicit user request.

### Takeaway sections

The Takeaway-section convention from the source guideline
(numbered list, bold lead-ins, count introduction, optional `*Next:*` link)
is chapter-authoring content, not rewrap content.
Leave existing sections untouched; do not create or restructure them.

## Protected content

Preserve the following content byte-for-byte and do not rewrap it.
The only permitted change inside a fenced code block is adding a missing language tag when the language is unambiguous.

- YAML frontmatter between leading `---` fences.
- Fenced code blocks (`` ``` `` and `` ~~~ ``), including their language tag and content.
- Indented code blocks (4-space).
- Inline code spans (`` ` ``).
- Tables, including every line starting with `|`.
- URLs inside `[text](url)` and bare `<url>`.
- Blockquotes, including GitHub/GitLab admonitions.
- HTML blocks such as `<div>`, `<details>`, and `<table>`.
- Diagram blocks: mermaid, d2, plantuml, graphviz, and math (`$$ ... $$`).
- ASCII art and file-tree drawings containing `├`, `└`, or `│`.
- Image references and alt text inside `![...](...)`.

When in doubt whether something is prose or protected structure, **do not rewrap it**.

## Workflow

1. Read the entire target file.
2. Mark all protected regions listed above.
3. For each remaining flowing-prose paragraph, apply Rule 3, start each sentence on a new line,
   apply Rule 2 to sentences longer than 100 columns, and verify the resulting line lengths.
4. Apply the companion heading, list, code, and admonition rules outside protected content.
5. Show the proposed diff before writing when the document is larger than 200 lines or contains
   many em-dashes; for smaller files, write in place and report the change count.

## Markdownlint compatibility notes

This guideline does not invoke `markdownlint` or apply its auto-fixes.
The output is designed to satisfy these rules outside protected content:

| Rule | Description | How this guideline behaves |
|---|---|---|
| `MD003` | Heading style | ATX headings are required. |
| `MD007` | Unordered list indentation | Existing indentation is preserved. |
| `MD009` | Trailing spaces | Removed outside protected content. |
| `MD010` | Hard tabs | Not introduced; existing tabs are preserved. |
| `MD012` | Multiple consecutive blanks | Collapsed to one outside protected content. |
| `MD013` | Line length | 100 columns for prose; tables and code are exempt. |
| `MD022` | Blanks around headings | Enforced. |
| `MD023` | Headings start at column 1 | Enforced. |
| `MD025` | Single top-level heading | Preserved; not added. |
| `MD031` | Blanks around fenced code | Enforced. |
| `MD032` | Blanks around lists | Enforced. |
| `MD034` | Bare URLs | Not auto-wrapped because that could change semantics. |
| `MD040` | Fenced code language | Add a tag when unambiguous; ask the user when ambiguous. |
| `MD046` | Code block style | Preserve the existing style; never convert indented blocks to fences. |
| `MD047` | File ends with single newline | Enforced. |

These rules are out of scope: `MD026` (heading punctuation), `MD033` (inline HTML),
`MD036` (emphasis as heading), and `MD041` (first-line H1), which is preserved if present.

If the project has a stricter `.markdownlint.yaml`, run `markdownlint <file>` only when the
project workflow or user requests that validation, then address remaining issues separately.

## Examples

### Em-dash conversions

Before:

```markdown
The system — when configured correctly — expires sessions after 30 minutes,
and emits an audit log — this is required by §5.1.
```

After:

```markdown
The system (when configured correctly) expires sessions after 30 minutes,
and emits an audit log: this is required by §5.1.
```

### Sentence-first and clause-aware wrapping

Before:

```markdown
I read the article and felt something tighten in my chest. Not because I hate regulation.
Because I knew, in that moment, that the mechanism was wrong, technically, legally, ethically,
and that somebody should say something about it. The solution, it turns out, was in their own help center.
```

After:

```markdown
I read the article and felt something tighten in my chest.
Not because I hate regulation.
Because I knew, in that moment, that the mechanism was wrong, technically, legally, ethically,
and that somebody should say something about it.
The solution, it turns out, was in their own help center.
```

Never combine adjacent sentences merely because they fit below 100 characters.

### Clause fallback for a long sentence

Before:

```markdown
The verification triangle anchors stakeholder intent (StRS) into testable software-requirement shall-clauses (SwRS), which are then verified by both source-code annotations and test-report covered_requirements entries.
```

After:

```markdown
The verification triangle anchors stakeholder intent (StRS)
into testable software-requirement shall-clauses (SwRS),
which are then verified by both source-code annotations
and test-report covered_requirements entries.
```

Within one sentence, a coordinated series may stay on one line when it fits comfortably:

```markdown
but decision-making, review, coordination, integration, and rollout,
```

This exception applies only within one sentence; it does not permit adjacent sentences to share a line.

For a colon-led expansion:

```markdown
The internet did the rest:
2M impressions, 40k+ engagements, +9k stars on GitHub, dozens of contributions,
in less than three days.
```

### Preserved content (table)

Before and after are identical; table rows are never rewrapped:

```markdown
| Tier | Granularity | Owner |
|---|---|---|
| StRS | stakeholder intent — long shall-clauses welcome here, untouched | PO |
```

## Out of scope

This guideline does not:

- Rewrite prose for tone, voice, or clarity.
- Add or modify citations.
- Generate or restructure Takeaway sections.
- Translate between admonition flavours.
- Run linters or apply auto-fixes on its own.
- Touch files outside the target path.

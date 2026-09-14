# ai-guidelines

`ai-guidelines` manages reusable Markdown guidelines for a project.
It copies explicitly selected files into a project, records their source and SHA-256 hashes,
and can reproduce or update that selection safely.

It does not execute Markdown; it does not inject instructions; it does not activate skills.

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Tutorials
:link: tutorials/index
:link-type: doc

Learning-oriented walkthroughs. Start here if you are new.
:::

:::{grid-item-card} {octicon}`tools` How-To Guides
:link: how-to/index
:link-type: doc

Task-oriented recipes for a specific goal.
:::

:::{grid-item-card} {octicon}`book` Reference
:link: reference/index
:link-type: doc

The CLI, the Python API, and the file formats.
:::

:::{grid-item-card} {octicon}`light-bulb` How It Works
:link: explanation/index
:link-type: doc

Design rationale, the caching model, and comparisons.
:::

::::

```{toctree}
:hidden:
:maxdepth: 2

installation
tutorials/index
how-to/index
reference/index
How It Works <explanation/index>
changelog
```

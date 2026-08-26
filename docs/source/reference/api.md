# Python API

`ai_guidelines.api` is the stable public facade. Anything not re-exported there is internal
and may change without notice.

## Example

```python
from pathlib import Path

from ai_guidelines import load_manifest, parse_location, sync_manifest

manifest = load_manifest(Path("guidelines.yml"))
location = parse_location(manifest.guidelines[0].source)
result = sync_manifest(Path.cwd())
print(location.canonical_source, result)
```

## Facade

```{eval-rst}
.. automodule:: ai_guidelines.api
   :members:
   :undoc-members:
   :show-inheritance:
```

## Models

```{eval-rst}
.. automodule:: ai_guidelines.models
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: Field, model_config, model_fields, model_computed_fields
```

## Locations

```{eval-rst}
.. automodule:: ai_guidelines.locations
   :members:
   :undoc-members:
   :show-inheritance:
   :exclude-members: Field, model_config, model_fields, model_computed_fields
```

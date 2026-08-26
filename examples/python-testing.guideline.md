---
name: Python testing guideline
description: Practical rules for reliable Python tests
---

# Python testing

- Prefer focused function-based tests with clear behavioral names.
- Test success, malformed input, boundary conditions, and failure preservation.
- Use temporary paths and local fakes instead of network services.
- Keep tests deterministic and run the smallest relevant test first, then the full suite.

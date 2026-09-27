# Python: readability checks

Apply these curated checks, not strict PEP 8 compliance. Follow the project's
naming conventions, formatter, import tooling, and lint rules first.

Source citation: [PEP 8](https://peps.python.org/pep-0008/).

- Name functions and variables for their purpose (`snake_case` when the project
  is silent); use `CapWords` for classes. Do not rename public interfaces or
  unrelated code for consistency alone.
- Keep imports explicit and grouped sensibly; avoid wildcard imports that
  conceal where names come from.
- Break complex expressions and long functions at meaningful boundaries.
  Prefer readable control flow over condensed comprehensions with side effects
  or deeply nested conditions.
- Compare to `None` with `is`/`is not`, not `==`/`!=`. Catch specific
  exceptions; when translating one, use `raise ... from exc` if preserving
  the cause helps callers debug it.
- Add docstrings for non-obvious public behavior; explain intent rather than
  restating the code. Let the configured formatter decide spacing and line
  length.

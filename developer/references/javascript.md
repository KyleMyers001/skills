# JavaScript: readability checks

Apply these curated checks, not strict Google compliance. Follow the project's
module conventions, lint rules, and formatter first.

Source citation: [Google JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html).

- Prefer `const` for bindings that do not change and `let` when reassignment is
  needed. Avoid `var` and accidental shared mutable state.
- Prefer `===` and `!==` unless coercion is deliberate. When choosing a
  fallback, distinguish absent (`null`/`undefined`) from other falsy values
  (`0`, `false`, `''`); do not use `||` when those values are valid.
- Use optional chaining only when a missing value is acceptable; otherwise
  validate before accessing it. Keep promise ownership explicit: return or
  await async work rather than silently discarding it.
- Preserve existing import/export patterns and public shapes. Do not impose
  Google's semicolons, quote choices, or module restrictions on a project using
  another standard.

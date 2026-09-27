# TypeScript: readability checks

Apply these curated checks, not strict Google compliance. Follow the project's
types, module conventions, lint rules, and formatter first.

Source citation: [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html).

- Make public and cross-module contracts clear. Let obvious local types infer;
  annotate where inference obscures intent or a boundary needs a stable contract.
- Represent distinct states explicitly. Narrow `unknown` and nullable values
  before use; avoid `any`, broad assertions, and non-null assertions that
  conceal missing validation.
- Keep types understandable: prefer a small named type or discriminated union
  over repeated structural shapes, clever conditional types, or parallel flags
  that allow impossible combinations.
- Use `import type` where the project's TypeScript settings require or prefer
  it. Do not force Google's export or import policy onto an existing module
  system.

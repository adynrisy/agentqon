---
name: techdebt
description: Find and report technical debt in the codebase
---

# Tech Debt Scanner

Scan the codebase for common Elixir/Phoenix tech debt patterns.

## Check for

1. **Dead code**: Unused functions, modules, aliases
   - `mix xref unreachable`
   - Grep for `# TODO`, `# FIXME`, `# HACK`
2. **Duplicated code**: Similar function bodies across modules
3. **Missing typespecs**: Public functions without `@spec`
4. **Large modules**: Files > 300 lines
5. **Missing tests**: Modules in `lib/` without corresponding `test/` files
6. **Deprecated patterns**: Old Phoenix/Ecto patterns
7. **Credo issues**: `mix credo --strict --format json`

## Output format

Priority-sorted list with file:line references and suggested fixes.

## Rules

1. Always review the full diff before commenting
2. Prioritize security issues over style preferences
3. Never approve a PR with critical or high-severity findings
4. Provide actionable fix suggestions, not vague complaints
5. Acknowledge good code patterns explicitly
6. Respect the author's intent; suggest, don't dictate
7. Group related issues into a single comment thread
8. Flag missing tests for new logic paths
9. Never auto-merge without human confirmation
10. Keep comments concise; link to docs instead of explaining standards

### 2. Security Review

- Scan for SQL injection, XSS, SSRF, command injection
- Flag hardcoded secrets, API keys, tokens, passwords
- Check authentication and authorization on new endpoints
- Verify input validation and sanitization
- Review dependency changes for known vulnerabilities
- Flag unsafe deserialization or eval usage

### 3. Performance Check

- Identify N+1 query patterns
- Flag unnecessary re-renders in frontend code
- Check for missing database indexes on new queries
- Detect memory leaks (unclosed connections, event listeners)
- Review pagination on list endpoints
- Flag synchronous operations that should be async

### 4. Test Coverage

- Verify new functions have corresponding tests
- Check edge cases: empty input, null, boundary values
- Flag mocked tests that don't test real behavior
- Ensure integration tests for new API endpoints
- Check that error paths are tested, not just happy paths

### 5. Naming & Conventions

- Verify branch naming follows convention (feat/, fix/, chore/)
- Check commit messages follow conventional commits
- Ensure file organization matches project structure
- Flag inconsistent naming patterns within the PR
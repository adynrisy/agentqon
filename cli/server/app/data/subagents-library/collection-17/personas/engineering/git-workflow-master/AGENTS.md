## 🔧 Critical Rules

1. **Atomic commits** — Each commit does one thing and can be reverted independently
2. **Conventional commits** — `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`
3. **Never force-push shared branches** — Use `--force-with-lease` if you must
4. **Branch from latest** — Always rebase on target before merging
5. **Meaningful branch names** — `feat/user-auth`, `fix/login-redirect`, `chore/deps-update`

## 📋 Branching Strategies

### Trunk-Based (recommended for most teams)
```
main ─────●────●────●────●────●─── (always deployable)
           \  /      \  /
            ●         ●          (short-lived feature branches)
```

### Git Flow (for versioned releases)
```
main    ─────●─────────────●───── (releases only)
develop ───●───●───●───●───●───── (integration)
             \   /     \  /
              ●─●       ●●       (feature branches)
```

## 🎯 Key Workflows

### Starting Work
```bash
git fetch origin
git checkout -b feat/my-feature origin/main
# Or with worktrees for parallel work:
git worktree add ../my-feature feat/my-feature
```

### Clean Up Before PR
```bash
git fetch origin
git rebase -i origin/main    # squash fixups, reword messages
git push --force-with-lease   # safe force push to your branch
```

### Finishing a Branch
```bash
# Ensure CI passes, get approvals, then:
git checkout main
git merge --no-ff feat/my-feature  # or squash merge via PR
git branch -d feat/my-feature
git push origin --delete feat/my-feature
```

## 💬 Communication Style
- Explain Git concepts with diagrams when helpful
- Always show the safe version of dangerous commands
- Warn about destructive operations before suggesting them
- Provide recovery steps alongside risky operations
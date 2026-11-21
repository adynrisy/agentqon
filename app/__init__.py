"""AgentQon — multi-agent platform server package."""
# Rebrand bridge: apply the AGENTQON_* -> OMNIMIND_* env mirror as soon
# as any app module is imported, so direct env readers in sibling
# modules see the preferred prefix too (see app/config.py).
from app.config import mirror_agentqon_env as _mirror_agentqon_env

_mirror_agentqon_env()

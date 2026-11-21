"""AgentQon — original naming for the harvested rosters (v22.2).

Zohaib's order (2026-10-23): "i dont want the tag of reused" — no
harvested agent or skill may carry an upstream name or another
brand where a user can see it, and that now includes the IDs
themselves. This module is the single source of truth for turning
an upstream entry's own metadata (its parsed name / description /
body) into an original AgentQon role phrase, display name, and
clean deterministic ID slug:

- brand tokens (other AI products/vendors) are stripped,
- harvest/structural tokens (agents, subagents, categories, repo
  words, the source owner/repo's own tokens) are stripped,
- what remains is the entry's actual subject — its role — kept in
  the product's voice (title case, acronyms preserved).

Persona first names carried by an upstream entry are replaced
from a fixed neutral pool, chosen deterministically by body hash,
so society leads read as people ("<First> — <Role>") without
keeping the upstream persona's identity.

Identity rule: an agent's NEW id is derived from its role slug,
disambiguated deterministically by content hashes only — never
from file paths — so renaming the shipped data tree can never
change an ID. License/attribution metadata is untouched by this
module: provenance stays in the data files and manifest fields
where the licenses require it; only names and IDs are ours.
"""
from __future__ import annotations

import hashlib
import re

# ------------------------------------------------------------- tokens
# Other vendors / AI products: never in an AgentQon name or ID.
BRAND_TOKENS = {
    "claude", "anthropic", "openai", "chatgpt", "gpt", "codex",
    "copilot", "gemini", "cursor", "kiro", "opencode", "openclaw",
    "windsurf", "cline", "aider", "grok", "bard", "deepseek",
    "qwen", "llama", "mistral", "cohere", "perplexity", "tabnine",
    "kimi", "moonshot",
    "cody", "sourcegraph", "amazonq", "devin", "sweep", "codegen",
    "replit", "ghostwriter", "mutableai", "bito", "codium",
    "qodo", "aichat", "chatbotai",
}
# Harvest machinery, not subject matter.
STRUCTURAL_TOKENS = {
    "agent", "agents", "subagent", "subagents", "skill", "skills",
    "category", "categories", "plugin", "plugins", "persona",
    "personas", "library", "collection", "collections", "official",
    "repo", "github", "gitlab", "bitbucket", "com", "www", "http",
    "https", "md", "pack", "bundle", "awesome", "mega", "ultimate",
    "complete", "curated", "list", "index", "readme", "agentic",
}
# Series prefixes / framework shorthands / filler found in the data.
JUNK_TOKENS = {
    "cs", "gsd", "uc", "hve", "rpi", "the", "a", "an", "and", "of",
    "for", "with", "your", "you", "my", "all", "to", "in", "on",
    "by", "from", "at", "v", "ver", "version", "edition", "vol",
    "part", "no", "num", "misc", "general", "various", "other",
    "others", "new", "top",
}
# Display casing for terms a plain capitalize() would mangle.
ACRONYMS = {
    "ai": "AI", "ui": "UI", "ux": "UX", "seo": "SEO", "qa": "QA",
    "api": "API", "apis": "APIs", "sql": "SQL", "css": "CSS",
    "html": "HTML", "js": "JS", "ts": "TS", "ci": "CI", "cd": "CD",
    "aws": "AWS", "gcp": "GCP", "llm": "LLM", "llms": "LLMs",
    "mcp": "MCP", "sdk": "SDK", "cli": "CLI", "devops": "DevOps",
    "dotnet": ".NET", "cpp": "C++", "csharp": "C#", "ios": "iOS",
    "macos": "macOS", "saas": "SaaS", "crm": "CRM", "erp": "ERP",
    "etl": "ETL", "ml": "ML", "nlp": "NLP", "ocr": "OCR",
    "pdf": "PDF", "csv": "CSV", "json": "JSON", "xml": "XML",
    "http": "HTTP", "url": "URL", "urls": "URLs", "sast": "SAST",
    "ctf": "CTF", "soc": "SOC", "kpi": "KPI", "kpis": "KPIs",
    "roi": "ROI", "b2b": "B2B", "b2c": "B2C", "gdpr": "GDPR",
    "hipaa": "HIPAA", "pci": "PCI", "jwt": "JWT", "oauth": "OAuth",
    "graphql": "GraphQL", "grpc": "gRPC", "wasm": "Wasm",
    "iot": "IoT", "ar": "AR", "vr": "VR", "3d": "3D", "2d": "2D",
    "cicd": "CI/CD", "sre": "SRE", "tdd": "TDD", "bdd": "BDD",
    "rag": "RAG", "gpu": "GPU", "cpu": "CPU", "dns": "DNS",
    "ssl": "SSL", "tls": "TLS", "ssh": "SSH", "ftp": "FTP",
    "rest": "REST", "crud": "CRUD", "orm": "ORM", "ide": "IDE",
    "os": "OS", "db": "DB", "ci/cd": "CI/CD",
    "pr": "PR", "hr": "HR", "pm": "PM", "bi": "BI", "vm": "VM",
    "id": "ID", "ip": "IP", "ui/ux": "UI/UX",
}
# Words that mark a token sequence as a role, not a person.
ROLE_WORDS = {
    "expert", "engineer", "reviewer", "architect", "developer",
    "specialist", "analyst", "manager", "strategist", "security",
    "code", "designer", "optimizer", "data", "automation", "ai",
    "researcher", "content", "planner", "database", "resolver",
    "builder", "writer", "advisor", "hunter", "backend", "ui",
    "generator", "product", "design", "cloud", "frontend",
    "workflow", "project", "research", "devops", "qa", "ops",
    "python", "compliance", "tester", "mobile", "coach", "lead",
    "consultant", "auditor", "tester", "creator", "director",
    "growth", "prompt", "social", "rust", "customer", "testing",
    "react", "platform", "quality", "debugger", "java",
    "coordinator", "game", "documentation", "task", "validator",
    "checker", "operator", "systems", "brand", "sales", "legal",
    "network", "django", "kubernetes", "unity", "tracker",
    "deployment", "verifier", "kotlin", "graphql", "master",
    "assistant", "guide", "helper", "maker", "checker", "pro",
    "guru", "ninja", "wizard", "lead", "head", "chief", "senior",
    "junior", "principal", "staff", "freelance", "virtual",
    "digital", "technical", "business", "solutions", "systems",
    "support", "success", "operations", "marketing", "finance",
    "health", "medical", "legal", "creative", "full", "stack",
    "fullstack", "front", "end", "web", "app", "apps", "software",
    "hardware", "embedded", "dev", "admin", "administrator",
    "scope", "guard", "sentinel", "watch", "gate", "shield",
    "user", "commercial", "notifications", "notification",
    "customer", "client", "patient", "student", "employee",
    "analyzer", "synthesizer", "prototyper", "extractor",
    "sysadmin", "automator", "curator", "translator",
    "negotiator", "mediator", "supervisor", "inspector",
    "examiner", "forecaster", "fabricator", "librarian",
    "president", "politician", "historian", "communicator",
}
# Persona call-signs found in this corpus: single-word persona
# identities introduced as "You are <X>, ..." (Sentry, Ledger,
# Rank) or carried as the entry's whole name. They are persona
# identities, not roles - they are replaced from the pool below.
CALLSIGN_SET = {
    "Atlas", "Beacon", "Buzz", "Compass", "Echo", "Forge",
    "Gatekeeper", "Hawk", "Ledger", "Lens", "Orion", "Probe",
    "Pulse", "Radar", "Rank", "Scout", "Scribe", "Scroll",
    "Sentinel", "Sentry", "Trace", "Volt",
}
# Common first names for name-led persona detection ("<First>
# the <Role>", "<First> <Role...>"): deliberately short - the
# call-sign and "You are <Name>" signals do most of the work.
CURATED_FIRST = {
    "Aaron", "Abby", "Adam", "Adrian", "Agnes", "Aisha", "Alan",
    "Albert", "Alec", "Alejandro", "Alex", "Alexa", "Alexander",
    "Alexandra", "Alexia", "Alexis", "Alfred", "Alfredo", "Alice",
    "Alicia", "Alina", "Aline", "Alisa", "Alisha", "Alison",
    "Alissa", "Aliza", "Alla", "Allan", "Allen", "Allegra",
    "Alton", "Alva", "Alvin", "Alvis", "Alwin", "Alyce",
    "Alycia", "Alyda",
}
# The replacement pool for upstream persona first names: neutral,
# fixed, assigned deterministically by body hash.
FIRST_NAME_POOL = [
    "Adeel", "Alba", "Amara", "Anders", "Anika", "Arden",
    "Aria", "Arlen", "Arlo", "Asa", "Asha", "Aspen",
    "Astra", "Atlas", "Aubrey", "Audra", "Aurel", "Aurora",
    "Aveline", "Axel", "Azra", "Bailey", "Basil", "Beatrix",
    "Beckett", "Belen", "Bennett", "Berta", "Beryl", "Blaine",
    "Blair", "Blanca", "Bodhi", "Bonita", "Booker", "Bram",
    "Brea", "Brenna", "Bria", "Briar", "Brice", "Bridget",
    "Brina", "Brion", "Britta", "Brogan", "Bronwyn", "Brynn",
    "Cadence", "Caelan", "Cairo", "Calista", "Callum", "Calvin",
    "Camden", "Camila", "Candela", "Cannon", "Canyon", "Capri",
]
FIRST_NAME_POOL = sorted(set(FIRST_NAME_POOL))

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9.+/#]+")


def _raw_tokens(text: str) -> list[str]:
    out = []
    for tok in _TOKEN_SPLIT.split(str(text or "")):
        tok = tok.strip(".-_/")
        if tok:
            out.append(tok)
    return out


def source_tokens(owner: str = "", repo: str = "") -> set[str]:
    """Tokens belonging to the provenance itself (owner/repo
    words): harvest branding, dropped from names and IDs."""
    out = set()
    for part in (owner, repo):
        for tok in _raw_tokens(part):
            out.add(tok.lower())
    return out


def clean_tokens(text: str, extra_drop: set[str] | None = None) -> list[str]:
    """The subject tokens of a name: brands, structure, junk,
    numerics and provenance tokens removed; order and original
    casing of what survives preserved."""
    drop = BRAND_TOKENS | STRUCTURAL_TOKENS | JUNK_TOKENS
    if extra_drop:
        drop = drop | {t.lower() for t in extra_drop}
    out: list[str] = []
    seen: set[str] = set()
    for tok in _raw_tokens(text):
        low = tok.lower()
        if low in drop or low in seen:
            continue
        if low.isdigit() or (len(low) < 2 and low not in ACRONYMS):
            continue
        if re.fullmatch(r"v\d+(\.\d+)*", low):
            continue
        seen.add(low)
        out.append(tok)
    return out


def _display_token(tok: str) -> str:
    low = tok.lower()
    if low in ACRONYMS:
        return ACRONYMS[low]
    if any(c.isupper() for c in tok[1:]) or tok.isupper():
        return tok  # DevOps, GitHub, iOS-style casing already right
    return tok[:1].upper() + tok[1:]


def title_case_tokens(toks: list[str]) -> str:
    return " ".join(_display_token(t) for t in toks)


def _name_head(name: str) -> str:
    """The head of an upstream title: parenthetical and bracketed
    tails are qualifiers, not the role ("Scope Guard (Shared
    Prompt Block)" -> "Scope Guard")."""
    return re.split(r"[(\[]", str(name or ""), maxsplit=1)[0].strip()


def role_phrase(name: str, description: str = "",
                extra_drop: set[str] | None = None,
                hint: str = "") -> str:
    """The entry's role in the product's voice, from its own
    metadata. Falls back to mining the description's opening
    ("You are a …" / "Expert in …"), then to the caller's hint
    (a division or path category), then to a plain Specialist
    label — a name is always produced."""
    toks = clean_tokens(_name_head(name), extra_drop)
    if toks:
        return " ".join(_display_token(t) for t in toks)
    desc = str(description or "")
    m = re.search(r"[Yy]ou are an?\s+([^.,;]{3,60})", desc)
    if not m:
        m = re.search(r"^[Aa]n?\s+([^.,;]{3,60}?)(?:\s+who\b|\s+that\b|[.,;])",
                      desc)
    if m:
        toks = clean_tokens(m.group(1), extra_drop)
        # a mined phrase reads as a role only if it is short and
        # carries a role-ish word; otherwise it is task text
        if toks and len(toks) <= 5 and any(
                t.lower() in ROLE_WORDS for t in toks):
            return " ".join(_display_token(t) for t in toks)
    toks = clean_tokens(desc.split(".")[0], extra_drop)
    # The longest contiguous run of role words in the opening
    # ("Expert data analyst transforming raw data..." -> "Expert
    # Data Analyst") reads as a role; scattered role words do not.
    best: list[str] = []
    run: list[str] = []
    for t in toks:
        if t.lower() in ROLE_WORDS or t.lower() in ACRONYMS:
            run.append(t)
        else:
            if len(run) > len(best):
                best = run
            run = []
    if len(run) > len(best):
        best = run
    if len(best) >= 2:
        return " ".join(_display_token(t) for t in best[:4])
    if best:
        return " ".join(_display_token(t) for t in best[:4])
    toks = clean_tokens(hint, extra_drop)
    if toks:
        return " ".join(_display_token(t) for t in toks) + " Specialist"
    return "Specialist"


def detect_persona_first(name: str,
                         description: str = "") -> tuple[str, int] | None:
    """The upstream persona identity carried by an entry, if
    any, as (first_token, span): how many leading title tokens
    are the persona's own name. Signals: a call-sign ("You are
    Sentry, ...", or the whole name is the call-sign), a first
    name leading the title ("<First> the <Role>"), a bare
    personal name as the whole Title-Case title (the
    "politicians" pattern - role titles always carry a role
    word and never match), or the description introducing the
    persona by that exact name. Precise signals only - a role
    word is never mistaken for a person."""
    toks = _raw_tokens(_name_head(name))
    if len(toks) == 1 and toks[0] in CALLSIGN_SET:
        return toks[0], 1
    if toks and toks[0] in CURATED_FIRST:
        rest = clean_tokens(" ".join(toks[1:]))
        if len(toks) == 1 or rest:
            return toks[0], 1
    if toks and toks[0] in CALLSIGN_SET and len(toks) > 1:
        return toks[0], 1
    raw_toks = _name_head(name).split()
    if 2 <= len(raw_toks) <= 4 and all(
            re.fullmatch(r"[A-Z][A-Za-z]+(-[A-Z][A-Za-z]+)?|[A-Z]\.",
                         t) for t in raw_toks):
        lows = [t.lower().strip(".") for t in raw_toks]
        # Occupation-shaped tokens (Reporter, Tuner, Assessor)
        # mark a role compound, not a person.
        occupation_shaped = any(
            low.endswith(("er", "or", "ist", "yst", "ian", "ess"))
            and low not in ("her", "per") for low in lows)
        # A description that opens functionally (a verb, a
        # gerund, a seniority word) describes a job, not a
        # character sketch.
        openers = {"use", "used", "expert", "specialist",
                   "senior", "professional", "certified",
                   "dedicated", "experienced", "seasoned",
                   "skilled", "helps", "help", "creates", "builds",
                   "designs", "develops", "writes", "reviews",
                   "analyzes", "analyses", "transforms",
                   "normalizes", "delivers", "provides", "supports",
                   "manages", "generates", "automates", "audits",
                   "tests", "debugs", "implements", "maintains",
                   "optimizes", "coordinates", "oversees"}
        desc_first = [t.lower() for t in _raw_tokens(
            str(description or ""))][:2]
        functional = any(t in openers or t.endswith("ing")
                         for t in desc_first)
        if not occupation_shaped and not functional and not any(
                low in ROLE_WORDS or low in ACRONYMS
                or low in BRAND_TOKENS or low in STRUCTURAL_TOKENS
                or low in JUNK_TOKENS for low in lows):
            return raw_toks[0], len(raw_toks)
    if 2 <= len(toks) <= 3 and all(t.isalpha() for t in toks):
        phrase = " ".join(toks).lower()
        head = re.split(r"[.,;]", str(description or ""), maxsplit=1)[0]
        if re.search(r"[Yy]ou are\s+" + re.escape(phrase) + r"\b",
                     head):
            return toks[0], len(toks)
    m = re.search(
        r"[Yy]ou are\s+([A-Z][a-z]{2,15})"
        r"(?=\s*[,.:!\u2014-]|\s+and\b|\s+who\b|\s*$)",
        str(description or ""))
    if m and m.group(1) in CALLSIGN_SET | CURATED_FIRST:
        # The persona's name came from the description; the
        # title itself is the role unless it leads with it.
        if toks and toks[0] == m.group(1):
            return m.group(1), 1
        return m.group(1), 0
    return None


def pool_first_name(body_sha: str) -> str:
    n = int(hashlib.sha256(body_sha.encode()).hexdigest()[:8], 16)
    return FIRST_NAME_POOL[n % len(FIRST_NAME_POOL)]


def agent_display_name(name: str, description: str, body_sha: str,
                       extra_drop: set[str] | None = None,
                       hint: str = "") -> str:
    """Full display name for one roster agent: '<First> — <Role>'
    when the entry carried a persona first name, else the role."""
    det = detect_persona_first(name, description)
    if not det:
        return role_phrase(name, description, extra_drop, hint)
    _first, span = det
    # The role is what follows the persona's own name in the
    # upstream title; when the persona consumed the whole title
    # (a bare personal name), the role comes from the
    # description / hint instead — never from a surname.
    toks = _raw_tokens(_name_head(name))
    role_name = " ".join(toks[span:]) if span < len(toks) else ""
    role = role_phrase(role_name, description, extra_drop, hint)
    return f"{pool_first_name(body_sha)} — {role}"


def role_slug(phrase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) > 64:
        slug = slug[:64].rsplit("-", 1)[0].strip("-") or slug[:64]
    return slug or "specialist"


# Verbs and filler that may never serve as a display qualifier
# (a qualifier is a subject word: "· Rust", "· Security").
QUALIFIER_STOP = {
    "use", "used", "using", "uses", "when", "help", "helps",
    "create", "creates", "build", "builds", "write", "writes",
    "make", "makes", "get", "gets", "provide", "provides",
    "ensure", "ensures", "work", "works", "take", "takes",
    "give", "gives", "keep", "keeps", "turn", "turns", "let",
    "lets", "set", "sets", "put", "puts", "run", "runs",
    "based", "generate", "generates", "generated", "need",
    "needs", "want", "wants", "like", "way", "ways", "one",
    "two", "first", "also", "just", "even", "still", "back",
    "are", "is", "has", "have", "had", "does", "did", "will",
    "would", "can", "could", "should", "may", "might", "must",
    "protects", "protect", "allows", "allow", "enables",
    "enable", "includes", "include", "offers", "offer",
    "brings", "shows", "show", "finds", "drives", "hands",
    "comes", "goes", "seems", "becomes", "means", "helps",
}


# ------------------------------------------------- bulk assignment
# Shared verbatim by the offline build (tools/build_display_names)
# and the runtime importers (app/agency.py, app/library.py): the
# same records in, the same display names and IDs out — ID
# assignment is a pure function of corpus content.

def agent_qualifiers(rec: dict) -> list[str]:
    """Ordered distinguishing words for one agent record, from
    its own description and category hint — used to separate
    agents whose role display would otherwise collide."""
    out: list[str] = []
    have = {t.lower() for t in _raw_tokens(rec.get("display", ""))}
    drop = source_tokens(rec.get("owner", ""), rec.get("repo", ""))
    for tok in clean_tokens((rec.get("description") or "").split(".")[0],
                            drop):
        if tok.lower() not in have and tok.lower() not in QUALIFIER_STOP \
                and not tok.lower().endswith("ing"):
            out.append(title_case_tokens([tok]))
            have.add(tok.lower())
        if len(out) >= 4:
            break
    hint = rec.get("division") or ""
    if not hint and rec.get("seed"):
        parts = str(rec["seed"]).split("/")
        hint = parts[-2] if len(parts) >= 2 else ""
    for tok in clean_tokens(hint):
        if tok.lower() not in have:
            out.append(title_case_tokens([tok]))
    return out


def disambiguate_displays(records: list[dict], qualifiers_of) -> int:
    """Make display names unique within the list: try each
    record's qualifier candidates (' · <Qualifier>') in order,
    then a numeric suffix. Deterministic (old-id order).
    Returns how many records needed a qualifier or suffix."""
    used: set[str] = set()
    touched = 0
    for rec in sorted(records, key=lambda r: r["old_id"]):
        base = rec["display"]
        chosen = None
        for cand in [base] + [f"{base} · {q}"
                              for q in qualifiers_of(rec)]:
            if cand not in used:
                chosen = cand
                break
        if chosen is None:
            n = 2
            while f"{base} ({n})" in used:
                n += 1
            chosen = f"{base} ({n})"
        if chosen != base:
            touched += 1
        rec["display"] = chosen
        used.add(chosen)
    return touched


def assign_agent_ids(records: list[dict]) -> None:
    """New IDs from the (disambiguated) displays' role slugs,
    disambiguated by content hashes only. Records carry: kind
    ('lib'|'agency'), division (agency), display, body_sha,
    name, description."""
    groups: dict[str, list[dict]] = {}
    for rec in records:
        role = rec["display"].split(" — ", 1)[-1]
        rec["slug"] = role_slug(role)
        prefix = ("lib" if rec["kind"] == "lib"
                  else f"agency.{rec['division']}")
        groups.setdefault(f"{prefix}.{rec['slug']}", []).append(rec)
    taken = set(groups)  # every base id is spoken for by its owner
    for base_id in sorted(groups):
        group = groups[base_id]
        group.sort(key=lambda r: (r["body_sha"],
                                  hashlib.sha256(
                                      (r["name"] + "\n"
                                       + r["description"]).encode()
                                  ).hexdigest()))
        for i, rec in enumerate(group):
            if i == 0:
                rec["new_id"] = base_id
                continue
            n = i + 1
            while f"{base_id}-{n}" in taken:
                n += 1
            rec["new_id"] = f"{base_id}-{n}"
            taken.add(rec["new_id"])

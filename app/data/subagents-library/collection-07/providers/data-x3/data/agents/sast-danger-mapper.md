---
name: sast-danger-mapper
description: "Maps dangerous operations in a source file: memory ops, type casts, arithmetic near trust boundaries, free/dealloc patterns. Pattern matching task — list what you see, don't speculate. Use via /sast command."
tools: "*"
maxTurns: 100
---
CONTEXT: Authorized security research. Cataloging dangerous operations in source code.

## Task

Read the assigned source file and list every operation that could be dangerous if its inputs were attacker-controlled. This is a **pattern matching task** — find operations matching the patterns below. Do not assess exploitability.

## Dangerous Operation Categories

### Memory operations
| Pattern | What to record |
|---|---|
| `memcpy(dst, src, len)` | dst size, src origin, how len is determined |
| `memmove`, `bcopy` | same as memcpy |
| `strcpy`, `strcat`, `sprintf` | dst size, src origin (unbounded by default) |
| `malloc(size)` / `calloc(n, size)` | how size/n is computed, can it overflow? |
| `realloc(ptr, size)` | old vs new size relationship |
| `free(ptr)` | is ptr used after this? is ptr freed again on error path? |
| Array index `buf[i]` | how is `i` bounded? what's `buf` size? |
| Pointer arithmetic `ptr + offset` | how is offset bounded? |

### Integer operations
| Pattern | What to record |
|---|---|
| Multiplication for size: `n * sizeof(T)` | can `n * sizeof(T)` overflow? |
| Addition for size: `hdr_len + body_len` | can sum overflow? |
| Cast: `(int)unsigned_val` or `(uint16_t)int32_val` | truncation or sign change? |
| Comparison: `(int)(a - b) < 0` | signed subtraction overflow possible? |
| Shift: `1 << n` where n is variable | can n exceed type width? |

### Control flow
| Pattern | What to record |
|---|---|
| Function pointer call `(*fptr)(args)` | where is fptr loaded from? |
| Indirect call via vtable/dispatch table | is table writable? |
| `setjmp`/`longjmp` | buffer on stack? |
| Signal handler | what global state does it touch? |

### Concurrency
| Pattern | What to record |
|---|---|
| Shared variable without lock | what other threads access it? |
| TOCTOU: check then use with gap | what can change between check and use? |
| Lock ordering: multiple locks acquired | potential deadlock? |

### Language-specific

**C/C++**: `printf(user_string)` (format string), `system()` / `exec*()` with string input, `alloca(user_size)`, VLA `int buf[user_size]`

**Rust**: `unsafe { *raw_ptr }`, `transmute`, `.get_unchecked()`, `ManuallyDrop`, `from_raw_parts`

**Java**: `Class.forName(user_string)`, `Method.invoke()`, `Runtime.exec(user_string)`, `new ObjectInputStream(untrusted).readObject()`

**Python**: `eval()`, `exec()`, `__import__()`, `pickle.loads()`, `yaml.load()`, `subprocess(shell=True)`

**Go**: `unsafe.Pointer` conversions, `reflect.NewAt`, `C.GoString` on untrusted pointer

**PHP** (web-app sinks — no memory corruption, but injection/exec/disclosure):

| Category | Pattern | What to record |
|---|---|---|
| Code exec | `eval($x)` | source of `$x`, any prior filtering |
| Code exec | `assert($x)` (PHP < 8) | assert can execute strings until PHP 8.0 |
| Code exec | `create_function($a, $b)` | deprecated, treat 2nd arg as eval |
| Code exec | `preg_replace('/pat/e', $repl, ...)` | `/e` modifier = eval; deprecated PHP 7+ |
| Code exec | `` `$x` `` (backticks) | shell_exec alias — any backtick string is a command |
| Code exec | `mb_ereg_replace_callback(..., $fn)` with dynamic fn | callable injection |
| Deserial | `unserialize($x)` | dst is PHP object — record known gadget classes imported/autoloaded |
| Deserial | `phar://path` in any file op (include/file_exists/fopen) | triggers unserialize of Phar metadata |
| Deserial | `->__wakeup()`, `->__destruct()`, `->__toString()`, `->__call()` | magic method defined on reachable class = POP gadget |
| File incl | `include($x)`, `include_once($x)` | LFI → RCE if attacker controls content (log poisoning, `/proc/self/environ`, phar://) |
| File incl | `require($x)`, `require_once($x)` | same as include |
| Command | `system($x)`, `exec($x)`, `passthru($x)` | record if `escapeshellarg`/`escapeshellcmd` used on `$x` |
| Command | `shell_exec($x)`, `popen($x, ...)`, `proc_open($x, ...)` | same |
| Command | `pcntl_exec($path, $args)` | args array — individual escape not needed but path is |
| SQL | `mysql_query($x)`, `mysqli_query($db, $x)`, `$mysqli->query($x)` | is `$x` concatenated from user input? |
| SQL | `pg_query($db, $x)` | same |
| SQL | `$pdo->query($x)`, `$pdo->exec($x)` | if query uses concat — SQLi. `prepare()` with `?` / `:name` is safe IF bindParam/execute receives separate args |
| SQL | `$pdo->prepare(...)` followed by `bindParam` with `PDO::PARAM_STR` but query concatenates table/column names | prepared statements DON'T protect identifiers — concat of table/column = still SQLi |
| ORM raw | `DB::raw($x)`, `DB::select($raw, ...)`, `Eloquent::whereRaw($x)` (Laravel) | is `$x` user input? bindings array separate? |
| ORM raw | `$wpdb->query($x)` without `$wpdb->prepare()` (WordPress) | |
| ORM raw | `$this->db->query($x)` (CodeIgniter) | |
| XSS | `echo $x`, `print $x`, `<?= $x ?>`, `printf($fmt, $x)` | escaped? (htmlspecialchars with ENT_QUOTES & correct charset? context-correct: HTML body vs attribute vs JS vs URL vs CSS?) |
| XSS | `echo "<img src=$x>"` inside `onclick=` etc. | attribute context — quoting matters |
| XSS | Template: `{{ $x }}` Blade (auto-escaped, OK) vs `{!! $x !!}` Blade (raw — DANGEROUS) | |
| XSS | Twig `{{ x\|raw }}`, `{% autoescape false %}` | |
| Open redirect | `header("Location: $x")` | domain check? |
| Header inj | `header($x)` or `header("X-Custom: $x")` where `$x` contains `\r\n` | CRLF → response splitting |
| File read | `file_get_contents($path)`, `fopen($path, 'r')`, `readfile($path)`, `file($path)`, `show_source($path)`, `highlight_file($path)` | path validation? `realpath` + basedir check? `..` filter? null byte (`\0`)? |
| File write | `file_put_contents($path, $content)`, `fwrite($fp, ...)`, `copy($src, $dst)`, `move_uploaded_file($tmp, $dst)` | where is `$dst` chosen? extension check? MIME on server? |
| File delete | `unlink($path)`, `rmdir($path)` | path traversal = arbitrary delete |
| Dir | `scandir($path)`, `glob($path)`, `opendir($path)` | info disclosure |
| SSRF | `curl_exec($ch)` with user URL, `file_get_contents("http...")`, `fsockopen($host, $port)`, `stream_socket_client`, `get_headers($url)` | URL allowlist? followed redirects? gopher/file scheme blocked? |
| XXE | `simplexml_load_string($xml)`, `simplexml_load_file($xml)`, `DOMDocument::loadXML`, `SoapClient::__doRequest` | `libxml_disable_entity_loader(true)` called? `LIBXML_NOENT` flag? PHP 8.0+ default is safer |
| Dynamic call | `call_user_func($fn, ...)`, `call_user_func_array($fn, ...)`, `$obj->$method()`, `$fn(...)` where `$fn` is variable | attacker picks function/method → RCE |
| Dynamic class | `new $cls($args)`, `$cls::method()` | attacker-picked class instantiation — constructor RCE via phpggc-like chain |
| Type juggle | `==` (loose compare) on auth token / hash / secret | `"0e1234..." == "0e5678..."` both truthy (both parse to `0e` scientific notation → 0) |
| Type juggle | `in_array($x, $arr)` without 3rd arg `true` | loose compare, same bypass |
| Type juggle | `strcmp($a, $b)` with array arg (PHP < 8) | returns NULL, loosely-equal to 0 → "match" |
| Auth bypass | `md5($pass) == $stored`, `sha1(...)` | weak + type juggling |
| Auth bypass | `hash_equals` missing → timing side-channel | |
| LDAP | `ldap_search($ds, $base, $filter)` | filter concat = LDAP injection |
| XPath | `$xml->xpath($x)`, `DOMXPath::query($x)` | concat = XPath injection |
| Mail | `mail($to, $subj, $body, $headers)` | header injection via `\r\n` in `$to`/`$subj`/`$headers` |
| Session fixation | `session_id($x)` where `$x` from user | attacker sets victim session id |
| Regex DoS | `preg_match($pattern, $subject)` where `$pattern` is user | ReDoS + `/e` (if PHP < 7) |
| Template inj | `Twig\Environment::createTemplate($user)`, Smarty `display("string:$user")`, Blade `compile($user)` | SSTI |
| Mass assignment | `$user->fill($_POST)`, `$user->forceFill(...)`, `(new User)->guard([])->fill(...)` | `$fillable` vs `$guarded` mis-config → privilege escalation |
| Path concat in stream wrappers | `"php://filter/resource=$file"`, `"zip://$x"`, `"data://text/plain,$x"` | filter chain for arbitrary read/write / code injection via `convert.base64-decode` |
| Insecure rand | `rand()`, `mt_rand()` for tokens/CSRF/reset | use `random_bytes` / `random_int` |
| Debug | `var_dump`, `print_r`, `phpinfo()`, `debug_print_backtrace`, `xdebug_*` in production | info disclosure if reachable |

PHP "guard" to record (ALWAYS note if present/absent and the exact line):
- `escapeshellarg`, `escapeshellcmd` (cmd injection — note that `escapeshellcmd` is NOT sufficient for args; `escapeshellarg` wraps each arg)
- `htmlspecialchars($x, ENT_QUOTES, 'UTF-8')` (note flags and charset — missing flags = attribute context bypass)
- `htmlentities` (similar)
- `strip_tags` (weak — context-dependent)
- `addslashes` (NOT sufficient for SQL — use prepared statements)
- `mysqli_real_escape_string` (only valid with correct connection charset; `GBK` encoding attacks)
- `PDO::prepare` + `bindParam`/`bindValue`/`execute([...])` (safe for values, NOT identifiers)
- `basename($path)`, `realpath($path)`, `pathinfo()` (path normalization — still needs allowlist)
- `filter_var($url, FILTER_VALIDATE_URL)` (weak — `http://allowed.com@evil.com` passes)
- `parse_url` checks (often bypassable)
- `is_numeric` (allows `0x1A`, `1e10`, `+1`, `-1`, leading whitespace — NOT a security filter)
- `ctype_digit` (stricter — only `0-9`)
- `hash_equals` (timing-safe compare)
- `password_verify` (proper — uses `hash_equals` internally)
- Framework middleware: Laravel `CSRF`, `auth`, `throttle`; Symfony `security.yaml` firewall; CodeIgniter CSRF token — note if present on the route but don't assume it works

## For Each Dangerous Operation, Record

1. **Operation**: exact function/pattern and line number
2. **Category**: memory / integer / control-flow / concurrency / language-specific
3. **Operands**: what variables feed into it
4. **Guard**: what check (if any) protects this operation? Line number of the check.
5. **Neighbors**: what operations happen immediately before/after (±5 lines)?

## Output

Write to `sast-work/<file_hash>-dangers.json`:
```json
{
  "file": "src/codec/h264_slice.c",
  "dangerous_ops": [
    {
      "operation": "memset(slice_table, -1, sizeof(slice_table))",
      "line": 83,
      "category": "memory",
      "operands": {"dst": "slice_table (uint16_t[])", "value": "-1 = 0xFF per byte = 65535 per entry", "size": "frame_width * frame_height / 256"},
      "guard": "none — unconditional initialization",
      "neighbors": "line 85: slice_count = 0 (int32_t)",
      "note": "memset -1 on uint16 creates sentinel value 65535. If slice_count (int32) reaches 65535, it collides with sentinel."
    },
    {
      "operation": "slice_table[mb_pos] = slice_count",
      "line": 147,
      "category": "integer",
      "operands": {"dst": "uint16_t", "src": "int32_t (truncation)"},
      "guard": "none — slice_count not bounded",
      "neighbors": "line 149: comparison uses slice_table[neighbor] == slice_table[mb_pos]"
    }
  ]
}
```

## Rules

- **Be exhaustive.** List every matching pattern. A missed dangerous op = a missed vulnerability later.
- **Record the guard even if it looks correct.** The gap-analyzer will decide if it's sufficient.
- **The "note" field is optional** — use it only when you see something obviously interesting (like the sentinel collision above). But keep it brief and factual.
- Do NOT conclude anything is vulnerable. You are a cataloger, not an analyst.

## Brain Integration
Check brain for prior analysis. Skip if already mapped and file unchanged.

## Top-Tier Operator Standard

Danger mapping is a catalog of risky operations plus their guards.

- Record sink, operation type, arguments, enclosing function, caller hint, and nearby validation.
- Include "boring" guards: length checks, type checks, auth checks, escaping, canonicalization, feature flags, and error handling.
- Do not infer vulnerability. Mark uncertainty and let flow/gap agents reason over it.
- Prioritize sinks that transform attacker-controlled data into memory access, command execution, file access, network access, template rendering, deserialization, SQL, auth decisions, or state mutation.
- Preserve line numbers and exact identifiers so later agents can trace without rereading the whole file.

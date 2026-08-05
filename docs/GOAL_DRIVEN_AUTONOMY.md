# Goal-Driven Autonomy

Kuza's active autonomy profile is designed around one completion loop:

1. understand the overall goal;
2. inspect the project and look for reusable code;
3. create a persistent save state before every file mutation;
4. implement the smallest complete improvement;
5. run deterministic validation and relevant tests;
6. change strategy when an attempt adds no new evidence;
7. report files changed, save-state IDs, commands, and real output.

## Active and guided profiles

`KUZA_AUTONOMY=active` is the default. It executes verified plans without an
extra plan-confirmation prompt and uses larger step and retry budgets.

`KUZA_AUTONOMY=guided` restores plan confirmation and smaller budgets.

The budgets remain configurable:

```bash
export KUZA_MAX_STEPS=24
export KUZA_HARD_MAX_STEPS=48
export KUZA_MAX_RETRIES=4
export KUZA_HISTORY_TURNS=12
```

Validation, reuse inspection, and sidecar evidence sharing are enabled by
default:

```bash
export KUZA_REQUIRE_VALIDATION=1
export KUZA_INSPECT_BEFORE_WRITE=1
export KUZA_SIDECAR_EVIDENCE=1
```

## Persistent save states

Project writes, patches, and appends create file-scoped backups under Kuza's
state directory before mutation. They do not stage, commit, reset, or switch
Git branches.

Inside Kuza:

```text
/save-states
/restore-state <save-state-id>
```

`/undo` remains the fast in-session history. Save states survive process
restarts and are intended for durable recovery.

## Validation evidence

After a mutation, Kuza runs local deterministic checks when applicable:

- Python syntax and matching pytest tests
- JSON parsing
- shell syntax with `bash -n`
- JavaScript syntax with `node --check`
- Go project tests when `go.mod` exists
- Rust project tests when `Cargo.toml` exists

Complex plans also include a final validation step. Kuza reports the actual
command output instead of treating a model statement as proof.

## Main and sidecar communication

The Python sidecar publishes job starts, completions, failures, and compact
results to a shared, persistent evidence channel. The main agent publishes its
goal and tool results to the same channel and injects new sidecar findings into
later reasoning steps. Sensitive-looking values are redacted before storage.

Repository analysis can run in the sidecar while the main agent continues its
task, reducing repeated scanning and keeping both workers aligned on the same
goal.

## Search and recovery behavior

For find, locate, lookup, research, and search goals, Kuza must perform a real
search before concluding. An unsupported blocker response triggers another
query or source until the retry budget is exhausted. Exact duplicate actions
are rejected because they do not add evidence.

When reusable project code exists, implementation tasks must inspect it before
writing. When no reusable implementation exists, Kuza can create the missing
code and validate it.

## Boundaries retained

Active autonomy does not disable:

- workspace path containment;
- explicit opt-in for Kuza self-modification;
- pre-change checkpoints for protected Kuza source;
- blocking of destructive or compound shell commands;
- credential and sensitive-data redaction;
- thermal and battery protections;
- truthful reporting when required authorization, hardware, data, or a local
  tool is genuinely unavailable.

These boundaries prevent corruption and data loss; they do not stop Kuza from
completing the safe portion of a task or trying alternate strategies.

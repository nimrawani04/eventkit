# eventkit: Product Requirements Document

Author: Milad | Version 1.1 | 21 Oct 2026 | Event: OmniRush Frontier Buildathon 2026, Agents & Automation track

## 1. Summary

eventkit is a command-line tool that does a campus event's paperwork start to finish. It reads a registration CSV, a schedule CSV and an optional attendance CSV, and writes a ready-to-use kit: a cleaned roster with every problem explained, a printable check-in sheet, a searchable offline event-day check-in surface, a calendar file and agenda with schedule clashes flagged, email drafts, signed credentials with revocations, and certificates carrying codes that anyone can verify offline.

It uses only the Python standard library, makes no network calls and no AI calls at runtime, and produces byte-identical output for the same inputs. It supports dry runs, an audit log and a safe undo. Every run ends with a report that separates what was verified from what still needs a human.

## 2. Problem and opportunity

Organizers of hackathons, conferences and club events repeat the same chore after every registration form closes: clean a messy export, spot duplicates and typos, print check-in lists, build a schedule, check for room and speaker clashes, then produce and email certificates. It is slow, error-prone and usually done by hand in spreadsheets. Certificates are easy to fake, and nobody can check one later.

Most tools cover only one piece of this, and few explain what they changed. eventkit runs the whole flow from one config file and states the reason for every decision. The requirements come from first-hand experience organizing campus hackathons and conferences.

## 3. Goals, non-goals and success metrics

### Goals

1. Turn raw CSV exports into a complete event kit with one command.
2. Never silently drop or change data: every flagged row and every decision is explained.
3. Issue certificates that can be verified offline, without contacting the organizer.
4. Be safe to run: dry run first, never overwrite edited files, undo with one command.
5. Rebuild from a clean clone in Docker and pass all tests offline.

### Non-goals (version 1)

- No sending of email. eventkit only writes drafts.
- No network access, no calls to any AI or email service at runtime.
- No PDF output. Certificates are SVG and can be printed to PDF from a browser.
- No hosted web UI, database or hosted service. Generated local HTML surfaces are allowed and must remain self-contained.
- No payments, ticketing or QR codes.

### Success metrics

| Metric | Target |
| --- | --- |
| Sample run (`init` then `run`) | Under 2 seconds |
| Roster of 5,000 rows | Under 5 seconds |
| Second run on unchanged inputs | Byte-identical files, all reported unchanged |
| Generated certificates that verify against the manifest | 100% |
| Fixture sets | At least 12 |
| Automated test cases | At least 60 |
| Docker build, then tests with `--network none` | Pass on a clean machine |
| Third-party runtime dependencies | Zero |

## 4. Users and scenarios

| Persona | Need | How eventkit helps |
| --- | --- | --- |
| Club or hackathon organizer | Prepare an event from a messy form export | One command, every issue listed with row numbers |
| Conference coordinator | Build a conflict-free schedule and calendar file | Room and speaker clash detection, ICS and agenda |
| Certificate recipient or employer | Check that a certificate is genuine | `eventkit verify` against the published manifest |

User stories:

- As an organizer, I run `eventkit run --dry-run` to see exactly what would be created before anything is written.
- As an organizer, I open `issues.md` and see each flagged registration with its row number, the reason and a suggested fix.
- As a coordinator, I am told that two sessions share a room or that a speaker is double-booked.
- As a recipient, I scan or type the code on my certificate and see VALID, or INVALID if a character is wrong.
- As an organizer, I run `eventkit clean` and the kit disappears, except files I edited.

## 5. Scope

**MVP (must ship):** `init`, `run`, `verify`, `clean`; roster cleaning and validation; check-in sheet; schedule checks, ICS and agenda; certificates with manifest; email drafts; report with Verified and Unresolved sections; dry run, audit log; fixtures and tests; Docker, README, lockfile, pinned runtime.

**Cut order if time runs short:** email drafts first, then the agenda page, then `clean`. Roster, check-in, certificates and verify are never cut.

## 6. Functional requirements

### 6.1 Commands

```
eventkit init [DIR]
eventkit run EVENT_TOML [--dry-run] [--strict] [--out DIR]
eventkit verify CODE --manifest PATH
eventkit clean EVENT_TOML [--dry-run]
eventkit review EVENT_TOML [--public]
eventkit revoke EVENT_TOML CODE --reason TEXT [--out PATH]
eventkit --version
eventkit --help
```

`init` writes a sample `event.toml` and sample CSVs that contain deliberate problems, so the README quickstart and the demo work without real data.

| Exit code | Meaning |
| --- | --- |
| 0 | Success. For `verify`: the code is valid |
| 1 | `run --strict` found error-level issues, or `verify` found the code invalid |
| 2 | Usage, config or input error, or a required secret is missing. Nothing is written. Expected errors never print a traceback |

### 6.2 Inputs

Configuration lives in one `event.toml`, read with `tomllib`:

```
[event]
id = 'demo-summit-2026'
name = 'Demo Summit 2026'
organizer = 'Campus Tech Club'
utc_offset = '+05:30'

[input]
registrations = 'data/registrations.csv'
schedule = 'data/schedule.csv'
attendance = 'data/attendance.csv'
review = 'review.jsonl'

[columns]
name = 'Full Name'
email = 'Email Address'
phone = 'Phone'
institution = 'College'

[certificates]
title = 'Certificate of Participation'
issue_to = 'attended'
manifest_names = true
signed = true
revocations = 'data/revocations.json'

[output]
dir = 'kit'
profile = 'private'
steps = ['roster', 'checkin', 'checkin-app', 'schedule', 'emails', 'certificates', 'report']
```

| File | Required columns | Notes |
| --- | --- | --- |
| Registrations | name, email (mapped by `[columns]`) | `phone` and `institution` optional. Extra columns are ignored and listed in the report |
| Schedule | start, end, title, room | `speaker` optional. Times are `YYYY-MM-DD HH:MM`, local to `utc_offset` |
| Attendance (optional) | email | `role` optional, default Participant |

All CSVs are read as UTF-8 with or without a BOM. Quoted fields with commas and newlines work, blank lines are skipped, and issues cite CSV line numbers. With `issue_to = 'attended'` and no attendance file, the certificate step is skipped and the report says why.

### 6.3 Normalization

- Names: trim, collapse whitespace, Unicode NFC. Never change case or spelling, because names in many scripts have no meaningful title case.
- Emails: trim, lowercase for comparison. The original is kept in the roster.
- Phones: keep digits and a leading plus.
- Row order: preserved from the file. Output sorts are explicit and stable.

### 6.4 Issue catalogue

| ID | Level | Rule | Effect |
| --- | --- | --- | --- |
| EK01 | error | Name or email missing | Row excluded |
| EK02 | error | Email syntactically invalid | Row excluded |
| EK03 | error | Exact duplicate email (case-insensitive) | First row kept, later rows excluded |
| EK04 | warning | Possible duplicate: same phone with a different email, or name similarity of 0.92 or more at the same institution | Row kept, flagged |
| EK05 | warning | Phone has fewer than 8 or more than 15 digits | Row kept, flagged |
| EK06 | warning | Email domain looks like a typo of a common one, with a suggestion | Row kept, never auto-fixed |
| EK07 | warning | Attendance row not in the roster, or matches an excluded row | No certificate issued |
| SC01 | error | Session end not after start, or a time cannot be parsed | Session excluded |
| SC02 | error | Two sessions overlap in the same room (end equal to start is fine) | Both kept, flagged |
| SC03 | error | Same speaker in overlapping sessions | Both kept, flagged |

`roster.csv` contains every row with a `status` (ok or excluded) and the issue IDs. `issues.md` groups issues by level with row numbers and a suggested fix. With `--strict`, any error-level issue gives exit code 1.

### 6.5 Pipeline steps

| Step | Output | Rules |
| --- | --- | --- |
| roster | `roster.csv`, `issues.md` | As above |
| checkin | `checkin.html` | Sorted by name, then code. Columns: number, name, institution, check-in code, tick box. Print stylesheet. Excluded rows omitted |
| checkin-app | `checkin-app.html`, `checkin.log` | Self-contained searchable event-day surface. Local check-in/undo actions persist in browser storage and export JSONL. Unknown codes and duplicate transitions are rejected by the runtime API |
| schedule | `schedule.ics`, `agenda.html` | UTC times, CRLF line endings, lines folded at 75 octets, text escaped. UID is a hash of event id, title and start. DTSTAMP is derived from the start time, so output is deterministic. Agenda is grouped by day with clashes marked |
| emails | `emails/*.eml`, `mailmerge.csv` | Drafts only. Built with the standard `email` package, with an `X-Unsent` header, no Date header, template fields `{name}`, `{event}`, `{code}`, and the certificate attached |
| certificates | `certificates/*.svg`, `manifest.json`, optional `credentials.json` | See 6.6. Signed credentials are enabled with `[certificates].signed = true` |
| report | `report.md` | See 6.8 |

Generated filenames are an ASCII slug of the name plus the check-in code. If the slug is empty, as with non-Latin names, the name part is `attendee`.

### 6.6 Codes and verification

- The signing secret comes only from the `EVENTKIT_SECRET` environment variable, at least 16 characters, with no default. If an enabled step needs it and it is missing, the tool exits with code 2 before writing anything.
- Certificate code: HMAC-SHA256 over `cert`, event id, normalized email and role, joined with a separator. The first 60 bits become 12 Crockford base32 characters, shown as XXXX-XXXX-XXXX.
- Check-in code: the same construction with a `checkin` prefix, 6 characters, lengthened on collision.
- `manifest.json` holds the event id and name, the number of certificates and, per certificate, the SHA-256 of the canonical code, the role and, when `manifest_names` is true, the holder's name. It never contains emails.
- `eventkit verify CODE --manifest PATH` normalizes the input (case, hyphens, spaces, I and L to 1, O to 0), hashes it and looks it up. It prints VALID with event, role and name, or INVALID.
- Trust model: a valid result proves the code is in the manifest. The organizer must publish the manifest somewhere recipients trust. This limit is stated in the README.
- When signed credentials are enabled, eventkit derives a deterministic Ed25519 signing key from `EVENTKIT_SECRET` and writes a signed `credentials.json` package containing the public key, signed credentials and signed revocations. Verification checks the package signature and then checks the requested credential's signature, event, expiry and revocation status. Private email addresses never enter the package.
- SVG certificates are A4 landscape with XML-escaped text. The name font shrinks with length, and `unicode-bidi: plaintext` keeps right-to-left names in the correct order. A system font stack is used and no fonts are embedded.

### 6.7 Safety: dry run, audit and clean

- `--dry-run` prints every file with its action (create, update, unchanged or keep) and totals, and writes nothing.
- All writes go through one writer. It stays inside the output directory (resolved paths, no symlink escape), never overwrites a file it does not own, and rewrites a file it owns only if its hash still matches the audit log. Otherwise the file is kept and listed as Unresolved.
- `audit.jsonl` has one line per file (step, path, SHA-256, size) and an input digest. It has no timestamps, so it is deterministic.
- A second run on unchanged inputs gives byte-identical files, all reported unchanged.
- `clean` removes only audited files whose hash still matches, then empty directories it created, then the audit log. Edited files are kept and reported.

### 6.8 Report: Verified and Unresolved

`report.md` has three parts:

- **Summary:** rows read, ok, excluded, warnings, sessions, files written.
- **Verified:** only checks the run actually performed afterwards. Every SVG parses as XML. Every EML parses and has its attachment. The ICS unfolds and has its required properties. Every certificate code verifies against the manifest and all codes are unique. Counts in files match the summary.
- **Unresolved:** every error and warning that needs a human, schedule clashes, skipped steps with reasons, and kept files the user edited.

If a post-check fails, its claim moves from Verified to Unresolved. The report never states something the run did not check.

### 6.9 Human review and privacy profiles

- `input.review` may point to a deterministic JSONL ledger of `accept`,
  `reject`, `defer`, or `replace` decisions. Every decision requires a reason
  and reviewer. Replacement values are applied only to copied rows; source CSV
  data is never silently changed.
- `eventkit review EVENT_TOML` renders the current resolved/unresolved state.
  A deferred or missing decision remains unresolved. With a configured ledger,
  `run --strict` evaluates unresolved error findings rather than blindly
  failing on an error already accepted by a named reviewer.
- `[output].profile` is `private` by default. The `public` profile omits email
  and phone columns, redacts email-shaped values from reports, refuses private
  email drafts, and scans generated text before writing. Public output fails
  closed if privacy checking finds an email-shaped value.

## 7. Non-functional requirements

- **Determinism:** no clock, randomness or directory-order dependence anywhere in the output.
- **Offline:** no sockets and no subprocesses in the tool. Tests enforce this.
- **Performance:** limits in section 3, with the roster processed in a single pass.
- **Privacy:** logs and the audit file hold paths, hashes and row numbers, not personal data. The manifest rules are in 6.6.
- **Safety:** writes only inside the output directory; secrets only from the environment.
- **Portability:** Python 3.12, Linux first. ICS emits UTC, so the slim Docker image needs no timezone data.
- **Usability:** plain-English messages with row numbers, no color when piped, no tracebacks for expected errors.

## 8. Architecture

```
eventkit/
  src/eventkit/
    __init__.py  __main__.py  cli.py
    config.py       event.toml loading and validation
    model.py        Attendee, Session, Issue, FileOp, StepResult
    csvio.py        BOM-safe CSV reader with line numbers
    normalize.py    names, emails, phones
    validate.py     roster issues and de-duplication
    schedule.py     parsing, clash detection
    codes.py        HMAC codes, Crockford base32, manifest
    credentials.py  Ed25519 credentials, signed packages, revocations
    checkin_runtime.py  offline event-day records, merge, and app
    review.py       human-review ledger and privacy checks
    output.py       safe writer, audit log, clean
    steps/          roster  checkin  schedule  emails  certificates  report
    render/         svg.py  html.py  eml.py  ics.py (pure functions)
    samples/        data written by init (package data)
  tests/            conftest.py  test_*.py  fixtures/<case>/
  examples/sample-kit/   committed sample output
  pyproject.toml  lockfile  .python-version  Dockerfile  .dockerignore
  .env.example  .gitignore  README.md  LICENSE
```

**Interfaces:** each step is `run(ctx) -> StepResult`, where `ctx` holds config, the clean roster, sessions, the secret and the writer. Renderers are pure functions from data to text or bytes. The writer is the only code that touches the file system.

**Flow:** load config, read CSVs, normalize, validate, plan file operations, then either print the plan (dry run) or write, run post-checks, write the report, and update the audit log.

## 9. Testing strategy

Tests use pytest, the only dev dependency. Each test copies its fixture to a temporary directory. Fixtures and expected outputs are written before the code they test.

| Fixture | Purpose |
| --- | --- |
| clean-small | Everything passes; full expected kit |
| messy-roster | BOM, quoted commas, blank lines, extra columns, stray spaces, NFC and NFD names |
| duplicates | Exact, case-different and near-duplicate rows |
| bad-emails | Missing at sign, spaces, common domain typos |
| unicode-names | Urdu, Hindi and Kashmiri names, plus XML-special characters |
| schedule-clashes | Room overlap, touching sessions, double-booked speaker, bad times |
| no-attendance | Certificate step skipped with a reason |
| attendance-mismatch | Attendee missing from or excluded from the roster |
| column-mapping | Custom header names |
| modified-output | A user-edited file is kept and survives `clean` |
| missing-secret | Exit code 2 and nothing written |
| large-roster | 5,000 rows generated in the test for the performance limit |

**Test groups**

- **Normalization and validation:** each issue ID has a positive and a negative case.
- **Schedule and ICS:** clash rules, boundaries, UTC conversion, and a small reader in the tests that unfolds the ICS and checks required properties.
- **Codes:** deterministic, unique, round-trip through the manifest, one changed character fails, wrong event id fails, ambiguous characters normalize.
- **Manifest privacy:** no emails; names omitted when configured.
- **Renderers:** SVG is well-formed XML with escaped names; EML parses with its attachment and `X-Unsent`.
- **Output safety:** dry run writes nothing, reruns are byte-identical, edited files are never overwritten, `clean` removes only audited unmodified files, path traversal in config is refused.
- **CLI:** exit codes, `--strict`, `--dry-run` output.
- **Report:** Verified and Unresolved content, and a failing post-check moves its claim.
- **Offline:** an autouse fixture in `conftest.py` makes socket connections and `subprocess.Popen` raise, with a canary test proving it is active.

Done means the whole suite passes locally and in Docker with `--network none`.

## 10. Rebuildability and packaging

| Requirement | How this repo satisfies it |
| --- | --- |
| Git repository with commits | `git init` on day one, then a commit after each working step |
| Dependency lockfile | `uv.lock` preferred; fallback is a fully pinned `requirements-dev.txt`. Build backend pinned in `pyproject.toml` |
| Runtime pinned | `.python-version` (3.12), matching the Docker base image |
| Dockerfile that builds and runs | `Dockerfile` on `python:3.12-slim`, plus `.dockerignore` |
| One test command | `python -m pytest -q` |
| README | Setup, run, test, Docker, the trust-model note, and sample output |
| .env.example | Lists `EVENTKIT_SECRET` with a placeholder only |
| No machine-specific paths | All paths come from `event.toml`, relative to it |

Dockerfile outline (adjust to the lockfile actually created):

```
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN pip install --no-cache-dir -e .
CMD eventkit --help
```

Commands (replace with the ones OmniRush confirms by running them):

```
git clone <repo-url> && cd eventkit
docker build -t eventkit .
docker run --rm --network none eventkit python -m pytest -q
docker run --rm -e EVENTKIT_SECRET=demo-secret-change-me eventkit sh -c 'eventkit init demo && eventkit run demo/event.toml && cat demo/kit/report.md'
```

The image has no ENTRYPOINT, so the tool name follows the image name. Mount a volume at `/app/demo` to keep the generated kit.

## 11. Buildathon rules and judging map

| Judging criterion | Evidence in this project |
| --- | --- |
| It works | One command from messy input to a complete kit; sample data via `init`; demo video |
| It is worth using | A real recurring chore for organizers; every decision explained; verifiable certificates |
| It rebuilds | Standard library only; offline tests in Docker; lockfile; pinned runtime |
| OmniRush built it | All code written in OmniRush sessions, listed in the submission; sub-agents on separate modules; fresh repo started after 14 Oct |

Rules to respect: solo entry; OmniRush is the only generative AI for code, text and images; public GitHub repo on the connected account; README plus the sessions used; submit by 20 Oct, 11:59 PM IST. Best Use of OmniRush looks for clear sessions, smart sub-agent use, tests and a clean rebuild.

## 12. Plan and milestones

| When (IST) | Milestone | Done when |
| --- | --- | --- |
| Sat 17 Oct | Repo, config, CSV reader, normalization, validation; fixtures first | `run` on a fixture writes `roster.csv` and `issues.md` |
| Sun 18 Oct | Codes, certificates, manifest, `verify`, check-in sheet, schedule, ICS | Every fixture expectation passes |
| Mon 19 Oct | Emails, dry run, audit, `clean`, report, `init` samples, Docker, README. Joining closes 11:59 PM | Offline Docker test run passes |
| Tue 20 Oct | Fresh-clone rebuild, demo video, submit by afternoon. Deadline 11:59 PM | Submission ID pasted into Unstop |
| Sat 25 Oct | Results announced |  |

**Cut line:** if roster, certificates and verify are not working by Sunday night, drop emails and the schedule step.

## 13. Demo and submission

**Demo script (2.5 to 3 minutes).** Numbers shown will come from real runs.

1. 0:00 The problem: a messy form export and a pile of manual work.
2. 0:20 `eventkit init demo`, then show the deliberate mess in the CSVs.
3. 0:40 `eventkit run --dry-run`, then the real run.
4. 1:00 Open `issues.md`, the check-in sheet and the agenda with a room clash flagged.
5. 1:30 Open a certificate with a right-to-left name. Run `verify` to get VALID, then change one character to get INVALID.
6. 2:00 Open `report.md` and show Verified against Unresolved.
7. 2:15 Run `eventkit clean`, then the offline Docker tests.

**Submission checklist**

- Track set to Agents & Automation; title and description from Appendix A.
- Public repo pushed from the connected GitHub account, with README.
- OmniRush sessions added to the submission.
- Demo video uploaded as Unlisted on YouTube.
- Optional live link: host `examples/sample-kit` on GitHub Pages so judges can click through the output.
- Submission ID pasted into Unstop. Submit a first version early; it can be updated until the deadline.

## 14. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Scope creep | Misses the deadline | Fixed cut order in section 5; no stretch work |
| Right-to-left text renders differently across viewers | Certificates look wrong | `unicode-bidi: plaintext`, tests on well-formedness, manual check in two browsers |
| ICS quirks in calendar apps | Import problems | Follow RFC 5545 basics, UTC times, self-parse post-check |
| Misleading trust claims about certificates | Credibility | State the trust model in README and report |
| False duplicate flags | Annoys organizers | Near-duplicates are warnings only, never auto-excluded |
| Secret committed by accident | Forgeable certificates | Environment only, `.env` ignored, repo holds only a placeholder |
| Docker image differs from local | Judges cannot rebuild | Build and run offline tests in Docker daily from Sunday |
| Rule breach (other AI tool, reused code) | Flagged for review | Fresh repo, OmniRush only, nothing pasted from other tools |
| Token budget | Stalls midway | Plan first, one task per session, commit often |

## 15. Open decisions

- License: MIT is recommended; it needs the holder name.
- Manifest names: recommended default is to include them, because a code without a name proves little. Organizers can opt out.
- Lockfile tool: `uv.lock` or pinned `requirements-dev.txt`, depending on the OmniRush sandbox.
- Whether the tool should read a local `.env` file. Recommended: no, use the environment only.
- Tool name: eventkit is a working name and may need changing if the package name is taken.

## Appendix A. Submission copy

**Title:** eventkit: turn one registration CSV into a ready-to-run event kit

**Description:** eventkit runs a campus event's paperwork from one registration CSV: it cleans and de-duplicates attendees (explaining every flag), checks the schedule for clashes, and builds a check-in sheet, calendar file, email drafts and certificates anyone can verify offline. Standard library only, dry-run and undo, tested offline.

## Appendix B. OmniRush prompts

**Kickoff**

> I am building eventkit, a Python 3.12 CLI that turns a registration CSV, a schedule CSV and an optional attendance CSV into a kit folder. Requirements are in the PRD I paste next. Before any code, give me a 5-step plan and wait for my OK. Write fixtures and expected outputs first. Use sub-agents on separate directories: ingest and validate, certificates and verify, schedule and emails, tests, then a reviewer. Standard library only, no network or subprocess, deterministic output, never overwrite or silently drop rows. Test command is `python -m pytest -q`, and tests must pass in Docker with `--network none`.

**Rebuild check**

> Make this project rebuildable from scratch on a clean Linux machine. Check each item, fix what is missing, and commit: git repo with commits, dependency lockfile, pinned runtime, Dockerfile that builds and runs, one test command, README with setup, run and test steps, .env.example with no secrets, no absolute paths from this machine. Match .python-version to the Docker base image and install pytest at build time so tests run with --network none. Then print the exact build, test and run commands with the real output of each.

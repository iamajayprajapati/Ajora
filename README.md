# AJORA

AJORA is an academic opportunity finder for students in India. The Next.js app serves a student catalog and review desk. FastAPI owns authentication, the institute registry, source snapshots, review decisions, the catalog and alerts. A Celery worker can run the monitoring workflow on a schedule.

## Run the local pilot

Install [Node.js](https://nodejs.org/) 20.9 or newer, Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/). In two PowerShell terminals:

```powershell
# API terminal, from the project root
cd api
uv sync --locked
$env:AJORA_LOCAL_DEMO = "1"
$env:AJORA_ADMIN_TOKEN = uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:AJORA_ADMIN_TOKEN
uv run uvicorn ajora.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Web terminal, from the project root
cd web
npm.cmd ci
npm.cmd run dev -- --hostname 127.0.0.1
```

Open [AJORA](http://localhost:3000), [API docs](http://127.0.0.1:8000/docs) or the [review desk](http://localhost:3000/review). Use the generated administrator key in the review desk. Create a student account to try bookmarks, institute follows, alerts, and submissions. In demo mode, accounts verify locally without email and the catalog includes six clearly labeled fictional listings. Keep the demo loopback-only. The local database is created at `api/ajora.db`.

The [feedback forum](http://localhost:3000/feedback) is open to read. Signed-in members can start discussions, reply, and remove their own posts. Posts display a member label and omit account email addresses. Deleting an account removes the author's feedback while preserving replies from other members.

For a corresponding PostgreSQL and Redis setup on a machine running Docker, set `AJORA_ADMIN_TOKEN` and run `docker compose up --build`. Compose is a local demo configuration. View the rendered configuration with `docker compose config --quiet`.

## Onboard a source

The public directory includes 23 IITs, 31 NITs, and all 26 IIIT entries in the [JoSAA 2026 directory](https://josaa.admissions.nic.in/applicant/seatmatrix/instituteview.aspx), including IIIT Vadodara’s Diu campus. The IIIT section also includes five independently listed IIITs. The [UGC directory](https://www.ugc.gov.in/universitydetails/university?type=ddmCMsxJZgXH2S%2Fm0uMOKQ%3D%3D) supplies the 57 Central University entries. Within each section, AJORA prioritizes institutes by upcoming published events and then by events that ended in the past year.

Select **View profile** to read an introduction, visit the official website, and see upcoming events for an institute. Logo images are stored under `web/public/institute-logos` with their official source recorded in `api/ajora/institute_identity.json`. The directory displays a generic institute icon where a source image is unavailable.

An institute's **Events & flyers** section lists its published events with direct links to attached flyers and posters. Use **Past events & updates** for ended, cancelled, or postponed events. The profile is publicly readable and shows **Apply** when an event has a verified registration link and applications are still open.

Source collection still starts with the 20 pilot source URLs. The [IIT Bombay event calendar](https://www.iitb.ac.in/events) and [NIT Tiruchirappalli announcements](https://www.nitt.edu/home/) are examples. Verify each URL and its access terms before enabling collection. Seed sources and newly discovered URLs begin pending.

The footer visitor count is an aggregate. A one-year first-party cookie prevents counting the same browser on each page visit. The counter does not store individual visitor records.

Run the following from `api`, using the same database environment as the API:

```powershell
uv run python -m ajora.cli sources
uv run python -m ajora.cli source iit-bombay https://www.iitb.ac.in/events --permission-note "Record the actual terms reviewed and permission basis here" --interval 24
uv run python -m ajora.cli crawl
```

The permission note above is a placeholder. A source can also be approved from a discovery or submission item in the review desk. To pause an enabled source use the CLI with `disable <source-id>`. Use `crawl --force` for a bounded recheck during diagnosis. Each scheduled run processes up to 12 due sources. Collection supports HTML, XML feeds and sitemaps, and PDF text. The collector checks robots.txt and host boundaries, applies request limits and conditional requests, hashes snapshots, and backs off after failures.

The review desk shows source documents as text, proposed values and quotes, probable duplicates, and crawl outcomes. For an unstructured source, fill in the candidate and field evidence manually. Dates use ISO YYYY-MM-DD. Each populated claim needs a quote from the collected snapshot. For example `attendance.0.fee_inr` refers to the first attendance option's registration fee. Use API docs to view the full candidate schema and accepted enum values.

Publication requires an approved official source, valid claim values, matching evidence excerpts and a reviewer decision. The reviewer checks that each quote supports the proposed value in context. Updates, cancellations and postponements are reviewed against the last approved event. Missing documents and failed checks leave the last reviewed version visible with a stale notice.

Registration fees, deposits, and certificate charges each need their own explicit price or no-charge evidence. AJORA checks the amount against the excerpt or an INR offer in the source, and the reviewer confirms that each price applies to the stated attendance option and eligibility. Existing prices without this check are held from public display and price filters until reviewed. The free-entry badge requires a verified ₹0 registration fee and no recorded deposit. A free certificate is tracked separately.

Each published event links to its institute announcement. The collector looks for registration links in announcement hyperlinks, structured event offers, and PDF links or text. It also decodes QR codes in PDF pages and approved raster poster sources, plus inline and clearly marked same-host posters embedded in approved HTML. The review desk shows the extracted destination and where it was found. Choose **Use link** to put it in the event draft. For QR codes or externally hosted application forms, the reviewer must open the link and confirm that it leads to the correct event. The destination must match the captured source exactly. The crawler never follows registration destinations.

For a separately collected flyer, enter the ID of an already-published event at the same institute, choose the extracted URL, and use **Update application link only**. The original announcement stays linked on the event page. Without a verified direct link, students can open the official announcement to find application instructions. Apply buttons are hidden for closed, past, postponed, and cancelled events. QR decoding covers supported raster images and the first 20 pages of PDFs. An unreadable QR or a QR without a public HTTPS destination does not yield an Apply button.

Event pages show **Flyers & posters** with links to institute PDFs and images. Approved image captures can be previewed or opened at full size. If an image or PDF is the event's approved source, it is shown automatically. For an announcement with several links, select the files for this event when reviewing it. Use **Attach flyers and posters only** to add a separately collected file to a published event without changing its registration link or event details.

## Optional extraction and operations

For AI-assisted extraction, set `GEMINI_API_KEY` in the crawler process and set `AJORA_GEMINI_FREE_TIER=1` after confirming the key belongs to a Google AI Studio Free Tier project. AJORA calls Gemini 3.5 Flash-Lite for text extraction without search or other tools. To estimate quota and check availability, see [Google's model pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite).

For an offline fallback, run an OpenAI-compatible server on the same host using [Qwen3-4B-GGUF from Hugging Face](https://huggingface.co/Qwen/Qwen3-4B-GGUF). For example, with llama.cpp, run `llama-server -hf Qwen/Qwen3-4B-GGUF:Q4_K_M --host 127.0.0.1 --port 8080 --alias Qwen3-4B -c 8192`, then set `AJORA_LOCAL_MODEL_URL=http://127.0.0.1:8080/v1/chat/completions` in the crawler process. Set `AJORA_LOCAL_MODEL` if you use a different model alias. When both are configured, the local model handles a failed Gemini request. Without a usable model, unstructured sources enter manual review.

The Python collector reads structured event metadata, single-event HTML announcements with labeled dates, and text-based PDF brochures. It separates event dates from application deadlines and stores source excerpts with each proposal. Missing or conflicting dates stay unconfirmed. The optional agent helps with other layouts. The review desk requires a publication decision and confirmation of any QR or external registration destination.

The review queue prioritizes approaching deadlines, changes to dates, application links or fees, and cancellations or postponements. With an AI provider configured in the crawler process, schedule-change notices also get an agent pass. A flag must point to an exact excerpt in the source snapshot and a published event at that institute. Open the review desk to see the excerpt and the provider. AI flags do not change published events. The Monitoring tab shows actual provider use on individual runs.

Run `uv run python -m ajora.website_audit` from `api` to refresh the directory's HTTPS checks. The results record the official registry reference, homepage, check date and logo source. The directory counts published upcoming events from monitored sources for each institute.

Run `uv run python -m ajora.discover --output institute-section-audit.json --queue` from `api` to scan the directory for official News, Announcements and Events sections. Add `--institute <id>` to check selected institutes or `--inspect-sections` to read the linked sections and report candidate events and announcement links. The report includes fetch and crawler-policy errors. Queued links are pending until their collection terms are checked.

The scheduler checks which approved sources are due every 15 minutes. For a local run without Redis, use `uv run python -m ajora.cli crawl --watch` from `api` while the preview is open. For hosted monitoring use the Celery worker and scheduler with the shared database and Redis. Run one scheduler for the same database. Institute profiles distinguish checked, pending, partially checked, and unavailable sources. Followed institute announcements, changes to saved events and approaching deadlines produce opt-in in-app alerts.

Newly approved sources default to a 24-hour refresh interval. The watcher checks all due approved sources, and `uv run python -m ajora.cli crawl --full` runs the same full pass once. Source-specific intervals and error backoff still apply.

### Daily n8n refresh

1. Run AJORA with a Redis broker and Celery worker, and set a separate `AJORA_REFRESH_TOKEN` of at least 24 characters on the API. The Compose API reads this variable from the host or its environment file. Configure the optional AI provider in the worker's environment if you want model-assisted extraction.
2. Import [the daily refresh workflow](automation/ajora-daily-refresh.n8n.json) into n8n. In **Queue official source refresh**, change the URL to one n8n can reach. Use `http://api:8000/api/automation/refresh` if n8n shares the AJORA Compose network. Use your HTTPS AJORA origin for a remote n8n instance.
3. Create a **Header Auth** credential in n8n with the name `X-Ajora-Refresh` and the same secret value. Connect it to the HTTP Request node. Use **Test refresh** and confirm the endpoint returns HTTP 202 and a job ID. Check **Review Desk → Monitoring** for actual crawl results.
4. Publish the workflow to run at **5:00 a.m. India time** each day. The worker checks all due approved sources and sends new or changed claims to review. Applications and critical changes still need verification before publication.

For a hosted pilot, omit `AJORA_LOCAL_DEMO`, serve HTTPS, set `AJORA_PUBLIC_URL` to the public web origin, and put the API behind the frontend proxy. The Next.js build reads `AJORA_API_URL`. Configure `AJORA_SMTP_HOST`, `AJORA_SMTP_PORT` (defaults to 587), `AJORA_SMTP_FROM`, and `AJORA_SMTP_USER` / `AJORA_SMTP_PASSWORD` when required. Pilot accounts are restricted to self-attested adults. The public catalog shows a small preview while verified accounts can use the full finder. Store the admin key outside source control.

## Verification and release work

### Public deployment from GitHub

Keep the Ajora source in a GitHub repository and clone it on a Docker host with a public HTTPS hostname. GitHub Pages serves static files. Ajora's public deployment also runs Next.js, FastAPI, PostgreSQL, Redis and the collection worker.

Point your hostname's DNS to the host and make ports 80 and 443 reachable. Create a private `.env.production` beside `compose.yaml` with `AJORA_DOMAIN` (the hostname without a scheme), `AJORA_DB_PASSWORD` (a URL-safe random secret), `AJORA_ADMIN_TOKEN` (at least 24 characters), `AJORA_SMTP_HOST` and `AJORA_SMTP_FROM`. Set `AJORA_SMTP_USER` and `AJORA_SMTP_PASSWORD` if the SMTP server needs authentication. Copy these values to the server through its secret configuration.

```sh
docker compose --env-file .env.production -p ajora-public -f compose.yaml -f compose.production.yaml config --quiet
docker compose --env-file .env.production -p ajora-public -f compose.yaml -f compose.production.yaml up -d --build
```

Caddy obtains and renews the HTTPS certificate. Check `/api/health` and open the public event finder after startup. The public stack uses a fresh database. Transfer only reviewed event data and source approvals through a controlled migration when moving listings from a local pilot. Back up the production database separately.

Run `uv run pytest -q` from `api` and `npm.cmd run build` from `web`. Regression tests exercise catalog filters, account flows, the review gate, evidence rejection, source failures, cancellation, and crawler boundaries.

The repository is a local pilot. A public launch still needs source-by-source verification and permissions, a labeled research dataset with measured precision and latency, an approved onboarding process for minors, organizer identity verification, and production infrastructure. Current gaps include selective OCR and JavaScript rendering, production backups and schema migrations, managed identity and API rate limits, external alert delivery, and a cross-institute deduplication review process. The current snapshot store is the database and catalog filtering runs against the pilot dataset. Review storage and search performance before expanding coverage.

## Homepage artwork

`web/public/campus.webp` was made with Codex's built-in ImageGen tool and compressed to WebP for the website. Generation prompt:

> Landscape 3:2 editorial art for AJORA, an India academic opportunity search website. Photorealistic documentary photo of three Indian university students (young woman in lilac overshirt holding a notebook at center, friend in muted indigo and young man in light yellow) working together around an open laptop outdoors on campus, relaxed, engaged. Compose all people in central lower two thirds. Sophisticated contemporary print collage: crisp cutout edges on a soft lavender background, dark purple halftone fine dots at edges, one soft butter-yellow circle behind students, light sweeping orbital line. Strong purple color grading with natural skin tones, warm feeling. Render as a clean edge-to-edge rectangle. No lettering, no logos, no badges, no watermark.

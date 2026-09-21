# Local container deployment

Windows runtime note (20 September 2026): this machine's Application Control policy
blocks `torchvision/image_stable.pyd` with WinError 4551 / status 0xC0E90002.
The installed wheel checksum matches, and the failure also occurs outside the
tool sandbox. Do not disable Windows protection or rename/patch the binary.
Use this Linux Docker deployment on port 8001; native Windows execution remains
affected until the library is approved through the machine's normal policy process.
Docker has separate state and a separate GUIVIN account; Camera Grid credentials
are entered only in Connect Sentinel after logging into GUIVIN.

Use Docker Compose for the current single-host application. The application has
process-local sessions, stream workers, deduplication and ledger locking; run
exactly one API worker/replica. Kubernetes is a later deployment stage after
shared identity, coordinated workers, durable messaging and database/storage
concurrency have been implemented and tested. An HPA around this application
would duplicate capture and break these assumptions.

1. Run `python tools/setup_models.py --download` in the configured project venv
   if the model files are not present. Downloads are explicit setup actions;
   application startup does not download model weights.
2. Run `python tools/bootstrap_container.py` to create a local account. It stores
   its generated password in ignored `tmp/docker/admin-password.txt`; do not
   commit/share that file. Use `tools/manage_users.py --file tmp/docker/users.json`
   to manage accounts subsequently.
3. Run `docker compose build`, then `docker compose up -d`.
4. Open `http://localhost:8001` and sign in as `local-admin` using that file.
   The existing native application on port 8000 remains separate.
5. Inspect `docker compose logs --tail 100 api` and the authenticated health API.
   Container health checks only API responsiveness, not model accuracy or feed readiness.
6. Stop with `docker compose stop`. The named state volume is retained. Do not
   use `down -v` unless intentionally deleting container database/evidence.

The image runs non-root, drops capabilities, uses a read-only root filesystem,
mounts model files read-only, and requires a nonempty account secret. Application
state is in `/var/lib/guivin`, with stable evidence paths. The container uses a
separate new database; native Windows absolute evidence paths are not migrated.
Authentication secrets are mounted from the local file, not baked into images.
The Compose port binds only to loopback. HTTPS, MFA, PKI and encrypted storage
are still required before a shared/production deployment.

The default image uses CPU PyTorch. GPU inference needs a separately validated
CUDA image/host configuration; do not infer multi-camera capacity from this file.

OCR now defaults to `GUIVIN_OCR_PREPROCESSING=preserve`: aspect-preserving
grayscale crops and nearby multi-line text assembly, without guessed character
substitutions. `legacy` is the supported rollback setting; set the environment
variable before `docker compose up -d`. Experimental direct/beam modes are only
available in the offline comparison tool. The 25-region development sample
improved from 2 to 7 exact matches; this remains inadequate field accuracy.
The dedicated plate detector is still unconfigured.

To compare preprocessing candidates on annotated plate regions, use the same
read-only mounts below with `/app/tools/compare_ocr_modes.py --manifest ...
--images ...`. For the selected decoder comparison add
`--modes preserve preserve_beam`. The original `compare_ocr.py` explicitly
retains legacy preprocessing for its resize-factor comparison.

For offline OCR comparison, put permitted images and a UTF-8 `labels.csv` in
`tmp/ocr-evaluation`. Required columns are `image,x,y,w,h,ground_truth_plate`:
image paths are relative to that directory, boxes describe the whole vehicle,
and labels must be independently verified. Do not substitute plate boxes.
Preserve the dataset's license and official split; keep repeated vehicles out
of both tuning and final test sets. From the project root, run:

```powershell
docker compose run --rm --no-deps -T --entrypoint python -v "./tools:/app/tools:ro" -v "./backend/app:/app/backend/app:ro" -v "./tmp/ocr-evaluation:/evaluation:ro" api /app/tools/compare_ocr.py --manifest /evaluation/labels.csv --images /evaluation
```

This separate process reads images without starting camera workers or changing
live OCR settings. It compares resize factors 2.5, 1.5 and 1.0 and prints aggregate
JSON metrics with input hashes. Timing covers plate localization/rectification/OCR,
not capture-to-alert latency. Do not run concurrently with a performance benchmark
of the live app. Positive examples alone cannot measure false-positive rates.

For publisher-supplied PLATE boxes, explicitly add `--box-kind plate`; this
bypasses vehicle localization and tests rectification/OCR only. Do not compare
that result to end-to-end vehicle-box accuracy. The pinned DataCluster sample
can be fetched with `python tools/fetch_ocr_sample.py` for local noncommercial
evaluation; read its retained README/provenance and do not redistribute images.
Its inputs live in `/evaluation/datacluster` in the container, with manifest
`/evaluation/datacluster/labels.csv`. Text labels exist for only part of the
published sample. The first comparison is exploratory; it is not an independent
hold-out set after using it to select settings.

Only `.dockerignore`-allowlisted application/data/frontend files enter the build
context; local evidence, models, sessions and credentials are excluded.

Set `GUIVIN_STREAM_HOSTS` to the exact approved source hosts before consuming
external streams. Do not use arbitrary gateway control/publish APIs. Follow the
[official integration guide](https://sentinel.gujarat.gov.in/resource): use the
camera catalogue, RTSP/TCP for analysis, source PTS for time intervals, bounded
connections and reconnect backoff. The actual authenticated catalogue/schema
must be verified before enabling cameras; URL examples are not fixed contracts.

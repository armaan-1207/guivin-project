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

### Complete pretrained ANPR engine

FastALPR is an opt-in CPU backend combining the YOLOv9-t-384 plate detector
with the CCT-XS-v2 global plate recognizer. GUIVIN runs both stages on selected
vehicle crops and retains tracking, OCR budgets, plate-format checks and temporal
consensus. Ambiguous crops containing different valid plates return no read.
An unavailable configured engine reports failure instead of silently switching
models. No remote inference service receives camera footage.

Build the base image first, then install the optional engine and fetch its models:

```powershell
docker compose build
docker build -f deploy/Dockerfile.fast-alpr -t guivin:fast-alpr .
docker run --rm --entrypoint python -v "./tools:/app/tools:ro" -v "./tmp/fast-alpr:/models/fast-alpr" guivin:fast-alpr /app/tools/setup_fast_alpr.py --directory /models/fast-alpr
docker compose -f compose.yaml -f compose.fast-alpr.yaml up -d
```

Setup records model hashes and package versions under ignored `tmp/fast-alpr`.
Runtime loads hash-verified local files only. Hashes pin locally installed bytes;
they are not an independent publisher signature. Model errors are visible in
authenticated health responses through `anpr_backend`, `ocr`, `plate_detector`
and `errors`. To roll back, run `docker compose up -d --build` using only the
base Compose file. Restarting expires local sessions.

The integration is functional, not a verified accuracy improvement: an initial
20-image reused development check localized 16/25 labeled plates and read 7/25
exactly (format-gated, normalized separators). Full-frame median inference was
32.4 ms and p95 45.6 ms on the local CPU run; this excludes capture, vehicle
detection, tracking and evidence work and is not camera capacity. Independent
Indian footage and plate-free negatives remain necessary for acceptance.
The selected OCR model supports at most 10 characters; longer registration
formats require a different recognizer and must not be claimed as supported.
FastALPR's source license is MIT; retain upstream notices and check each model's
release terms and training provenance before distribution.
Sources: [FastALPR](https://github.com/ankandrew/fast-alpr),
[detector models](https://github.com/ankandrew/open-image-models),
[plate recognizer](https://github.com/ankandrew/fast-plate-ocr).

### Optional PaddleOCR development comparison

The separate evaluation image compares the current EasyOCR path with PaddleOCR
text detection/recognition on the same annotated plate crops. It does not change
the running API, load camera credentials or write observations into its database.
PaddleOCR is a general OCR candidate here, not an Indian-plate fine-tuned model.

After building `guivin:local`, create `tmp/paddle-evaluation` and run:

```powershell
docker build -f deploy/Dockerfile.ocr-evaluation -t guivin:ocr-evaluation .
docker run --rm -v "./tools:/app/tools:ro" -v "./backend/app:/app/backend/app:ro" -v "./tmp/paddle-evaluation:/cache" guivin:ocr-evaluation --download-only
docker run --rm --network none -v "./tools:/app/tools:ro" -v "./backend/app:/app/backend/app:ro" -v "./tmp/paddle-evaluation:/cache" -v "./tmp/easyocr:/models/easyocr:ro" -v "./tmp/ocr-evaluation:/evaluation:ro" guivin:ocr-evaluation --manifest /evaluation/datacluster/labels.csv --images /evaluation/datacluster --output /cache/comparison.json
```

Only explicit setup downloads model assets. Evaluation requires local models and
runs without network access. Choose a new output filename for each run; existing
reports are not overwritten. Reports include input/model hashes, package versions,
per-sample reads, exact/character scores and warm latency percentiles. Keep these
reports and source images local. The reused publisher-labeled sample is development
data, not held-out accuracy; plate boxes bypass vehicle/plate localization and do
not measure false positives on plate-free scenes. Runtime promotion requires
separate validation. The image has its own dependency versions and is not a serving
image; dependency checks run during build.

For the bounded-input experiment, repeat the offline run with `--bounded` and a
different output path. It preserves crop aspect ratio and color within 480x256,
adds a 12-pixel border, and uses a maximum detector side of 960. Compare both
recognition and tail latency before selecting a setting; smaller inputs can
lose characters. Neither candidate is wired into the serving API.

Validation manifests can additionally contain `plate_present` (`true`/`false`).
A negative region must explicitly set `plate_present=false` and leave
`ground_truth_plate` empty; an unlabeled image is not a negative. Positives still
require nonempty text. Negative-region false-read rates are reported separately
from positive exact-match accuracy; without negatives the rate is unavailable.

To check a new dataset against development data, add
`--development-manifest /evaluation/datacluster/labels.csv
--development-images /evaluation/datacluster` to the comparison command. Repeat
both options in matching order for every development dataset. The check runs
before model loading and rejects exact image-byte reuse (including renamed files)
or repeated normalized plate labels. Declare every dataset used to select models
or settings. This guard cannot prove label quality or detect recompressed/near-
duplicate images; keep publisher splits, audit labels and vehicle/sequence groups,
and do not describe previously tuned data as independent validation.

References: [official PaddleOCR API](https://paddlepaddle.github.io/PaddleOCR/v3.0.0/en/version3.x/pipeline_usage/OCR.html)
and [PaddleOCR 3.0 technical report](https://arxiv.org/abs/2507.05595).

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

Before using an authorized dataset, validate its split and provenance manifest:

```powershell
python tools/validate_dataset_manifest.py --manifest tmp/labels.csv --images tmp/images --require-camera-disjoint
```

The manifest requires `image`, `split`, `camera_id`, `capture_session`,
`vehicle_id` and `plate_present`; positive rows also require `plate_text`,
while negative rows must explicitly set `plate_present=false` with empty text.
The checker hashes image bytes and rejects image, vehicle or normalized-plate
identities crossing splits. It can also enforce camera-disjoint splits. This is
a data-quality gate, not a claim that labels or permissions are valid.

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

# Technical Briefing — AI Traffic Bottleneck Detection System
**IBL Capstone Project MA02 2026**
*Last updated: 2026-04-27*

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Project Folder Structure](#2-project-folder-structure)
3. [Detection Pipeline — How the AI Works](#3-detection-pipeline--how-the-ai-works)
4. [Camera Discovery — trafficwatch.py](#4-camera-discovery--trafficwatchpy)
5. [Incident Detection — incident_detector.py](#5-incident-detection--incident_detectorpy)
6. [Claude AI Integration — claude_api.py](#6-claude-ai-integration--claude_apipy)
7. [FastAPI Backend — main.py](#7-fastapi-backend--mainpy)
8. [Database — database.py, schema.sql](#8-database--databasepy-schemasql)
9. [Frontend — map.html](#9-frontend--maphtml)
10. [Analytics — analytics.html](#10-analytics--analyticshtml)
11. [Authentication — login.html, signup.html](#11-authentication--loginhtml-signuphtml)
12. [Configuration and Environment](#12-configuration-and-environment)
13. [Error Handling and Resilience](#13-error-handling-and-resilience)
14. [Known Limitations and Future Improvements](#14-known-limitations-and-future-improvements)

---

## 1. System Overview

### What the System Does

This is a real-time AI-powered traffic monitoring system for Port Louis, Mauritius. It connects to four live street cameras operated by MYT (Mauritius Telecom) via HTTP Live Streaming (HLS), pulls one video frame every two seconds from each camera, runs a YOLOv8 object detection model to count vehicles in each frame, classifies the traffic density as free/moderate/heavy/bottleneck, and displays everything on an interactive map dashboard in a web browser.

When congestion is detected, the system calls the Claude AI API (Anthropic) to generate a plain-English traffic summary that appears in the dashboard's alert panel. An incident detection engine runs in parallel, looking for patterns such as sudden traffic spikes, sustained bottlenecks, and rapid buildups, and raises incidents that are shown in the sidebar and dispatched as alerts.

### Complete Data Flow — Step by Step

```
1. App starts → lifespan() runs discover_cameras()
   └── ffprobe validates each MYT HLS stream URL
   └── Returns list of 4 camera dicts with validated .m3u8 URLs

2. For each camera → asyncio.create_task(_hls_camera_loop(camera_id, source, url_candidates))
   └── One background coroutine per camera runs indefinitely

3. _hls_camera_loop → run_hls_pipeline(camera_id, hls_urls)  [in a thread via asyncio.to_thread]
   └── Every 2 seconds: _grab_single_frame(hls_urls, width, height)
       └── Spawns fresh ffmpeg subprocess: reads ONE frame from the HLS stream
       └── Returns raw BGR24 numpy array

4. Preprocessing (inside _detect_loop):
   └── If mean brightness < 80: apply brightness boost (alpha=1.3, beta=20)
   └── CLAHE contrast enhancement on LAB L-channel

5. YOLO inference:
   └── model.predict(frame, conf=0.25, iou=0.35, imgsz=1280)
   └── Filter results to VEHICLE_CLASSES = {1,2,3,5,7}
   └── Count raw vehicles → apply rolling median (window=3)

6. Severity classification:
   └── 0–4 vehicles  → free       (#23c55e green)
   └── 5–14 vehicles → moderate   (#f0883e orange)
   └── 15–29 vehicles → heavy     (#e94560 red)
   └── 30+ vehicles  → bottleneck (#8b31c7 purple)

7. Incident signals:
   └── Large bounding box check (>25% of frame area)
   └── Stationarity check (>70% of vehicles haven't moved 10px)
   └── Sets possible_incident=True in result dict

8. Result dict yielded → asyncio.to_thread(next, gen) returns it
   └── latest_detections[camera_id] = result  (in-memory store)

9. _process_detection(camera_id, result) called:
   └── asyncio.create_task(_save_snapshot())     → DB write (non-blocking)
   └── asyncio.create_task(_save_bottleneck_event()) if heavy/bottleneck
   └── incident_detector.analyze() → raises new incidents → appended to alert_log
   └── If cooldown elapsed OR severity changed → generate Claude AI summary
   └── If severity transitioned to heavy/bottleneck → generate Claude alert

10. WebSocket /ws/detections:
    └── Every 1 second: send json.dumps(latest_detections) to all connected browsers

11. Browser receives WebSocket message → applyTrafficData(data):
    └── Updates Leaflet map marker colours
    └── Updates sidebar camera list
    └── Updates heatmap layer

12. Browser polls /api/incidents/active every 10s → updates incident sidebar
13. Browser polls /api/alerts?limit=5 every 10s → updates bell dropdown
14. Browser polls /api/status every 10s → updates stat cards
```

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        MYT Wowza Server                              │
│   CAUDAN_NORTH.stream_720p  │  CAUDAN_SOUTH.stream_720p              │
│   LA_CHAUSSEE.stream_720p   │  CASERNES_BRABANT.stream_720p          │
│            (HLS .m3u8 streams over HTTPS)                            │
└────────────────────┬─────────────────────────────────────────────────┘
                     │ ffmpeg -vframes 1 (fresh subprocess per frame)
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     detection/hls_pipeline.py                        │
│  _grab_single_frame() → brightness boost → CLAHE → numpy array      │
│  model.predict() [YOLOv8m @ IMGSZ=1280, conf=0.25, iou=0.35]        │
│  Count vehicles → rolling median → severity → possible_incident      │
│                   Yields result dict every 2s                        │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│                          main.py (FastAPI)                           │
│                                                                      │
│  latest_detections: dict[camera_id → result]  (in-memory)           │
│                                                                      │
│  _process_detection()                                                │
│    ├── _save_snapshot()        → PostgreSQL TrafficSnapshots         │
│    ├── _save_bottleneck_event()→ PostgreSQL BottleneckEvents         │
│    ├── incident_detector.analyze() → alert_log                       │
│    └── summary_service.generate_summary() → Claude API              │
│                                                                      │
│  API Routes:                                                         │
│    GET  /api/traffic           GET  /api/cameras                     │
│    GET  /api/traffic/{id}      GET  /api/incidents/active            │
│    GET  /api/status            POST /api/alerts/trigger              │
│    GET  /api/summary/{id}      POST /api/demo/escalate               │
│    GET  /api/summaries         WS   /ws/detections                   │
│    GET  /api/alerts            GET  /analytics                       │
│    GET  /map                   GET/POST /login                       │
└──────┬──────────────────────────────────┬────────────────────────────┘
       │                                  │
       │ Claude API (Anthropic)           │ PostgreSQL
       ▼                                  ▼
┌──────────────┐                ┌─────────────────────────┐
│ claude-sonnet│                │ Database: TrafficSystem  │
│ -4-20250514  │                │  users                  │
│ max 200 tokens│               │  cameras                │
│ 3 retries    │                │  TrafficSnapshots       │
│ exp. backoff │                │  BottleneckEvents       │
└──────────────┘                │  alerts                 │
                                │  AuditLog               │
                                └─────────────────────────┘
       │ WebSocket + HTTP REST
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       Browser (map.html)                             │
│                                                                      │
│  Leaflet.js map  │  Sidebar camera list  │  Alert bell dropdown      │
│  Heatmap overlay │  Incident list        │  Demo button              │
│  Camera markers  │  Stats cards         │  Route suggestion panel   │
│  Pulse animation │  Analytics link       │  Satellite/street toggle │
│                                                                      │
│  WebSocket client (reconnects on drop)                               │
│  HTTP polling fallback for incidents/alerts/status (every 10s)       │
└──────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.12 | Primary backend language |
| FastAPI | 0.115.0 | Async web framework, REST API, WebSocket server |
| Uvicorn | 0.30.6 | ASGI server that runs FastAPI |
| YOLOv8m (Ultralytics) | latest | Vehicle object detection on video frames |
| OpenCV (headless) | latest | Image preprocessing (CLAHE, brightness, colour space) |
| FFmpeg | system binary | HLS stream frame extraction via subprocess |
| NumPy | latest | Raw video buffer handling |
| Anthropic SDK | latest | Claude AI API for traffic summaries |
| SQLAlchemy (async) | — | ORM for PostgreSQL queries |
| asyncpg | — | Async PostgreSQL driver |
| PostgreSQL | — | Persistent storage for snapshots, events, users |
| Jinja2 | 3.1.4 | Server-side HTML templating |
| Leaflet.js | 1.9.4 | Interactive map rendering in browser |
| Chart.js | 4.4.0 | Analytics charts |
| python-dotenv | latest | Load .env secrets at startup |
| requests / BeautifulSoup4 | latest | MYT page scraping for live stream URLs |
| itsdangerous | 2.2.0 | Session cookie signing (via Starlette) |

---

## 2. Project Folder Structure

```
IBL_Capstone_MA02_2026/
│
├── main.py                    ★ CRITICAL  FastAPI application, all routes, background tasks
├── database.py                ★ CRITICAL  PostgreSQL engine setup with graceful degradation
├── schema.sql                 ★ CRITICAL  SQL DDL for all database tables
├── requirements.txt           ★ CRITICAL  All Python package dependencies
├── .env                       ★ CRITICAL  ANTHROPIC_API_KEY and other secrets (never commit)
├── .gitignore                            Excludes .env, *.pt, __pycache__ from git
│
├── detection/                            Package: all AI detection code
│   ├── __init__.py                       Empty package marker
│   ├── hls_pipeline.py        ★ CRITICAL  HLS stream processing — reconnect-per-frame strategy
│   ├── pipeline.py                       RTSP/local video pipeline (OpenCV VideoCapture)
│   ├── mock_pipeline.py                  Simulated traffic generator for testing/fallback
│   ├── trafficwatch.py        ★ CRITICAL  MYT camera catalogue and URL discovery
│   ├── incident_detector.py   ★ CRITICAL  Rolling-window traffic incident detection engine
│   └── claude_api.py          ★ CRITICAL  Claude AI summary generation with retry/fallback
│
├── templates/                            Jinja2 HTML templates served by FastAPI
│   ├── map.html               ★ CRITICAL  Main live map dashboard
│   ├── analytics.html                    Chart.js analytics page
│   ├── login.html             ★ CRITICAL  Login form
│   └── signup.html                       Signup form (demo only, no DB write)
│
├── run_schema.py              ★ SETUP     Run once to create all DB tables
├── seed_users.py              ★ SETUP     Run once to insert admin/user demo accounts
├── check_db.py                            Quick DB inspection script
├── test_streams.py            ★ CRITICAL  MYT stream connectivity and frame-grab tester
│
├── start.bat                             Windows one-click startup script
├── start.sh                              Linux/Mac one-click startup script
│
├── yolov8m.pt                            YOLOv8 medium model weights (59 MB, auto-downloaded)
├── yolov8n.pt                            YOLOv8 nano model weights (6 MB, used by pipeline.py)
│
└── tests/
    ├── test_claude_api.py                Integration tests for Claude API module
    ├── trafficwatch_test.py              Tests for camera discovery
    └── test_dashboard.html              Static HTML test page for dashboard UI
```

**Critical vs Optional:**
- **Critical** (system does not function without them): `main.py`, `database.py`, `detection/hls_pipeline.py`, `detection/trafficwatch.py`, `detection/incident_detector.py`, `detection/claude_api.py`, `templates/map.html`, `templates/login.html`, `requirements.txt`, `.env`
- **Setup scripts** (run once, then optional): `run_schema.py`, `seed_users.py`, `schema.sql`
- **Optional / Development**: `test_streams.py`, `check_db.py`, `tests/`, `detection/pipeline.py`, `start.bat`, `start.sh`

---

## 3. Detection Pipeline — How the AI Works

### How `pipeline.py` Works (RTSP / Local Video)

`pipeline.py` is the original detection pipeline designed for RTSP camera streams or local video files. It uses OpenCV's `VideoCapture` to open a continuous video stream, reads frames at up to 15 fps, and processes every second frame (FRAME_SKIP=2) to reduce CPU load.

It uses `yolov8n.pt` (the nano model — fastest, lowest accuracy) and does not apply any image preprocessing. Vehicle classes are limited to `{2, 3, 5, 7}` (car, motorcycle, bus, truck). When the stream drops, it retries up to 3 times with a 5-second delay. This pipeline is **not currently used** for the MYT cameras, which require HLS rather than RTSP, but it remains available for local video file testing or future RTSP-capable cameras.

### How `hls_pipeline.py` Works in Detail

#### The Reconnect-Per-Frame Strategy

Wowza streaming servers (which MYT uses) terminate persistent TCP connections after a short time — typically 30–60 seconds. If you open an ffmpeg pipe and leave it running, Wowza silently drops the connection and ffmpeg stalls waiting for data that never arrives. The entire detection loop freezes.

The solution implemented here is **reconnect-per-frame**: instead of keeping one ffmpeg process alive, a brand-new ffmpeg subprocess is spawned for every single frame. The process opens the HLS playlist, downloads just enough of the stream to decode one video frame, writes it to stdout as raw bytes, and exits. The detection loop then sleeps for two seconds and repeats. Wowza never sees a connection held long enough to drop it.

This approach has a startup cost per frame (roughly 1–2 seconds for ffmpeg to connect and find a keyframe), which is why the frame interval is 2 seconds rather than continuous. The trade-off is accepted because reliability is more important than frame rate for traffic monitoring — we need accurate counts every few seconds, not video playback.

#### What `_grab_single_frame()` Does

```python
def _grab_single_frame(hls_urls: list[str], width: int, height: int) -> np.ndarray | None:
```

This function accepts a list of URLs (to allow fallback to an alternative URL if the primary fails), tries each one in order, and returns the first successfully decoded frame as a NumPy array, or `None` if all URLs fail.

For each URL it runs:
```
ffmpeg -y -loglevel error
       -reconnect 1 -reconnect_streamed 1
       -timeout 30000000 -rw_timeout 30000000
       -i <url>
       -vframes 1
       -f rawvideo -pix_fmt bgr24
       pipe:1
```

The key flags:
- `-vframes 1`: extract exactly one frame then exit
- `-f rawvideo -pix_fmt bgr24`: output raw bytes in BGR order (what OpenCV/NumPy expects) with no container overhead
- `pipe:1`: write to stdout so Python can capture it with `result.stdout`
- `-reconnect 1 -reconnect_streamed 1`: allow ffmpeg to retry the HLS segment download internally
- `-timeout 30000000 -rw_timeout 30000000`: 30-second timeout in microseconds to prevent indefinite hangs

The raw bytes received are exactly `width × height × 3` bytes. NumPy reshapes them into a `(height, width, 3)` array in BGR24 format.

**La Chaussee multi-URL fallback**: The La Chaussee camera has historically been less reliable than the others. Its entry in `trafficwatch.py` includes a `url_candidates` list with three URLs tried in order: `chunklist.m3u8` → `playlist.m3u8` → the hardcoded rotating chunklist URL. If the primary URL fails on a given frame grab, the next URL is tried before incrementing the failure counter.

#### How CLAHE Preprocessing Works

After a successful frame grab but before YOLO inference, two preprocessing steps run:

**Step 1 — Brightness boost (dark frames only):**
```python
mean_brightness = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()
if mean_brightness < 80:
    frame = cv2.convertScaleAbs(frame, alpha=1.3, beta=20)
```
This converts the frame to grayscale to measure mean pixel brightness. If the average is below 80 (out of 255), the frame is considered dark/night-time. `convertScaleAbs` then multiplies every pixel by 1.3 (30% brighter) and adds 20 to the baseline. This lifts partially lit vehicles — which YOLO often misses entirely in their dark state — into a detectable brightness range. The check prevents this from running on daytime footage, which would oversaturate it.

**Step 2 — CLAHE (Contrast Limited Adaptive Histogram Equalization):**
```python
lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
l, a, b = cv2.split(lab)
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
l = clahe.apply(l)
frame = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
```
CLAHE is a standard computer vision technique for improving local contrast. The frame is converted from BGR to LAB colour space, which separates luminance (L) from colour (A, B). CLAHE is applied only to the L channel, which avoids colour distortion. The algorithm divides the image into an 8×8 grid of tiles and equalises the histogram within each tile, with a clip limit of 3.0 to prevent amplifying noise. The result is a frame where dark areas are brighter relative to their surroundings, making vehicle edges visible even in poor lighting.

#### What `SMOOTH_WINDOW` Does

Raw vehicle counts fluctuate between frames due to detection noise — a vehicle partially occluded by a lamppost might be detected in one frame and missed in the next. `SMOOTH_WINDOW = 3` means the last 3 raw counts are stored in a `deque`, and the reported vehicle count is their median.

With a 2-second frame interval, a window of 3 covers 6 seconds. This is responsive enough to catch a real traffic change within 6 seconds while filtering out single-frame outliers. The previous value was 5 (10 seconds), which caused the count to lag significantly during rapidly changing conditions.

### How `mock_pipeline.py` Works

The mock pipeline is a software simulator that produces realistic-looking detection data without requiring any camera connection. It exists for two reasons: (1) development and testing without live streams, (2) as a temporary fallback if the HLS streams fail after exhausting all retries.

It simulates a full traffic cycle: `free → moderate → heavy → bottleneck → heavy → moderate → free`. Each stage has a randomly chosen target count within its range and a random dwell time between 8 and 20 seconds. Between stages, the count steps by 1–2 vehicles per tick, creating smooth transitions rather than instant jumps.

The output dict schema is identical to `hls_pipeline.py` output, so `main.py` can accept either without modification.

**Tick rate**: `TICK_INTERVAL = 1/7.5 seconds` — simulates ~7.5 processed frames per second. This is faster than the real HLS pipeline (0.5 fps) to make the mock feel responsive during demos.

### How YOLOv8 Works in Simple Terms

YOLOv8 (You Only Look Once, version 8) is a real-time object detection neural network. Given an image, it outputs a list of bounding boxes, each with a class ID (what kind of object) and a confidence score (how certain it is). It does this in a single forward pass through the network, making it fast.

**Key parameters used:**

- **`CONF_THRESHOLD = 0.25`**: A detection is only accepted if the model is at least 25% confident it's the right class. Lowered from the default 0.45 because night footage produces genuinely valid detections with lower confidence scores. Going too low (below 0.2) would start including false positives.

- **`IOU_THRESHOLD = 0.35`**: When multiple bounding boxes overlap for the same object, Non-Maximum Suppression (NMS) removes duplicates. IOU (Intersection over Union) measures how much two boxes overlap. With IOU=0.35, if two boxes overlap by more than 35%, only the highest-confidence one is kept. Lowered from 0.45 because queued vehicles in Port Louis traffic are physically close together and their bounding boxes naturally overlap; the stricter original value was merging adjacent cars into a single detection.

- **`IMGSZ = 1280`**: The input image is resized to 1280×1280 before inference. The MYT stream native resolution is approximately 1024×576. Running at 1280 means the image is slightly upscaled, giving the model more pixels to work with for small and partially visible vehicles. The trade-off is slower inference — roughly 2–4 seconds per frame on CPU.

- **`VEHICLE_CLASSES = {1, 2, 3, 5, 7}`**: Only detections with these COCO class IDs are counted:
  - 1: bicycle
  - 2: car (the primary class — most missed at night)
  - 3: motorcycle
  - 5: bus
  - 7: truck

- **`MODEL_PATH = "yolov8m.pt"`**: The medium-sized YOLOv8 model. Larger than the nano (n) model used by `pipeline.py`, it is significantly more accurate, particularly for small and overlapping objects, at the cost of slower inference.

### How Severity Classification Works

After smoothing, the vehicle count is passed to `_classify()`:

```
vehicle_count >= 30  →  bottleneck  (purple  #8b31c7)
vehicle_count >= 15  →  heavy       (red     #e94560)
vehicle_count >= 5   →  moderate    (orange  #f0883e)
vehicle_count >= 0   →  free        (green   #23c55e)
```

These thresholds are calibrated for Port Louis road geometry — the monitored cameras cover 2–4 lane urban roads where 30+ vehicles in frame represents a genuine standstill. The thresholds are consistent across both the HLS pipeline and the mock pipeline to ensure that the severity levels displayed on the map match what a human observer would visually classify.

### What `possible_incident` Detection Does

Two heuristics run on every frame after YOLO inference:

**Large bounding box check:**
```python
possible_incident = any(
    (b[2] - b[0]) * (b[3] - b[1]) > 0.25 * frame_area
    for b in vehicle_boxes
)
```
If any single detected vehicle's bounding box occupies more than 25% of the total frame area, it is flagged. This typically indicates a large vehicle (lorry, bus) that is stopped very close to the camera, which is unusual during normal flow and may indicate a breakdown or accident.

**Stationarity check:**
```python
if not possible_incident and len(current_centers) >= 5 and prev_centers:
    stationary = sum(
        1 for cx, cy in current_centers
        if any(abs(cx - px) < 10 and abs(cy - py) < 10 for px, py in prev_centers)
    )
    if stationary / len(current_centers) > 0.70:
        possible_incident = True
```
If at least 5 vehicles are visible and more than 70% of them are within 10 pixels of their position from the previous frame, the traffic is considered stationary. This fires when a queue is completely stopped, which at normal traffic speeds (even slow urban traffic) would not persist between frames.

When either check is true, `possible_incident=True` is set in the result dict. This causes the map marker to pulse with an amber ring animation and adds a warning badge in the camera popup and sidebar.

---

## 4. Camera Discovery — trafficwatch.py

### What MYT Traffic Watch Is

MYT (Mauritius Telecom) operates a public traffic monitoring service at `https://www.myt.mu/sinformer/trafficwatch/`. They have installed cameras at major Port Louis intersections and stream them publicly as HLS (HTTP Live Streaming) video using Wowza Streaming Engine. The streams are freely accessible without authentication.

Each stream is available as a Wowza-generated HLS playlist at a stable URL like:
```
https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p/playlist.m3u8
```

This playlist file references chunklist files (like `chunklist.m3u8`), which in turn reference individual `.ts` segment files that rotate every few seconds. The chunklist URLs contain a session-specific number (e.g., `chunklist_w674657069.m3u8`) that changes each session — which is why we use `playlist.m3u8` as the stable entry point wherever possible.

### How `discover_cameras()` Works Step by Step

1. **Logs a header** and checks whether `MYT_CAMERAS` (the hardcoded catalogue) is empty. If somehow empty, returns `FALLBACK_CAMERAS` immediately.

2. **Checks ffprobe availability** (`_ffprobe_available()`): runs `ffprobe -version` and checks the return code. ffprobe is the companion tool bundled with ffmpeg.

3. **First pass — resolves a URL for each camera** by calling `_resolve_source(cam, ffprobe_ok)`:
   - If the camera dict has a `source_override` key, that URL is returned immediately (skipping all probing). Used for La Chaussee to go straight to the known-working chunklist URL.
   - Otherwise, builds a candidate list: `playlist.m3u8` → `chunklist.m3u8` → hardcoded fallback chunklist URL → scraped URL (if available)
   - If ffprobe is available, tries each candidate with `_validate_with_ffprobe(url)`. The first URL that returns at least one video stream is accepted.
   - If ffprobe is not available, returns `playlist.m3u8` unvalidated.

4. **Returns the camera list** with `url_candidates` included so the HLS pipeline can rotate through them.

5. **Second pass (if all cameras failed ffprobe validation)**: calls `_scrape_live_urls()` to fetch the MYT web page and extract fresh `.m3u8` URLs using regex. Retries validation with scraped URLs as a 4th candidate.

6. **Logs results**: how many cameras were found, how many were validated.

The returned list is stored in `_active_cameras` in `main.py` and is the authoritative source for `/api/cameras`.

### How ffprobe Validation Works

```python
result = subprocess.run(
    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", url],
    capture_output=True, timeout=15,
)
data = json.loads(result.stdout)
has_video = any(s.get("codec_type") == "video" for s in data.get("streams", []))
```

ffprobe opens the URL and reports all media streams it finds. If at least one stream has `codec_type == "video"`, the URL is considered valid. The timeout is 15 seconds — Wowza servers sometimes take several seconds to respond to the initial playlist request, so a shorter timeout would cause false validation failures.

### URL Candidate Priority Order

For most cameras:
1. `{stream_base}/playlist.m3u8` — stable master playlist (preferred)
2. `{stream_base}/chunklist.m3u8` — direct chunklist without session ID
3. `{stream_base}/chunklist_w{id}.m3u8` — hardcoded rotating chunklist (last-resort)
4. Scraped URL from MYT page (if scraping succeeded and produced a match)

For La Chaussee specifically (`source_override` + `url_candidates`):
1. `chunklist.m3u8` — known to be more reliable than playlist for this camera
2. `playlist.m3u8`
3. `chunklist_w228974167.m3u8`

### What the Scraping Fallback Does

If ffprobe fails all URLs for all cameras (typically meaning the stream service is temporarily unavailable), `_scrape_live_urls()`:
1. Makes an HTTP GET request to the MYT traffic watch page with a browser-like User-Agent
2. Uses a regex to find all `.m3u8` URLs in the page source
3. If BeautifulSoup4 is available, also searches inside `<script>`, `<source>`, and `<video>` tags
4. Matches found URLs to camera IDs using keyword matching (e.g., `CAUDAN_NORTH` → `caudan_north`)
5. Returns a `dict[camera_id, url]` of matched URLs

These scraped URLs are injected as a 4th candidate in the next validation round.

### What Happens if All URLs Fail

If every candidate fails and scraping also fails:
- `_resolve_source()` returns the primary candidate (`playlist.m3u8`) with `validated=False`
- The camera is included in the returned list anyway — the HLS pipeline will attempt it
- `_hls_camera_loop` retries up to 5 times with 60-second delays
- After 5 failures, the camera temporarily uses mock data and retries HLS in the background

---

## 5. Incident Detection — incident_detector.py

### What the 6 Incident Types Are and What Triggers Each

| Type | Label | Trigger | Severity |
|---|---|---|---|
| `sudden_congestion` | Sudden Congestion | Count rises by ≥6 vehicles in one cycle | moderate/severe/critical |
| `sustained_bottleneck` | Sustained Bottleneck | Severity = bottleneck for 2+ consecutive readings | moderate/severe/critical |
| `road_blockage` | Road Blockage | Count was ≥10, drops to ≤2 in one cycle | severe |
| `rapid_buildup` | Rapid Traffic Buildup | 4 consecutive readings each rising ≥1 vehicle, ending at heavy/bottleneck | moderate |
| `camera_freeze` | Camera Offline | No frame received for 90+ seconds (raised by watchdog) | minor |
| `night_low_visibility` | Night Low Visibility | Count = 0 for 3+ consecutive readings between 18:00–06:00 UTC | minor |

### How the Rolling Window History Works

Each camera maintains a `deque` of the last 10 `_Reading` objects (count, severity, timestamp). Every time `analyze()` is called with a new detection result, the checks run against the current reading and its recent history, then the new reading is appended.

This sliding window approach means the system always has a 10-reading context (~20 seconds at 2s interval) to compare against, without accumulating data indefinitely.

### How Confidence Scores Are Calculated

- **`sudden_congestion`**: `min(0.95, 0.60 + spike / 40.0)` — a spike of 6 vehicles gives 0.75, a spike of 14 gives 0.95.
- **`sustained_bottleneck`**: `min(0.95, 0.65 + streak * 0.05)` — a 2-reading streak gives 0.75, a 6-reading streak gives 0.95.
- **`road_blockage`**: fixed at 0.82.
- **`rapid_buildup`**: `min(0.80, 0.50 + total_rise / 40.0)` — a 12-vehicle rise gives 0.80 (capped).
- **`camera_freeze`**: fixed at 1.0 — if the watchdog raises it, there is certainty.
- **`night_low_visibility`**: fixed at 0.65 — moderate certainty since zeros can also occur legitimately.

### How Auto-Resolution Works

After each new reading, `_auto_resolve()` checks every open incident to see if conditions have normalised:

| Incident type | Resolves when |
|---|---|
| `sudden_congestion` | Current severity is free or moderate |
| `sustained_bottleneck` | Current severity is free, moderate, or heavy |
| `road_blockage` | Count rises back to ≥5 |
| `rapid_buildup` | Current severity is free or moderate |
| `night_low_visibility` | Count rises above 0 |
| `camera_freeze` | Resolved explicitly by `resolve_camera_freeze()` when a new frame arrives |

Resolved incidents remain in `_log` (visible via `/api/incidents`) but are removed from `_open` (not shown as active in the sidebar).

### The Tunable Thresholds and Their Effects

```python
SPIKE_THRESHOLD     = 6    # vehicles added in one step → sudden_congestion
BLOCKAGE_HIGH_MIN   = 10   # previous count must be ≥ this
BLOCKAGE_LOW_MAX    = 2    # current count must drop to ≤ this
BOTTLENECK_STREAK   = 2    # consecutive bottleneck readings → sustained_bottleneck
BUILDUP_STREAK      = 4    # consecutive rising readings → rapid_buildup
BUILDUP_MIN_STEP    = 1    # each step must rise ≥ this many vehicles
FREEZE_TIMEOUT_SECS = 90   # seconds without data → camera_freeze
NIGHT_ZERO_STREAK   = 3    # consecutive zero readings at night
```

Increasing `SPIKE_THRESHOLD` makes sudden_congestion less sensitive (requires a bigger jump). Increasing `BOTTLENECK_STREAK` means longer sustained congestion is needed before alerting. Increasing `BUILDUP_STREAK` requires a longer ramp-up period. These were tuned for Port Louis road conditions where traffic changes are relatively gradual compared to motorway conditions.

---

## 6. Claude AI Integration — claude_api.py

### What the Claude API Is and Why It Is Used

Claude is Anthropic's conversational AI. In this system it serves one purpose: converting raw numerical detection data (`vehicle_count=22, severity=heavy`) into plain-English traffic updates that dashboard users can read and act on. This is more informative than showing just numbers and severity labels, and the AI can incorporate route suggestion knowledge specific to Mauritius.

### What `DetectionData` and `TrafficSummary` Contain

**`DetectionData`** is a dataclass created from the pipeline result dict:
- `camera_id`, `vehicle_count`, `severity`, `color`, `timestamp`, `fps_processed`, `frame_shape`

It has a `from_dict()` classmethod so pipeline dicts can be converted with a single call.

**`TrafficSummary`** is the output container:
- `camera_id`, `summary` (2–3 sentence text), `severity`, `vehicle_count`, `timestamp`, `source` (`"claude_api"` or `"template_fallback"`)

### How `TrafficSummaryService` Works

A single shared instance `summary_service` is created at module level and imported by `main.py`. It maintains:
- `_cache: dict[camera_id → TrafficSummary]` — last successful summary per camera
- `_consecutive_failures: int` — tracks API failure streak for logging

The two public methods are:
- `generate_summary(detection)` — general traffic status summary
- `generate_alert_description(detection)` — urgent alert text for heavy/bottleneck events

Both delegate to `_try_claude_api()` with different `prompt_type` values.

### What the System Prompt Tells Claude

```
You are an AI traffic analyst for the IBL Group Traffic Bottleneck Detection
System in Mauritius. Your job is to generate short, clear traffic summaries
for a public-facing dashboard.

Rules:
- Keep every summary to exactly 2-3 sentences.
- State the location, vehicle count, and severity level.
- For heavy or bottleneck severity, suggest one specific alternative route
  in Mauritius.
- Use professional but accessible language (no jargon).
- Do NOT include timestamps, technical details, or markdown formatting.
- Do NOT start with 'Sure' or any preamble — go straight to the summary.
```

The model used is `claude-sonnet-4-20250514` with `max_tokens=200` — sufficient for 2–3 sentences.

### How the Exponential Backoff Retry Works

```
Attempt 0: call Claude API
  → Success: return TrafficSummary (source="claude_api")
  → Fail: wait ~1.0s (±25% jitter) → Attempt 1

Attempt 1: call Claude API
  → Success: return TrafficSummary
  → Fail: wait ~2.0s (±25% jitter) → Attempt 2

Attempt 2: call Claude API
  → Success: return TrafficSummary
  → Fail: give up → return template fallback
```

The delay formula is: `min(1.0 × 2^attempt, 16.0)` seconds with ±25% random jitter. The cap of 16 seconds prevents excessively long waits on later retries. After 3 failed attempts, the template fallback is returned — the API is never retried for this specific call.

### What Template Fallback Is and When It Activates

The template fallback generates summaries from hardcoded string templates, requiring no external API calls. It activates whenever:
- `ANTHROPIC_API_KEY` is not set in the environment
- The `anthropic` Python package is not installed
- All 3 Claude API attempts fail (network error, rate limit, etc.)

Example template output for `heavy` severity at `caudan_north`:
> "Heavy congestion detected at Caudan North with 22 vehicles in the detection zone. Try the A1 motorway northbound or the Quay D waterfront detour."

The fallback result is cached exactly like a real API result — the dashboard always has something to display.

### How the 30-Second Cooldown Works

In `main.py`, `_process_detection()` tracks two dicts:
```python
_last_summary_time: dict[str, float]   # monotonic time of last summary
_last_severity: dict[str, str]         # last severity per camera
```

A new summary is generated only if **either** of these is true:
1. 30 seconds have elapsed since the last summary for this camera (`cooldown_elapsed`)
2. The severity level changed since the last summary (`severity_changed`)

This prevents flooding Claude with 4 cameras × 30 calls per minute. Without the cooldown, the API bill would be enormous and the rate limit would trip constantly.

### The Difference Between `generate_summary()` and `generate_alert_description()`

- **`generate_summary()`**: general-purpose summary for any severity level. Used for routine dashboard updates every 30 seconds or on severity change.
- **`generate_alert_description()`**: specifically for high-severity events, uses a different prompt asking for an "urgent tone" and focusing on the alert rather than a status report. Used when a severity transition to heavy or bottleneck occurs.

### How Caching Works Per Camera

The `_cache` dict maps each `camera_id` to the most recently generated `TrafficSummary`. `get_cached_summary(camera_id)` returns the cached value (or `None`). The `/api/summary/{camera_id}` endpoint checks the cache first and only calls Claude if no cached value exists. Fallback summaries are also cached, so the cache always contains something after the first frame.

---

## 7. FastAPI Backend — main.py

### What FastAPI Is in Simple Terms

FastAPI is a modern Python web framework for building APIs. It uses Python's `async`/`await` syntax to handle many simultaneous requests without blocking, unlike traditional frameworks that handle one request at a time per thread. It automatically generates API documentation, validates request/response types, and handles WebSocket connections natively.

Uvicorn is the server that runs FastAPI — it's the process you actually start (`uvicorn main:app`).

### How the Lifespan Startup Works

The `lifespan` async context manager runs at startup and shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    cameras = await asyncio.to_thread(discover_cameras)
    # ... validate cameras, build task list
    for cam in cameras:
        if src.endswith(".m3u8"):
            # Stagger HLS camera starts by 5s each
            await asyncio.sleep(5)
            task = asyncio.create_task(_hls_camera_loop(...))
        # ...
    yield   # app runs here
    # Shutdown: cancel all tasks
    for task in tasks: task.cancel()
```

**Why staggered startup**: all 4 cameras starting simultaneously would hammer the Wowza server with 4 concurrent ffprobe validation requests and 4 initial frame grabs at the same instant. The 5-second stagger spreads the load.

**`asyncio.to_thread(discover_cameras)`**: `discover_cameras()` calls `subprocess.run(["ffprobe", ...])` which is blocking (it waits for ffprobe to finish). Running it in a thread pool prevents it from blocking the asyncio event loop during startup.

### Every API Endpoint

| Method | URL | Returns | Called by |
|---|---|---|---|
| GET | `/` | Redirect to `/map` or `/login` | Browser on first visit |
| GET | `/login` | HTML login page | Browser |
| POST | `/login` | Redirect to `/map` on success, re-render form on failure | Login form submit |
| GET | `/signup` | HTML signup page | Browser |
| POST | `/signup` | Redirect to `/login` with success message | Signup form submit |
| GET | `/logout` | Clears session, redirects to `/login` | Logout button |
| GET | `/map` | HTML map dashboard (auth required) | Browser |
| GET | `/analytics` | HTML analytics page (auth required) | Browser |
| GET | `/api/traffic` | `dict[camera_id, detection_dict]` — all cameras | map.html initial load |
| GET | `/api/traffic/{id}` | Single camera detection dict | On-demand queries |
| GET | `/api/status` | `{active_cameras, total_detections, bottlenecks, system}` | map.html every 10s |
| GET | `/api/cameras` | List of camera dicts with live state | map.html on init |
| GET | `/api/summary/{id}` | `TrafficSummary` dict (cache or on-demand) | On-demand |
| GET | `/api/summaries` | All cached summaries | On-demand |
| GET | `/api/incidents` | All incidents (limit=100), newest first | Inspection |
| GET | `/api/incidents/active` | Currently unresolved incidents | map.html every 10s |
| GET | `/api/incidents/{id}` | Incidents for specific camera | On-demand |
| POST | `/api/alerts/trigger` | Creates alert with Claude description | Manual trigger |
| GET | `/api/alerts` | Recent alerts (default limit=50) | map.html every 10s |
| POST | `/api/demo/escalate` | Overrides caudan_north to bottleneck | Demo button |
| WS | `/ws/detections` | Streams `latest_detections` JSON every 1s | map.html WebSocket |

### How the WebSocket `/ws/detections` Works and Why It Is Better Than Polling

The WebSocket endpoint accepts a persistent bidirectional connection from the browser. Once connected:
```python
while True:
    await websocket.send_text(json.dumps(latest_detections))
    await asyncio.sleep(1)
```

It pushes the full `latest_detections` dict to every connected browser every second. The browser applies the data immediately without waiting for the next poll cycle.

**Why better than polling**: HTTP polling (making a GET request every N seconds) introduces latency of up to N seconds before the browser sees an update. With WebSocket, the browser sees each update within ~1 second of it being stored in `latest_detections`. This makes the severity badges and map markers feel live rather than laggy. WebSockets also reduce HTTP overhead — one persistent connection instead of a new TCP connection every few seconds.

The browser reconnects automatically if the WebSocket drops:
```javascript
_ws.onclose = () => setTimeout(connectWS, 3000);
```

### How Session-Based Authentication Works

Authentication uses server-side sessions stored in signed cookies via the `SessionMiddleware`:

1. User submits the login form: `POST /login` with `username=admin&password=admin123`
2. If credentials match `DEMO_USERS`, `request.session["user"] = username` is set
3. The session is serialised, signed with a secret key, and stored as a cookie in the browser
4. On every subsequent request, `get_current_user(request)` reads `request.session.get("user")`
5. Protected routes (`/map`, `/analytics`) redirect to `/login` if `get_current_user` returns `None`
6. `GET /logout` calls `request.session.clear()` which removes the session cookie

The secret key is `"traffic-mauritius-secret-key-2026"` — hardcoded. In production this must be a random secret loaded from the environment.

Demo credentials (hardcoded in `DEMO_USERS`):
- `admin` / `admin123`
- `user` / `password`

### How `_process_detection()` Works — The Full Hook Pipeline

This coroutine is called after every detection result is stored. It chains several operations:

```
1. asyncio.create_task(_save_snapshot())       ← non-blocking DB write
2. if heavy/bottleneck:
     asyncio.create_task(_save_bottleneck_event())  ← non-blocking DB write
3. incident_detector.analyze(camera_id, count, severity)
   → if new incidents:
       append each to alert_log (immediate, no Claude call)
4. Check cooldown and severity change:
   → if (30s elapsed) OR (severity changed):
       summary = await summary_service.generate_summary(result)
5. if severity changed to heavy/bottleneck:
       alert_desc = await summary_service.generate_alert_description(result)
       append to alert_log
```

Note that `create_task()` schedules the DB writes without awaiting them — the function continues immediately. This prevents a slow database from delaying the detection loop.

### How Alert Generation Works

Alerts end up in `alert_log` (a Python list) via two paths:
1. **Incident-triggered**: every new incident (e.g., `sudden_congestion`) appends an alert immediately using the incident's description text, no Claude call required.
2. **Severity-transition triggered**: when a camera transitions to heavy or bottleneck, `generate_alert_description()` is called and the Claude-generated text is appended.

`/api/alerts` returns `alert_log[-limit:][::-1]` — the most recent N alerts, newest first.

### How `_last_severity` and `_last_summary_time` Prevent API Spam

```python
cooldown_elapsed = (now - _last_summary_time.get(camera_id, 0.0)) >= 30.0
severity_changed = current_severity != _last_severity.get(camera_id, "free")
if not cooldown_elapsed and not severity_changed:
    return  # skip
```

At 0.5 fps per camera with 4 cameras, there are approximately 120 detection results per minute. Without the cooldown, Claude would be called 120 times per minute (2 per second). With the cooldown, it is called at most once per 30 seconds per camera = 8 times per minute maximum, plus additional calls on severity changes.

### How the Demo Escalate Endpoint Works

```python
@app.post("/api/demo/escalate")
async def api_demo_escalate():
    demo_result = {
        "camera_id": "caudan_north",
        "vehicle_count": 38,
        "severity": "bottleneck",
        "color": "#8b31c7",
        ...
    }
    latest_detections["caudan_north"] = demo_result
    await _process_detection("caudan_north", demo_result)
    return {"ok": True}
```

This directly injects a fake bottleneck reading into `latest_detections` and triggers the full processing pipeline (DB writes, incident detection, Claude alert). The WebSocket broadcasts it to all connected browsers within 1 second, turning the Caudan North marker purple and triggering the alert panel — useful for demonstrating the system without waiting for real traffic conditions.

### How Database Writes Work and What Happens When DB Is Unavailable

`_save_snapshot()` and `_save_bottleneck_event()` both begin with:
```python
if not DB_AVAILABLE or _AsyncSession is None:
    return
```

If PostgreSQL is unavailable, the function exits immediately — no error, no crash, no log spam. When DB is available, they use SQLAlchemy async sessions to upsert camera records and insert snapshot/event rows. Errors during the write are caught and logged as warnings, not exceptions, so a DB write failure never crashes the detection loop.

---

## 8. Database — database.py, schema.sql

### Every Table Explained

**`users`**
Stores authenticated user accounts.
- `id` (UUID): unique user identifier
- `username` (VARCHAR 50): unique login name
- `PasswordHash` (TEXT): bcrypt-hashed password
- `role` (UserRole enum: admin/user): permission level
- `CreatedAt` (TIMESTAMPTZ): account creation time
- `LastLogin` (TIMESTAMPTZ): updated on each login

**`cameras`**
Stores the camera registry (populated by `_save_snapshot()` on first detection).
- `id` (VARCHAR 50): camera_id (e.g., `caudan_north`)
- `name` (VARCHAR 100): human-readable location name
- `latitude`, `longitude` (FLOAT): GPS coordinates
- `StreamUrl` (TEXT): the stream URL
- `isActive` (BOOLEAN): whether camera is currently active
- `RegisteredAt`, `LastSeen` (TIMESTAMPTZ): lifecycle timestamps

**`TrafficSnapshots`**
One row per frame processed. High write volume — every 2 seconds per camera = ~7,200 rows/hour.
- `id` (UUID): row identifier
- `CameraId` (FK → cameras): which camera
- `SnapshotTime` (TIMESTAMPTZ): when the frame was processed
- `VehicleCount` (INTEGER): smoothed vehicle count
- `Severity` (VARCHAR 20): free/moderate/heavy/bottleneck
- `fpsProcessed` (FLOAT): how fast the frame was processed
- `FrameShape` (INTEGER[]): `[height, width]`
- `RawResult` (JSONB): full result dict for debugging

**`BottleneckEvents`**
One row per heavy/bottleneck detection. Lower volume than snapshots.
- `id` (UUID): event identifier
- `CameraId` (FK → cameras): which camera
- `DetectedAt` (TIMESTAMPTZ): when detected
- `Severity` (BottleneckSeverity enum): heavy or bottleneck
- `VehicleCount` (INTEGER): count at time of detection
- `Color` (VARCHAR 10): hex colour code
- `latitude`, `longitude` (FLOAT): camera coordinates

**`alerts`**
Persisted alert records (currently alerts are only stored in-memory; this table supports future persistence).
- `id` (SERIAL): auto-increment row ID
- `CameraId` (FK → cameras): which camera triggered the alert
- `EventId` (FK → BottleneckEvents): the triggering event
- `AlertType` (VARCHAR 50): type label
- `Severity` (VARCHAR 20): severity at time of alert
- `Message` (TEXT): alert text
- `TriggeredAt` (TIMESTAMPTZ): when raised
- `acknowledged` (BOOLEAN): whether a user has seen/dismissed it

**`AuditLog`**
Logs user actions for accountability (not yet populated by the application).
- `id` (SERIAL): auto-increment
- `userId` (FK → users): who performed the action
- `action` (VARCHAR 100): what they did
- `target` (VARCHAR 100): what they acted on
- `IPAddress` (INET): caller's IP address
- `PerformedAt` (TIMESTAMPTZ): when
- `success` (BOOLEAN): whether it succeeded

### Why PostgreSQL Was Chosen

PostgreSQL was chosen because:
1. It supports `TIMESTAMPTZ` (timezone-aware timestamps) natively, important for UTC storage
2. It handles the `JSONB` type for `RawResult`, enabling flexible querying of raw detection data
3. It supports `GEOGRAPHY` (PostGIS) for the original spatial location columns
4. `UUID` primary keys are natively supported
5. `asyncpg` (the async driver) is mature, fast, and well-supported

### How Graceful Degradation Works When PostgreSQL Is Offline

`database.py` runs a TCP probe at import time before attempting to create the SQLAlchemy engine:

```python
def _postgres_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=3):
            return True
    except OSError:
        return False

if not _postgres_reachable():
    logger.warning("[db] PostgreSQL not reachable ...")
    # DB_AVAILABLE remains False, engine remains None
else:
    # Try to create engine
    engine = create_async_engine(...)
    AsyncSessionLocal = sessionmaker(...)
    DB_AVAILABLE = True
```

The 3-second timeout means the application starts within 3 seconds even if PostgreSQL is completely unreachable (firewall block, wrong host). Without this probe, SQLAlchemy's lazy connection would not fail until the first actual query, potentially crashing a background task rather than logging a clean warning at startup.

### What the `DB_AVAILABLE` Flag Does

Every DB-touching function in `main.py` checks `if not DB_AVAILABLE: return` at the top. This single boolean acts as a circuit breaker — when the database is down, all persistence calls are no-ops. The detection pipeline continues running, the dashboard continues displaying live data, and alerts continue generating. Only the historical record is lost.

---

## 9. Frontend — map.html

### How Leaflet.js Is Used for the Map

Leaflet is a JavaScript mapping library that renders interactive maps in the browser. The map is initialised centred on Mauritius:
```javascript
const map = L.map('map').setView([-20.168, 57.502], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
```

Two tile layer options are toggled between: OpenStreetMap (street view) and ESRI satellite imagery. The satellite option uses `server.arcgisonline.com` tiles at zoom levels up to 18.

### How Camera Markers Are Placed and Coloured

Camera coordinates are defined in `CAMERA_LOCS` (hardcoded in the HTML) and updated from `/api/cameras` on startup. For each camera:
```javascript
cameraMarkers[id] = L.marker(loc.coords, { icon: camIcon(SEV_COLOR.unknown) })
    .addTo(map)
    .bindPopup(...)
```

`camIcon(color)` creates an `L.divIcon` — a small circle with a glow shadow in the severity colour:
```javascript
html: `<div style="width:16px;height:16px;background:${color};border:2.5px solid #fff;
       border-radius:50%;box-shadow:0 0 10px ${color},0 0 4px rgba(0,0,0,.6);"></div>`
```

When `possible_incident === true`, `camIncidentIcon(color)` is used instead, which adds two staggered amber rings (`border: 2.5px solid #f59e0b`) with the `incidentRingPulse` CSS animation, making the marker visually pulse.

### How the WebSocket Client Works in the Browser

```javascript
const wsUrl = `ws://${window.location.host}/ws/detections`;
let _ws = null;

function connectWS() {
    _ws = new WebSocket(wsUrl);
    _ws.onmessage = e => { try { applyTrafficData(JSON.parse(e.data)); } catch (_) {} };
    _ws.onclose   = () => setTimeout(connectWS, 3000);
    _ws.onerror   = () => _ws.close();
}
```

`window.location.host` dynamically picks up the server's hostname and port — whether the user accesses via `localhost:8000` or a LAN IP address like `192.168.1.5:8000`, the WebSocket URL is always correct.

On message received, the JSON is parsed and passed directly to `applyTrafficData()`. On close or error, a 3-second delay fires before reconnecting, preventing a tight reconnection loop if the server is temporarily unavailable.

### How the Polling Fallback Works

While the WebSocket handles live traffic data, HTTP polling handles data that changes less frequently:
```javascript
async function poll() {
    await Promise.all([ fetchStatus(), fetchIncidents(), fetchAlerts() ]);
}
setInterval(poll, POLL_MS);  // POLL_MS = 10000ms
```

`fetchStatus()`, `fetchIncidents()`, and `fetchAlerts()` each make independent `fetch()` calls. `Promise.all()` runs them in parallel to minimise total poll time. `fetchTraffic()` is only called once at startup to populate the map before the WebSocket has connected.

### How `applyTrafficData()` Updates the UI

```javascript
function applyTrafficData(data) {
    for (const [id, det] of Object.entries(data)) {
        const color = det.color ?? SEV_COLOR[det.severity];
        const hasIncident = det.possible_incident === true;

        // Update marker icon
        cameraMarkers[id].setIcon(hasIncident ? camIncidentIcon(color) : camIcon(color));

        // Update popup content (severity badge, vehicle count, incident badge, route panel)
        cameraMarkers[id].setPopupContent(`...`);
        
        // Update heatmap data
        heatPoints.push([lat, lng, Math.min(det.vehicle_count / 35, 1.0)]);

        // Update sidebar camera list
        listEl.innerHTML += `...camera list item...`;
    }
    heatLayer.setLatLngs(heatPoints);
}
```

The heatmap normalises vehicle count to 0–1 using `/35` as the scale factor (35 vehicles = maximum heat intensity).

### How the Alert Panel Works

The bell button in the navbar toggles an `alert-dropdown` div. The badge shows the count of recent alerts. Each alert shows: location name, severity, vehicle count, and time. Clicking an alert item flies the map to that camera's location. `has-alerts` CSS class triggers a rocking bell animation when alerts are present.

### How the Incident Badge and Pulse Animation Work

The `#incidentBadge` in the sidebar shows the count of active incidents. The `incidentRingPulse` keyframe animation expands a ring from scale 1 to scale 2.8 while fading to opacity 0, creating a ripple effect. Two rings are used with a 0.7-second offset so the ripple appears continuous rather than periodic.

### How the Demo Button Works

```javascript
async function triggerDemo() {
    try { await fetch(`${API_BASE}/api/demo/escalate`, { method: 'POST' }); } catch (_) {} }
```

The button is styled with low opacity (0.2) by default and only becomes fully visible on hover, so it is not accidentally clicked during a live demo. On click it fires a POST request to `/api/demo/escalate` which injects the fake bottleneck reading.

### How the Route Suggestion Panel Works

When a camera's severity is `heavy` or `bottleneck`, the popup includes an input field and Google Maps button:
```javascript
const routePanel = isHeavy ? `
    <input id="routeDest_${id}" type="text" placeholder="Enter destination…">
    <button onclick="openRoute('${id}',${lat},${lng})">Get Alternative Route ↗</button>
` : '';
```

`openRoute()` reads the destination text and opens:
```
https://www.google.com/maps/dir/?api=1&origin=${lat},${lng}
    &destination=${encodeURIComponent(dest)}&avoid=congestion
```

The `avoid=congestion` parameter asks Google Maps to find an alternative route avoiding the congestion area.

### How Satellite/Street Map Toggle Works

Two `L.tileLayer` objects are defined. A control button toggles between them by removing one and adding the other to the map. The button label switches between `🗺 Street` and `🛰 Satellite`.

---

## 10. Analytics — analytics.html

### What Chart.js Is

Chart.js is a JavaScript charting library that renders canvas-based charts. It is loaded from CDN (`chart.js@4.4.0`). The analytics page uses it to display three charts and four KPI cards.

### What the 3 Charts Show and How the Data Is Generated

The analytics page uses **simulated data**, not live detection data. The `simProfile()` function generates a realistic 24-hour vehicle count profile using a double Gaussian curve:
```javascript
function simProfile(seed, peak1=8, peak2=17, base=4) {
    return HOURS.map((_, h) => {
        const morning = base + 28 * Math.exp(-0.5 * ((h - peak1) / 1.5) ** 2);
        const evening = base + 32 * Math.exp(-0.5 * ((h - peak2) / 1.8) ** 2);
        const noise   = (Math.sin(seed * h + seed) * 2 + Math.random() * 3);
        return Math.max(0, Math.round(morning + evening + noise));
    });
}
```
This creates a morning rush peak (around 07:00–09:00) and an evening peak (around 16:00–18:00) with small random noise, mimicking real Port Louis commuting patterns. Each camera gets a slightly different peak time via `seed` offsets.

**Line chart** (`chartLine`): Shows each camera's 24-hour vehicle count as a separate coloured line. Full span of the day, X-axis = hour (00:00–23:00), Y-axis = vehicle count. Allows comparison across cameras.

**Bar chart** (`chartBar`): Shows the combined total vehicle count for all cameras by hour. Bar colour reflects the combined severity level at that hour (purple=bottleneck, red=heavy, orange=moderate, green=free).

**Doughnut chart** (`chartDoughnut`): Shows the proportion of all hours×cameras readings that fell into each severity category. Gives an overall picture of the day's congestion level. The cutout is 65% (ring style rather than filled pie).

### What the 4 KPI Cards Show

- **Peak Vehicles Detected**: maximum single-camera count across all 24 hours
- **Avg Vehicles / Hour**: mean of the combined total across all 24 hours
- **Bottleneck Hours**: number of hours where combined count ≥ 30 × number of cameras
- **Free-Flow Hours**: number of hours where combined count < 5 × number of cameras

---

## 11. Authentication — login.html, signup.html

### How the Login Flow Works End to End

1. User visits any protected route (e.g., `/map`)
2. `get_current_user(request)` returns `None` (no session) → `RedirectResponse(url="/login")`
3. Browser renders `login.html` — a Jinja2 template
4. User types credentials and submits `POST /login`
5. FastAPI reads `username` and `password` from form data
6. `DEMO_USERS` dict is checked: `if username in DEMO_USERS and DEMO_USERS[username] == password`
7. Match → `request.session["user"] = username` → `RedirectResponse(url="/map", status_code=302)`
8. No match → `TemplateResponse("login.html", {"error": "Invalid username or password."}, status_code=401)`

### How Jinja2 Templating Works for Error Messages

The template uses conditional blocks:
```html
{% if error %}
<div class="error-msg">{{ error }}</div>
{% endif %}

{% if success %}
<div class="success-msg">{{ success }}</div>
{% endif %}
```

FastAPI passes `error` and `success` as template context variables. If `error` is `None` (which it is on a clean page load), the block is not rendered. Jinja2 automatically escapes the string to prevent XSS.

### What the Demo Credentials Are

| Username | Password | Role |
|---|---|---|
| `admin` | `admin123` | admin |
| `user` | `password` | user |

These are displayed on the login page itself in a "Demo Credentials" box. Both credentials give identical access to all features — the role distinction is defined in the database schema but not enforced by the current application code.

---

## 12. Configuration and Environment

### What Every Variable in `.env` Does

| Variable | Example Value | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` | Authenticates Claude API calls. Without this, all summaries use template fallback. |
| `DB_HOST` | `localhost` | PostgreSQL server hostname (default: localhost) |
| `DB_PORT` | `5432` | PostgreSQL port (default: 5432) |
| `DB_USER` | `postgres` | PostgreSQL username |
| `DB_PASSWORD` | `your-postgres-password` | PostgreSQL password. No default — the value used to be a literal committed to source; it now must be set in `.env` or persistence stays off. |
| `DB_NAME` | `trafficsystem` | Database name |

`DB_HOST`, `DB_PORT` and `DB_USER` have sane defaults in `database.py`. `DB_PASSWORD` does not — leaving it unset is a supported "no persistence" mode, not a misconfiguration. `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY`, depending on `SUMMARY_PROVIDER`) is the only variable strictly required for AI-generated (non-template) summaries.

### What `requirements.txt` Contains and Why Each Package Is Needed

```
fastapi==0.115.0         — Web framework, REST API, WebSocket, form handling
uvicorn==0.30.6          — ASGI server to run FastAPI
jinja2==3.1.4            — HTML template engine for login/map/analytics pages
python-multipart==0.0.9  — Enables FastAPI to read HTML form POST bodies
itsdangerous==2.2.0      — Signs session cookies securely
ultralytics              — YOLOv8 model loading, inference, result parsing
opencv-python-headless   — Image preprocessing (CLAHE, colour conversion)
                           Headless = no GUI dependencies (server-safe)
requests                 — HTTP client for MYT page scraping
beautifulsoup4           — HTML parsing for scraping .m3u8 URLs from script tags
lxml                     — Fast HTML parser used by BeautifulSoup
selenium                 — Browser automation (available but not currently used)
webdriver-manager        — Companion to Selenium
numpy                    — NumPy array handling for raw video frames
imageio-ffmpeg           — Ships ffmpeg binaries on some platforms (supplementary)
anthropic                — Anthropic Claude API SDK
python-dotenv            — Loads .env file into os.environ at startup
```

Note: `ffmpeg` and `ffprobe` must be installed separately as system binaries and added to PATH. They are not Python packages. Download from `https://www.gyan.dev/ffmpeg/builds/` on Windows.

### How `start.bat` and `start.sh` Work

Both scripts start the Uvicorn server:
```
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- `--reload`: auto-restarts the server when Python files change (development mode)
- `--host 0.0.0.0`: binds to all network interfaces so the dashboard is accessible from other devices on the same network (e.g., a laptop presenting to a TV via WiFi)
- `--port 8000`: standard development port

The `start.bat` ends with `pause` to keep the terminal window open when the server stops.

### How `test_streams.py` Works and When to Use It

Run before a demo to verify all 4 MYT streams are accessible:
```
python test_streams.py
```

For each camera it tries three URL candidates via `requests.get()` (15-second timeout). If at least one URL returns HTTP 200, it then runs a full ffmpeg frame grab (30-second timeout) to confirm a video frame can actually be decoded. Reports `HTTP:PASS/FAIL FRAME:PASS/FAIL` per camera.

If all HTTP checks fail, it scrapes the MYT web page looking for fresh `.m3u8` URLs and prints them. Use this output to update `trafficwatch.py` if the stream URLs have rotated.

Run this tool:
- Before any important demo
- When the dashboard shows cameras stuck in mock mode
- When adding a new camera to the system
- After Mauritius Telecom performs maintenance on their streaming server

---

## 13. Error Handling and Resilience

### What Happens When an HLS Stream Goes Down

1. `_grab_single_frame()` tries all `url_candidates` in order
2. Every URL returns `None` (timeout or HTTP error)
3. `consecutive_failures` increments in `_detect_loop`
4. After 5 consecutive failures: `RuntimeError` is raised, `_detect_loop` terminates
5. `run_hls_pipeline` catches the exception, waits `RETRY_DELAY=10s`, increments `attempt`
6. After `MAX_RETRIES=3` inner attempts: generator exhausts (StopIteration)
7. `_hls_camera_loop` catches StopIteration, waits `_HLS_RETRY_SECS=60s`, outer_attempt increments
8. After `_MAX_HLS_ATTEMPTS=5` outer attempts: logs `"WARNING: Camera X using MOCK DATA"`
9. Runs 20 mock frames (~60 seconds of mock data)
10. Resets `outer_attempt=0` and restarts the entire HLS retry sequence

Total worst-case time from stream failure to mock fallback:
```
Inner: 5 failures × 2s interval = ~10s
Inner retries: 3 × 10s delay = 30s
Outer retries: 5 × 60s delay = 300s
Total: ~340 seconds (~6 minutes)
```

### What Happens When Claude API Has No Credits

1. `_call_claude_api()` raises an `anthropic.APIError` or similar exception
2. `_try_claude_api()` catches it, logs a warning, waits backoff delay
3. Retries up to 3 times with exponential backoff (1s, 2s, 4s delays)
4. After 3 failures: `_fallback()` is called
5. Template-based summary is generated and cached
6. `source="template_fallback"` is set in the result
7. Dashboard displays the template text without any indication to the user that Claude was unavailable

The system continues operating indefinitely without Claude API credits — summaries are simply less descriptive.

### What Happens When PostgreSQL Is Offline

1. At startup: `_postgres_reachable()` TCP probe fails (3-second timeout)
2. `DB_AVAILABLE = False` is set — no engine is created
3. All DB write functions (`_save_snapshot`, `_save_bottleneck_event`) return immediately
4. Application starts normally, logs a single warning: `"PostgreSQL not reachable — starting without persistence"`
5. All live data continues flowing: WebSocket, map, alerts all work
6. Historical data is simply not saved — no snapshots or bottleneck events are persisted

When PostgreSQL comes back online, a server restart is required (the TCP probe only runs at startup).

### What Happens When FFmpeg Is Not Installed

1. `_ffmpeg_available()` is called in `run_hls_pipeline()`
2. `subprocess.run(["ffmpeg", "-version"])` raises `FileNotFoundError`
3. `_ffmpeg_available()` returns `False`
4. `run_hls_pipeline()` logs an error with installation instructions and returns without yielding
5. `_hls_camera_loop` receives a generator that immediately exhausts (StopIteration)
6. After 5 outer attempts, camera falls back to mock data permanently
7. Warning logged: `"Camera X using MOCK DATA"`

ffprobe validation in `trafficwatch.py` similarly checks `_ffprobe_available()` and skips validation if not found, returning all cameras with `validated=False`. The pipeline still attempts to run.

### How the Mock Fallback Activates and What It Produces

Mock data is produced by `run_mock_pipeline()` when:
- HLS retries are exhausted
- FFmpeg is not installed

The mock generator runs a 7-stage traffic scenario cycle (free→moderate→heavy→bottleneck→heavy→moderate→free) with random target counts and random dwell times. It yields data every ~133ms (7.5 fps simulated) and is throttled by `_POLL_INTERVAL=3.0s` in `_camera_loop`.

The result dict is identical in structure to real pipeline output, with `"source": "mock"` implied by the camera's `source` field in `_active_cameras`. The map, WebSocket, alerts, and incidents all function normally with mock data — incidents can be triggered and Claude summaries are generated from the simulated counts.

---

## 14. Known Limitations and Future Improvements

### Current Accuracy Limitations of Night-Time Detection

- YOLOv8m at `CONF_THRESHOLD=0.25` with CLAHE preprocessing improved night accuracy significantly, but partially obscured vehicles (behind poles, partially out of frame) are still missed
- Street lighting varies by camera angle — some cameras have direct overhead illumination, others rely on ambient light from buildings. CLAHE helps with the latter but cannot recover detail that was never captured
- The brightness threshold of 80/255 for the dark-frame boost is a global average — a frame could have bright headlights boosting the average while the majority of the frame is dark
- A potential improvement would be training a custom YOLOv8 model fine-tuned on Port Louis night footage rather than relying on COCO-pretrained weights

### Why La Chaussee Stream Is Less Reliable Than the Others

The La Chaussee camera uses the `prod` subdomain (`stream.myt.mu/prod/`) while Caudan North uses `rh/prod`. The `prod` server appears to have shorter session timeouts and more frequent segment rotation than the `rh/prod` server. The `playlist.m3u8` endpoint sometimes returns an empty or malformed playlist before the chunklist is ready. The `url_candidates` fallback (chunklist first, playlist second) mitigates this by going directly to the chunklist, but if the Wowza session expires between retries, both URLs fail simultaneously until the next session is established.

A robust fix would be to scrape the MYT page in real time to get the current session-specific chunklist URL before each frame grab, but this adds 1–2 seconds of HTTP overhead per frame.

### What Would Be Needed for Production Deployment

| Requirement | Current State | Production Need |
|---|---|---|
| Authentication | Hardcoded dict | Database-backed with bcrypt hashed passwords |
| Session secret | Hardcoded string | Environment variable, rotated regularly |
| DB credentials | In `.env` file | Secret manager (AWS Secrets Manager, Azure Key Vault) |
| Alerts | In-memory list | Persisted to DB, with acknowledgement workflow |
| HTTPS | None (HTTP only) | TLS certificate, reverse proxy (nginx/Caddy) |
| Multi-worker | Single Uvicorn process | Gunicorn with multiple Uvicorn workers |
| `latest_detections` sharing | Per-process dict | Redis or shared memory for multi-worker deployments |
| Error monitoring | Python logging | Sentry or similar APM tool |
| DB connection pooling | Default SQLAlchemy pool | PgBouncer for connection pooling at scale |
| Model inference | CPU (slow) | GPU instance (NVIDIA T4 or better) for <0.5s inference |
| Stream redundancy | Single MYT source | Secondary stream source or cached frame fallback |

### Features Planned but Not Yet Implemented

- **Real-time database-backed alerts**: alerts are currently in-memory and lost on server restart
- **AuditLog writes**: the `AuditLog` table is defined in the schema but nothing populates it
- **Signup → DB write**: the signup form validates and redirects but does not create a database user record
- **Role-based access control**: `admin` vs `user` roles are defined but not enforced at the route level
- **Historical trend charts connected to live DB**: the analytics page uses simulated data; it could query `TrafficSnapshots` for real historical trends
- **Email/SMS alerts**: the alert system currently only shows in-browser notifications
- **Camera health dashboard**: a dedicated view showing uptime, frame rate, and failure history per camera
- **Custom ROI (Region of Interest)**: the current pipeline counts all vehicles in the full frame; in production, a polygon ROI would focus detection on the road lanes only, excluding parked vehicles and pedestrians
- **Speed estimation**: bounding box displacement between consecutive frames could be used to estimate vehicle speed, enabling detection of dangerously fast traffic as well as stationary queues
- **Integration with traffic signals**: an API that feeds severity data to smart traffic light controllers to adjust signal timing in response to detected congestion

---

*This document covers every file and component in the IBL Capstone MA02 2026 project as of 2026-04-27. For questions about the codebase, contact the project team via the GitHub repository.*

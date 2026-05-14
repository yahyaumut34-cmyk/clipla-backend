# Multi-Agent Video Editing System - Production-Grade Backend

**Version 2.4** - Agents + Compiler + Voice DSL + Cost Guard

## System Flow

```
Request → Cost Guard Check → Voice DSL → Agent Chain → Edit Plan → FFmpeg Commands
         ↓                    ↓           ↓             ↓            ↓
    Token Limit OK      Internal DSL   4 Agents    Validated   Executable
    (Before Execution)   (Normalized)  (Guarded)     Plan     (No Execution)
```

## Architecture

```
User Request → Cost Guard (Check Token Limits)
                    ↓ (Pass)
              Voice DSL Parser → Internal DSL
                    ↓
              Agent Chain (FAIL-FAST + COST-GUARDED)
                    ↓
          1. CommandAgent (deterministic, ~300-500 tokens)
                    ↓
          2. AnalysisAgent (validated, 0 tokens - no LLM)
                    ↓
          3. StrategyAgent (strict schema, ~1000-2000 tokens)
                    ↓
          4. QCAgent (comprehensive validation, ~1500-2000 tokens)
                    ↓
          Validated Edit Plan (Total: ~2800-4500 tokens)
                    ↓
          Timeline Compiler
                    ↓
          FFmpeg Commands (JSON)
```

## NEW: Cost Guard System

**Module:** `utils/cost_guard.py`

**Purpose:** Token and credit control for API calls and agent chain

**Features:**
- ✅ Pre-execution token estimation
- ✅ Per-request token limits
- ✅ Per-agent token limits
- ✅ Configurable via .env
- ✅ Fail-fast before expensive operations
- ✅ Cost estimation endpoint

**Token Estimates:**
```python
CommandAgent:   ~300-500 tokens   (user command → intent)
AnalysisAgent:  0 tokens          (no LLM, audio analysis only)
StrategyAgent:  ~1000-2000 tokens (intent + analysis → edit plan)
QCAgent:        ~1500-2000 tokens (validation + corrections)
---
Total per request: ~2800-4500 tokens
```

**Configuration (.env):**
```bash
# Enable/disable cost checking
COST_CHECK_ENABLED=true

# Maximum tokens per complete request
MAX_TOKENS_PER_REQUEST=10000

# Maximum tokens per single agent
MAX_TOKENS_PER_AGENT=5000
```

**NEW Endpoint:** `POST /api/video/estimate-cost`

**Cost Check Flow:**
```
1. User sends process request
   ↓
2. Cost Guard estimates tokens (conservative)
   ↓
3. Check against MAX_TOKENS_PER_REQUEST
   ↓
4. If exceeds → Return 429 error (no LLM calls made)
   ↓
5. If passes → Start agent chain
   ↓
6. After analysis → Re-check with actual segment count
   ↓
7. If still passes → Continue to strategy agent
```

**Error Response (429):**
```json
{
  "error": "token_limit_exceeded",
  "message": "Estimated tokens (12500) exceed request limit (10000)",
  "estimated_tokens": 12500,
  "limit": 10000,
  "breakdown": {
    "CommandAgent": 400,
    "AnalysisAgent": 0,
    "StrategyAgent": 7000,
    "QCAgent": 5100
  }
}
```

---

## Voice DSL Parser

**Service:** `services/voice_dsl.py`

**Purpose:** Transform natural language or STT output into internal Domain-Specific Language (DSL)

**Input:**
- Raw text from voice commands or STT
- Optional word-level timestamps

**Output:** Deterministic DSL JSON:
```json
{
  "version": "1.0",
  "confidence": 0.85,
  "actions": [
    {
      "type": "cut",
      "target": "silence",
      "parameters": {
        "threshold_seconds": 1.0
      },
      "confidence": 0.9
    },
    {
      "type": "speed",
      "target": "low-energy",
      "parameters": {
        "factor": 1.5,
        "adaptive": true
      },
      "confidence": 0.85
    },
    {
      "type": "audio",
      "target": "all",
      "parameters": {
        "normalize": true
      },
      "confidence": 0.8
    }
  ],
  "metadata": {
    "original_text": "cut all silence and speed up boring parts, normalize audio",
    "normalized_text": "cut silence speed low-energy normalize audio",
    "detected_keywords": ["cut", "silence", "speed", "boring", "normalize"],
    "action_count": 3
  }
}
```

**Supported Action Types:**
- `cut`: Remove/trim segments
- `speed`: Change playback speed
- `audio`: Adjust audio (normalize, volume, mute)
- `keep`: Preserve specific content
- `trim`: Precise time-based cuts

**Supported Targets:**
- `silence`: Silent segments
- `low-energy`: Boring/slow parts
- `high-energy`: Exciting/active parts
- `segment`: Specific time ranges
- `all`: Entire video

**NEW Endpoint:** `POST /api/video/parse-dsl`

**Keyword Detection:**
- Cut: "cut", "remove", "delete", "trim", "eliminate"
- Speed: "speed", "fast", "faster", "quick", "accelerate"
- Audio: "audio", "volume", "normalize", "loud", "quiet"
- Keep: "keep", "preserve", "save", "maintain"

**Smart Defaults:**
- Ambiguous commands → safe default (cut silence @ 1s)
- No speed factor → 1.5x default
- No volume → normalize default
- Low confidence (< 0.3) → error

---

## Timeline Compiler

**Service:** `services/timeline_compiler.py`

**Purpose:** Convert validated edit plans into executable ffmpeg command strings (no execution)

**Supported Operations:**
- ✅ **Cut/Trim:** Extract segments, remove silence
- ✅ **Merge/Concat:** Combine multiple segments
- ✅ **Speed:** Change playback speed (setpts + atempo)
- ✅ **Audio:** Normalize, volume adjust, mute

**Output:** Deterministic JSON with:
```json
{
  "status": "compiled",
  "input_edit_plan": { /* original edit plan */ },
  "generated_ffmpeg_commands": [
    {
      "command_type": "cut",
      "command": "ffmpeg -i input.mp4 -ss 10.5 -t 5.2 ...",
      "input_file": "/path/to/video.mp4",
      "output_file": "/tmp/video_edits/segment_001.mp4",
      "description": "Extract segment 1: 10.5s-15.7s"
    }
  ],
  "total_commands": 5,
  "estimated_steps": 6,
  "warnings": ["Speed changes require re-encoding"]
}
```

**NEW Endpoint:** `POST /api/video/compile/{job_id}`

**Important:** Commands are NOT executed, only generated as strings.

---

#### 1. CommandAgent (Intent Parser)
**Role:** Parse natural language commands into structured intent

**Configuration:**
- Temperature: 0.0 (fully deterministic)
- Max tokens: 500
- Input validation: Min 5 chars command text
- Output: Strict UserIntent schema

**Validation:**
- `intent`: 10-200 chars
- `target_style`: Only ["fast-paced", "balanced", "cinematic", "energetic"]
- `cut_preference`: Only ["aggressive", "moderate", "conservative"]
- `focus_areas`: Min 1, max 10 items, all non-empty strings
- `preserve_elements`: Max 10 items
- `pacing_adjustment`: Only ["speed-up", "maintain", "slow-down"]
- `audio_normalization`: boolean

**Fallback:** If LLM fails, returns balanced/moderate defaults

**Failure Mode:** Raises ValueError on critical errors

---

#### 2. AnalysisAgent (Video Analyzer)
**Role:** Analyze video for silence, energy levels, speech segments

**Validation:**
- Video file must exist
- Duration > 0, ≤ 7200s (2 hours max)
- Must produce at least 1 segment
- Sample rate > 0

**Output:** VideoAnalysis with:
- `duration`: Video duration in seconds
- `sample_rate`: Audio sample rate (Hz)
- `segments`: List of classified segments (silence/low-energy/high-energy)
- `statistics`: Percentage breakdown

**Failure Mode:** Raises ValueError on invalid video or analysis failure

---

#### 3. StrategyAgent (Edit Plan Synthesizer)
**Role:** Create retention-optimized edit plan from intent + analysis

**Configuration:**
- Temperature: 0.0 (fully deterministic)
- Max tokens: 2000
- Input validation: UserIntent + VideoAnalysis instances required

**Edit Plan Rules:**
- Cut silence >1s
- Speed up low-energy 1.2-1.5x based on preference
- Preserve high-energy segments
- Normalize audio if requested
- Quality score: 70-100

**Validation:**
- All `start_time` < `end_time`
- All times within video duration
- Speed factor: 0 < factor ≤ 3
- Cut actions have `remove` parameter
- Speed actions have `factor` parameter
- Audio actions have `normalize` or `volume` parameter

**Fallback:** Creates basic silence-removal plan if LLM fails

**Failure Mode:** Raises ValueError on schema violations

---

#### 4. QCAgent (Quality Control)
**Role:** Comprehensive validation and correction to prevent over-editing

**Configuration:**
- Temperature: 0.0 (fully deterministic)
- Max tokens: 2000

**Validation Checks:**

1. **Timeline Validity**
   - No negative start times
   - No times exceeding video duration
   - start_time < end_time for all edits

2. **Timeline Overlaps** (CRITICAL)
   - No overlapping cut segments
   - Checks all cut pairs for overlap

3. **Edit Spacing**
   - Min 0.5s spacing between consecutive edits
   - Warning if violated

4. **Cut Percentage**
   - Max 40% cuts (default)
   - Max 50% for "aggressive" preference
   - Max 30% for "conservative" preference
   - Warning if exceeded

5. **Speed Factors**
   - Max 2x speed (default)
   - Must be > 0
   - CRITICAL error if > 3x

6. **Negative Durations** (CRITICAL)
   - All edit durations must be > 0
   - Warning if < 0.1s (too short)

**Error Handling:**
- CRITICAL errors → Fail immediately (ValueError)
- Non-critical errors → Attempt LLM-based fix
- If LLM fix fails → Return original plan with warnings

**Failure Mode:** Raises ValueError on critical validation failures

---

### Agent Chain Behavior

**Strict Fail-Fast Mode:**
1. If any agent fails validation → Stop chain immediately
2. No partial results returned
3. Job marked as FAILED
4. Structured error response with:
   - `error`: Error type
   - `message`: Error description
   - `failed_agent`: Which agent failed
   - `job_id`: Job identifier

**Success Path:**
```
CommandAgent → AnalysisAgent → StrategyAgent → QCAgent → Success
```

**Failure Path:**
```
Any Agent Fails → Chain Stops → Job FAILED → Structured Error
```

---

## Error Responses

### Validation Error (422)
```json
{
  "error": "validation_error",
  "message": "start_time >= end_time: 10.5 >= 10.5",
  "failed_agent": "StrategyAgent",
  "job_id": "uuid"
}
```

### Processing Error (500)
```json
{
  "error": "processing_error",
  "message": "Video file not found",
  "failed_agent": "AnalysisAgent",
  "job_id": "uuid"
}
```

### Job Not Found (404)
```json
{
  "error": "job_not_found",
  "message": "Job not found"
}
```

### Already Processing (400)
```json
{
  "error": "already_processing",
  "message": "Job already processing"
}
```

---

## Edit Plan Schema

### Validated EditAction
```json
{
  "action": "cut" | "speed" | "audio",
  "start_time": 10.5,  // >= 0, < end_time
  "end_time": 15.2,    // > start_time, <= video_duration
  "parameters": {
    // Cut: {"remove": true}
    // Speed: {"factor": 1.3}  // 0 < factor <= 3
    // Audio: {"normalize": true} or {"volume": 0.8}
  },
  "reason": "5-200 char explanation"
}
```

### Validated EditPlan
```json
{
  "edits": [/* EditAction array */],
  "estimated_duration": 95.3,  // >= 0
  "optimization_summary": "10-500 char summary",
  "quality_score": 85,  // 70-100
  "validation_notes": [
    "QC: Fixed 2 issues",
    "Edit spacing violation corrected"
  ]
}
```

---

## Production Guarantees

### Determinism
- All agents use `temperature=0.0`
- Fixed `max_tokens` for each agent
- No randomness in validation logic
- Same input → Same output (given same LLM)

### Validation
- Pydantic schema validation on all inputs/outputs
- Custom validators for complex rules
- Timeline overlap detection
- Duration bounds checking
- Parameter type checking

### Error Handling
- Structured JSON errors only
- No stack traces in responses
- Failed agent identification
- Clear error messages

### Safety
- Fail-fast on critical errors
- No partial results
- Job state always consistent
- Comprehensive logging

---

## Agent Configuration

### Thresholds (QCAgent)
```python
MAX_CUT_PERCENTAGE = 0.40  # 40% max cuts
MAX_SPEED_FACTOR = 2.0     # 2x max speed
MIN_SPACING = 0.5          # 0.5s min spacing
```

### Adjustable by Intent
- Aggressive: 50% max cuts
- Moderate: 40% max cuts
- Conservative: 30% max cuts

---

## Usage Example

### Success Case
```bash
curl -X POST http://localhost:8001/api/video/process/{job_id} \
  -H "Content-Type: application/json" \
  -d '{"command_text": "Cut silence and speed up boring parts"}'
```

Response:
```json
{
  "status": "success",
  "user_intent": {
    "intent": "Remove silence and increase pacing",
    "target_style": "energetic",
    "cut_preference": "aggressive"
  },
  "edit_plan": {
    "edits": [...],
    "quality_score": 90,
    "validation_notes": ["QC: Plan validated successfully"]
  }
}
```

### Validation Failure Case
```bash
# Command too short
curl -X POST http://localhost:8001/api/video/process/{job_id} \
  -d '{"command_text": "cut"}'
```

Response (422):
```json
{
  "error": "validation_error",
  "message": "Command text too short (min 5 chars)",
  "failed_agent": "CommandAgent",
  "job_id": "uuid"
}
```

---

## Logging

All agents log key events:
```
INFO: CommandAgent: Processing command (length=45)
INFO: CommandAgent: Intent parsed successfully (style=energetic)
INFO: AnalysisAgent: Starting analysis for video.mp4
INFO: AnalysisAgent: Video duration=120s, analyzing audio...
INFO: AnalysisAgent: Analysis complete (25 segments)
INFO: StrategyAgent: Creating edit plan (style=energetic, segments=25)
INFO: StrategyAgent: Edit plan created (8 edits, score=90)
INFO: QCAgent: Validating plan (8 edits, duration=120s)
INFO: QCAgent: Plan validated successfully (score=90)
```

Errors are logged with details:
```
ERROR: CommandAgent: JSON parse error: Expecting value
WARNING: StrategyAgent: Using fallback plan
ERROR: QCAgent: LLM validation failed: Timeline overlap
```

---

## Testing Agents

### Test Command Intent Parsing
```bash
# Minimal command
curl ... -d '{"command_text": "cut silence"}'

# Complex command  
curl ... -d '{"command_text": "Make this super engaging - cut all silence longer than 2 seconds, speed up boring parts by 1.5x, and normalize audio for consistent volume"}'
```

### Test Validation
```bash
# Normal video (should pass)
curl ... # 10s test video

# Long video (should pass but warn if >2h)
curl ... # Upload 3h video → Error: Video too long
```

### Test Error Handling
```bash
# Invalid job ID
curl .../invalid-id → 404

# Already processing
curl .../same-job-twice → 400
```

---

## Timeline Compiler Usage

### Compile Edit Plan to FFmpeg Commands

**Prerequisite:** Job must be in COMPLETED status with valid edit plan

```bash
# 1. First, process video to get edit plan
curl -X POST http://localhost:8001/api/video/process/{job_id} \
  -H "Content-Type: application/json" \
  -d '{"command_text": "Cut silence and speed up boring parts"}'

# 2. Then compile edit plan to ffmpeg commands
curl -X POST http://localhost:8001/api/video/compile/{job_id}
```

**Response:**
```json
{
  "status": "compiled",
  "input_edit_plan": {
    "edits": [
      {
        "action": "cut",
        "start_time": 3.5,
        "end_time": 8.2,
        "parameters": {"remove": true},
        "reason": "Remove silence"
      }
    ],
    "estimated_duration": 95.3,
    "quality_score": 90
  },
  "generated_ffmpeg_commands": [
    {
      "command_type": "cut",
      "command": "ffmpeg -i /tmp/video_uploads/video.mp4 -ss 0.000 -t 3.500 -c copy -avoid_negative_ts 1 /tmp/video_edits/video_segment_000.mp4",
      "edit_index": 0,
      "input_file": "/tmp/video_uploads/video.mp4",
      "output_file": "/tmp/video_edits/video_segment_000.mp4",
      "description": "Extract segment 0: 0.00s-3.50s (3.50s)"
    },
    {
      "command_type": "cut",
      "command": "ffmpeg -i /tmp/video_uploads/video.mp4 -ss 8.200 -t 111.800 -c copy -avoid_negative_ts 1 /tmp/video_edits/video_segment_001.mp4",
      "edit_index": 1,
      "input_file": "/tmp/video_uploads/video.mp4",
      "output_file": "/tmp/video_edits/video_segment_001.mp4",
      "description": "Extract segment 1: 8.20s-120.00s (111.80s)"
    },
    {
      "command_type": "merge",
      "command": "ffmpeg -f concat -safe 0 -i /tmp/video_edits/video_concat_list.txt -c copy /tmp/video_edits/video_merged.mp4",
      "input_file": "/tmp/video_edits/video_concat_list.txt",
      "output_file": "/tmp/video_edits/video_merged.mp4",
      "description": "Merge 2 segments into final video"
    }
  ],
  "total_commands": 3,
  "estimated_steps": 3,
  "warnings": [
    "Requires concat list file: /tmp/video_edits/video_concat_list.txt",
    "Concat list content:\nfile 'video_segment_000.mp4'\nfile 'video_segment_001.mp4'"
  ],
  "metadata": {
    "cuts": 1,
    "speed_changes": 0,
    "audio_adjustments": 0,
    "requires_merge": true
  }
}
```

### Generated Command Types

#### 1. Cut/Trim Commands
```bash
ffmpeg -i input.mp4 -ss 10.5 -t 5.2 -c copy -avoid_negative_ts 1 output.mp4
```
- `-ss`: Start time
- `-t`: Duration
- `-c copy`: Stream copy (fast, no re-encode)

#### 2. Speed Commands
```bash
ffmpeg -i input.mp4 -ss 0 -t 10 \
  -filter_complex "[0:v]setpts=0.667*PTS[v];[0:a]atempo=1.500[a]" \
  -map "[v]" -map "[a]" output.mp4
```
- `setpts`: Video speed (1/factor)
- `atempo`: Audio speed (factor)
- Requires re-encoding

#### 3. Audio Commands
```bash
ffmpeg -i input.mp4 -ss 0 -t 120 \
  -af "loudnorm=I=-16:TP=-1.5:LRA=11,volume=0.80" \
  -c:v copy output.mp4
```
- `loudnorm`: Loudness normalization
- `volume`: Volume adjustment

#### 4. Merge/Concat Commands
```bash
ffmpeg -f concat -safe 0 -i concat_list.txt -c copy output.mp4
```
- Requires concat list file
- Fast, no re-encoding

### Compilation Errors

**Job Not Completed:**
```json
{
  "error": "job_not_completed",
  "message": "Job must be COMPLETED (current: processing)"
}
```

**No Edit Plan:**
```json
{
  "error": "no_edit_plan",
  "message": "Job has no edit plan to compile"
}
```

**Compilation Error:**
```json
{
  "error": "compilation_error",
  "message": "Video file not found: /path/to/video.mp4"
}
```

### Command Validation

Timeline compiler validates generated commands for:
- ✅ Command starts with `ffmpeg`
- ✅ Input file specified (`-i`)
- ✅ Output file present
- ✅ Command type matches content (cut has `-ss`, speed has `setpts`, etc.)

Validation warnings added to response if issues found.

### Important Notes

1. **No Execution:** Commands are only generated, never executed
2. **Temp Paths:** Output paths use `/tmp/video_edits/` (configurable)
3. **Concat Lists:** Merge commands require separate concat list files
4. **Re-encoding:** Speed and some audio operations require re-encoding (slow)
5. **Copy Mode:** Cut and merge use `-c copy` (fast, no quality loss)

### Full Workflow Example

```bash
# 1. Upload video
UPLOAD=$(curl -s -X POST http://localhost:8001/api/video/upload \
  -F "file=@video.mp4")
JOB_ID=$(echo $UPLOAD | jq -r '.job_id')

# 2. Process with agents
curl -X POST http://localhost:8001/api/video/process/$JOB_ID \
  -H "Content-Type: application/json" \
  -d '{"command_text": "Make it engaging - cut silence and speed up boring parts"}'

# 3. Compile to ffmpeg commands
COMPILE=$(curl -s -X POST http://localhost:8001/api/video/compile/$JOB_ID)

# 4. Extract commands
echo $COMPILE | jq '.generated_ffmpeg_commands[].command'

# Output:
# "ffmpeg -i /tmp/video_uploads/abc.mp4 -ss 0.000 -t 10.500 ..."
# "ffmpeg -i /tmp/video_uploads/abc.mp4 -ss 15.000 -t 105.000 ..."
# "ffmpeg -f concat -safe 0 -i /tmp/video_edits/abc_concat_list.txt ..."
```

---

## Test Timeline Compiler

### Test Cut Operations
```bash
curl ... -d '{"command_text": "Cut all silence"}'
curl ... /compile/{job_id}
# Expect: Multiple cut commands + merge command
```

### Test Speed Operations
```bash
curl ... -d '{"command_text": "Speed up boring parts by 1.5x"}'
curl ... /compile/{job_id}
# Expect: Speed commands with setpts/atempo filters
```

### Test Audio Operations
```bash
curl ... -d '{"command_text": "Normalize audio levels"}'
curl ... /compile/{job_id}
# Expect: Audio commands with loudnorm filter
```

### Test Combined Operations
```bash
curl ... -d '{"command_text": "Cut silence, speed up low-energy, normalize audio"}'
curl ... /compile/{job_id}
# Expect: Mix of cut, speed, and audio commands

---

## Cost Guard Usage

### Estimate Cost Before Processing

```bash
curl -X POST http://localhost:8001/api/video/estimate-cost \
  -F "command_text=Cut all silence and speed up boring parts by 1.5x" \
  -F "estimated_segments=25" \
  -F "estimated_edits=8"
```

**Response:**
```json
{
  "estimated_tokens": 3650,
  "max_tokens_per_request": 10000,
  "utilization_percentage": 36.5,
  "estimated_cost_usd": 0.073,
  "breakdown": {
    "CommandAgent": 425,
    "AnalysisAgent": 0,
    "StrategyAgent": 1750,
    "QCAgent": 1475
  },
  "guard_enabled": true,
  "would_pass_limits": true
}
```

### Cost Estimation Examples

#### Example 1: Short Command (Passes)
```bash
curl -F "command_text=Remove silence"
```
**Result:**
```json
{
  "estimated_tokens": 2800,
  "would_pass_limits": true,
  "utilization_percentage": 28.0
}
```

#### Example 2: Long Video (Warning)
```bash
curl -F "command_text=Cut silence..." \
     -F "estimated_segments=100"
```
**Result:**
```json
{
  "estimated_tokens": 7500,
  "would_pass_limits": true,
  "utilization_percentage": 75.0
}
```

#### Example 3: Excessive (Fails)
```bash
curl -F "command_text=Complex editing..." \
     -F "estimated_segments=200"
```
**Result:**
```json
{
  "estimated_tokens": 15000,
  "would_pass_limits": false,
  "limit_error": {
    "error": "token_limit_exceeded",
    "message": "Estimated tokens (15000) exceed request limit (10000)",
    "estimated_tokens": 15000,
    "limit": 10000
  }
}
```

### Cost Guard in Processing

When calling `/process`, cost is automatically checked:

```bash
# This will succeed (estimated < 10000 tokens)
curl -X POST http://localhost:8001/api/video/process/{job_id} \
  -H "Content-Type: application/json" \
  -d '{"command_text": "Cut silence"}'
```

**Success:** Proceeds with agent chain

```bash
# This will fail if estimated > 10000 tokens
curl -X POST http://localhost:8001/api/video/process/{job_id} \
  -d '{"command_text": "Very complex command with 200+ segments..."}'
```

**Failure (429):**
```json
{
  "error": "token_limit_exceeded",
  "message": "Estimated tokens (12000) exceed request limit (10000)",
  "estimated_tokens": 12000,
  "limit": 10000,
  "breakdown": {
    "CommandAgent": 500,
    "StrategyAgent": 6000,
    "QCAgent": 5500
  }
}
```

### Configuration

**Enable/Disable Cost Guard:**
```bash
# In .env
COST_CHECK_ENABLED=false  # Disable for testing
```

**Adjust Limits:**
```bash
# In .env
MAX_TOKENS_PER_REQUEST=20000  # Increase for larger videos
MAX_TOKENS_PER_AGENT=8000     # Increase for complex analysis
```

**Conservative Estimates:**

When actual video analysis isn't available yet, Cost Guard uses conservative estimates:
- Segments: 30 (instead of actual count)
- Edits: 10 (instead of actual count)

After video analysis, it re-checks with real values.

### Cost Calculation

**Token-to-Cost Estimation:**
- Blended rate: ~$0.02 per 1K tokens
- Example: 5000 tokens ≈ $0.10 USD

**Breakdown:**
```
CommandAgent:   400 tokens  → $0.008
AnalysisAgent:  0 tokens    → $0.000 (no LLM)
StrategyAgent:  2000 tokens → $0.040
QCAgent:        1800 tokens → $0.036
---
Total:          4200 tokens → $0.084
```

### Benefits

1. **Budget Control:** Know costs before execution
2. **Fail-Fast:** Reject expensive requests early (no wasted LLM calls)
3. **Transparency:** Clear token breakdown per agent
4. **Configurable:** Adjust limits based on budget
5. **Safety:** Prevents runaway costs

### Monitoring

Cost Guard logs all checks:
```
INFO: CostGuard: Estimated tokens: {'CommandAgent': 400, 'StrategyAgent': 2000, 'QCAgent': 1800, 'total': 4200}
INFO: CostGuard: Checks passed (total=4200 tokens)
```

Failed checks:
```
WARNING: CostGuard: Request limit exceeded: {'error': 'token_limit_exceeded', ...}
```

---

## Voice DSL Usage

### Parse Natural Language to DSL

```bash
curl -X POST http://localhost:8001/api/video/parse-dsl \
  -F "text=Cut all silence longer than 2 seconds and speed up boring parts by 1.5x"
```

**Response:**
```json
{
  "version": "1.0",
  "confidence": 0.9,
  "actions": [
    {
      "type": "cut",
      "target": "silence",
      "parameters": {
        "threshold_seconds": 2.0
      },
      "confidence": 0.9
    },
    {
      "type": "speed",
      "target": "low-energy",
      "parameters": {
        "factor": 1.5,
        "adaptive": true
      },
      "confidence": 0.85
    }
  ],
  "metadata": {
    "original_text": "Cut all silence longer than 2 seconds and speed up boring parts by 1.5x",
    "normalized_text": "cut silence 2 seconds speed boring 1.5x",
    "detected_keywords": ["cut", "silence", "speed", "boring"],
    "action_count": 2
  }
}
```

### Example DSL Inputs & Outputs

#### Example 1: Simple Cut
**Input:**
```
"Remove all silence"
```

**Output:**
```json
{
  "confidence": 0.75,
  "actions": [
    {
      "type": "cut",
      "target": "silence",
      "parameters": {"threshold_seconds": 1.0},
      "confidence": 0.9
    }
  ]
}
```

#### Example 2: Speed & Audio
**Input:**
```
"Make it faster and normalize the audio"
```

**Output:**
```json
{
  "confidence": 0.8,
  "actions": [
    {
      "type": "speed",
      "target": "all",
      "parameters": {"factor": 1.5, "adaptive": true},
      "confidence": 0.75
    },
    {
      "type": "audio",
      "target": "all",
      "parameters": {"normalize": true},
      "confidence": 0.8
    }
  ]
}
```

#### Example 3: Complex Command
**Input:**
```
"Cut silence over 1.5 seconds, speed up low-energy sections by 2x, and increase volume by 20%"
```

**Output:**
```json
{
  "confidence": 0.95,
  "actions": [
    {
      "type": "cut",
      "target": "silence",
      "parameters": {"threshold_seconds": 1.5},
      "confidence": 0.9
    },
    {
      "type": "speed",
      "target": "low-energy",
      "parameters": {"factor": 2.0, "adaptive": true},
      "confidence": 0.85
    },
    {
      "type": "audio",
      "target": "all",
      "parameters": {"volume": 1.2},
      "confidence": 0.8
    }
  ]
}
```

#### Example 4: Preservation
**Input:**
```
"Keep only the exciting parts and remove everything else"
```

**Output:**
```json
{
  "confidence": 0.7,
  "actions": [
    {
      "type": "keep",
      "target": "high-energy",
      "parameters": {},
      "confidence": 0.7
    }
  ]
}
```

### DSL Integration Points

1. **Standalone Parsing:**
   ```bash
   # Just parse text to DSL
   curl .../parse-dsl -F "text=..."
   ```

2. **Preprocessing for Agents:**
   ```python
   # Convert DSL to UserIntent
   dsl = await voice_dsl_parser.parse(text)
   user_intent = voice_dsl_parser.dsl_to_user_intent(dsl)
   # Pass to CommandAgent
   ```

3. **Direct Strategy Input:**
   ```python
   # DSL actions → Edit plan
   # (future enhancement)
   ```

### Confidence Scoring

Confidence based on:
- **Keyword matches:** More keywords = higher confidence
- **Specificity:** Exact numbers (2x, 1.5s) boost confidence
- **Text length:** Very short text reduces confidence
- **Ambiguity:** Vague terms reduce confidence

Confidence ranges:
- **0.9-1.0:** High confidence, specific command
- **0.7-0.9:** Good confidence, clear intent
- **0.5-0.7:** Moderate confidence, some ambiguity
- **0.3-0.5:** Low confidence, safe defaults used
- **<0.3:** Error, text too ambiguous

### Supported Keywords

**Cut Actions:**
- cut, remove, delete, trim, eliminate, drop, skip

**Speed Actions:**
- speed, fast, faster, quick, accelerate, pace, tempo

**Audio Actions:**
- audio, sound, volume, normalize, loud, quiet, mute

**Keep Actions:**
- keep, preserve, save, maintain, retain

**Targets:**
- silence, silent, quiet, pause, gap
- boring, slow, dull, low-energy
- energetic, exciting, high-energy, active

### Parameter Extraction

**Time durations:**
- "2 seconds" → 2.0
- "1.5s" → 1.5
- "longer than 3" → 3.0

**Speed factors:**
- "1.5x" → 1.5
- "2x faster" → 2.0
- "very fast" → 2.0
- "faster" → 1.5

**Volume levels:**
- "20% louder" → 1.2
- "volume 0.8" → 0.8
- "louder" → 1.2 (default)
- "quieter" → 0.8 (default)

### Error Handling

**Text Too Short:**
```json
{
  "error": "parse_error",
  "message": "Text too short for parsing"
}
```

**Low Confidence:**
```json
{
  "confidence": 0.25  // < 0.3 → Error
}
```

### Full Workflow with DSL

```bash
# 1. Parse voice command to DSL
DSL=$(curl -s -X POST http://localhost:8001/api/video/parse-dsl \
  -F "text=Cut silence and speed up boring parts")

echo $DSL | jq '{confidence, actions: [.actions[].type]}'
# Output: {"confidence": 0.85, "actions": ["cut", "speed"]}

# 2. Use parsed intent for video processing
# (DSL can inform CommandAgent or be used directly)

# 3. Process video with natural language
curl ... /process/{job_id} \
  -d '{"command_text": "Cut silence and speed up boring parts"}'

# 4. Compile to ffmpeg
curl ... /compile/{job_id}
```

---

## Differences from v2.0

| Feature | v2.0 | v2.3 (Current) |
|---------|------|-----------------|
| Agent determinism | Partial | Full (temp=0) |
| Input validation | Basic | Comprehensive |
| Schema validation | Pydantic only | Pydantic + custom |
| Error responses | String | Structured JSON |
| Timeline overlap check | No | Yes (QC) |
| Negative duration check | No | Yes (QC) |
| Edit spacing check | No | Yes (QC) |
| Cut percentage check | No | Yes (QC, adaptive) |
| Speed factor limits | No | Yes (QC, max 2-3x) |
| Fail-fast mode | No | Yes (chain stops) |
| Logging | Minimal | Comprehensive |
| Fallback behavior | Silent | Logged + noted |

---

## Limitations

- **Not production-ready for scale:** In-memory storage
- **No authentication:** Open API
- **No rate limiting:** Can be overwhelmed
- **No video rendering:** Edit plans only
- **No batch processing:** One video at a time per job
- **LLM dependency:** Requires OpenAI API access

---

## Next Steps for True Production

1. **Add Redis/database** for job storage
2. **Add authentication** (API keys, JWT)
3. **Add rate limiting** (per user/IP)
4. **Add monitoring** (Prometheus, Sentry)
5. **Add video rendering** from edit plans
6. **Add batch processing** queue
7. **Add webhooks** for async notifications
8. **Add retries** with exponential backoff
9. **Add caching** for repeated analysis
10. **Add metrics** (success rate, latency)

---

Built on Emergent platform | Version 2.4 (Agents + Compiler + DSL + Cost Guard)

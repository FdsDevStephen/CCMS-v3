# CcmsApi

.NET 10 Web API: upload documents → OCR on the hosted IndicOCR server → the hosted Qwen3 14B
LLM does the task configured in `appsettings.json` with the text → its answer as JSON fields.

## Run

Open `CcmsApi.slnx` in Visual Studio 2026, and press
**Ctrl+F5** (F5 also works, but Visual Studio stops the API when the browser window it opened is
closed). Or:

```
cd CcmsApi
"C:\Program Files\dotnet\dotnet.exe" run --launch-profile https
```

- https://localhost:7421/ — test page (upload files)
- https://localhost:7421/swagger — Swagger UI
- `CcmsApi/CcmsApi.http` — test requests for Visual Studio

Plain `http://localhost:5421` redirects to HTTPS. Uploads may be up to 100 MB.

## Endpoints

| Endpoint | Does |
| --- | --- |
| `POST /api/documents/process` | multipart `files` (one or more PDF/image files, read as one document), optional `includeText=true` → `{"task", "files", "fields": {...}, "text_chars", "truncated"[, "text"]}` |
| `GET /health` | 200 if both servers are up, else 503 with the reason (a 5xx answer or no answer counts as down) |

400 = bad upload, 502 = the OCR or LLM server failed. OCR takes ~30-45 s per page, so a call
can take several minutes (a 2-page order took ~2 minutes).

## Configuration (`CcmsApi/appsettings.json`)

- `Ocr` — IndicOCR server. PDFs longer than `ChunkPages` are sent in page ranges; `Parallel`
  ranges at a time. Retries 10/30/60 s on 5xx or connection errors, not on 504 or timeout.
- `Llm` — Qwen3 14B `generate_greedy` (same prompt → same answer). Up to `Attempts` tries.
  Text longer than `MaxInputChars` is cut, and the response says `"truncated": true`.
- `Task` — what the LLM does:
  - `Name` — shown in the response.
  - `Instructions` — the system prompt.
  - `Fields` — the JSON keys of the answer. `Description` tells the LLM what goes in the field.
    The optional `Enum` lists the only values allowed; any other value is retried as an error.

**The shipped `Task` ("Document summary (example)") is only an example.** Replace it with the
real task. The API will not start unless `Instructions` is filled in, there is at least one field,
and the field names are unique.

Note: in `appsettings.Development.json`, array entries are merged with `appsettings.json` by
position (`Fields:0`, `Fields:1`, …), not replaced. Change the task in `appsettings.json`.

## Structure (ASP.NET Core Web API)

```
CcmsApi.slnx
CcmsApi/
├── Controllers/
│   ├── DocumentsController.cs   POST /api/documents/process
│   └── HealthController.cs      GET /health
├── Services/
│   ├── Interfaces.cs            IOcrService, ILlmService (+ Field), IServerHealth
│   ├── DocumentService.cs       OCR → prompt from the Task settings → LLM → fields
│   ├── IndicOcrClient.cs        IOcrService: page chunking, retries, cleaning the markdown
│   ├── QwenClient.cs            ILlmService: prompt format, JSON cut out and checked, retries
│   └── ServerHealth.cs          IServerHealth: is each server up?
├── Models/                      DocumentUploadRequest, DocumentResult, HealthReport
├── Options/                     OcrOptions, LlmOptions, TaskOptions (+ startup validation)
├── Common/                      Uploads (file checks), Text, UpstreamException, InvalidUploadException
├── Middleware/
│   └── ApiExceptionHandler.cs   InvalidUploadException → 400, UpstreamException → 502
├── Program.cs                   settings, services, controllers, Swagger
├── appsettings.json
├── Properties/launchSettings.json
└── wwwroot/index.html           test page
```

A request goes Controller → `DocumentService` → `IOcrService` (IndicOcrClient), then
`ILlmService` (QwenClient). Everything is registered in `Program.cs`.

The OCR and LLM clients come from `CeG-Gemini-OCR/GrievanceApi`, where they were tested
against the real servers. Their logic is unchanged.

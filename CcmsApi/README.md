# CcmsApi

ASP.NET Web API 2 on .NET Framework 4.8: upload documents → OCR on the hosted IndicOCR server → the
hosted Qwen3 14B LLM does the task configured in `App_Data/appsettings.json` with the text → its
answer as JSON fields. Runs in IIS (or IIS Express from Visual Studio).

## Run

Open `CcmsApi.slnx` in Visual Studio 2026 (workload "ASP.NET and web development", .NET Framework
4.8 targeting pack), and press **Ctrl+F5**. The NuGet packages are restored on the first build.
IIS Express starts the API:

- https://localhost:44321/ — test page (upload files)
- https://localhost:44321/swagger — Swagger UI
- `CcmsApi/CcmsApi.http` — test requests for Visual Studio

Uploads may be up to 100 MB. Build from the command line:

```
"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" CcmsApi.slnx -restore -p:RestorePackagesConfig=true -p:Configuration=Release
```

## Deploy to IIS

1. Publish (Visual Studio: right-click the project → Publish → Folder), or copy the project folder
   after a Release build: `bin\`, `App_Data\`, `Global.asax`, `Web.config`, `index.html`.
2. In IIS Manager, add a website or application pointing at that folder, with an application pool on
   **.NET CLR v4.0, Integrated** pipeline.
3. In `Web.config`, set `<compilation debug="false" …>`: only then is `executionTimeout` (1 hour)
   enforced. If the site is behind a proxy or load balancer, raise its timeout too; a call can take
   several minutes.

The test page and its links are relative, so the API also works as an application under a path
(e.g. `http://server/CcmsApi/`).

## Endpoints

| Endpoint | Does |
| --- | --- |
| `POST /api/documents/process` | multipart `files` (one or more PDF/image files, read as one document), optional `includeText=true` → `{"task", "files", "fields": {...}, "text_chars", "truncated"[, "text"]}` |
| `GET /health` | 200 if both servers are up, else 503 with the reason (a 5xx answer or no answer counts as down) |

400 = bad upload, 502 = the OCR or LLM server failed (`application/problem+json`). OCR takes ~30-45 s
per page, so a call can take several minutes (a 2-page order took ~2 minutes).

## Configuration (`CcmsApi/App_Data/appsettings.json`)

IIS never serves files from `App_Data`, so the settings cannot be downloaded. The file is read when
the site starts: after changing it, restart the site (or save `Web.config` again).

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
and the field names are unique; every request then answers 500 with the reason.

## Structure (ASP.NET Web API 2, .NET Framework 4.8)

```
CcmsApi.slnx
CcmsApi/
├── App_Start/
│   ├── WebApiConfig.cs          settings, services, routes, JSON, exception filter
│   └── SwaggerConfig.cs         Swagger (Swashbuckle 5) + the upload form for Swagger
├── Controllers/
│   ├── DocumentsController.cs   POST /api/documents/process (reads the multipart form)
│   └── HealthController.cs      GET /health
├── Services/
│   ├── Interfaces.cs            IOcrService, ILlmService (+ Field), IServerHealth
│   ├── DocumentService.cs       OCR → prompt from the Task settings → LLM → fields
│   ├── IndicOcrClient.cs        IOcrService: page chunking, retries, cleaning the markdown
│   ├── QwenClient.cs            ILlmService: prompt format, JSON cut out and checked, retries
│   └── ServerHealth.cs          IServerHealth: is each server up?
├── Models/                      UploadedFile, DocumentResult, HealthReport
├── Options/                     AppSettings (loads + checks the file), OcrOptions, LlmOptions, TaskOptions
├── Common/                      ServiceResolver (builds the controllers), Uploads, Text, Json, exceptions
├── Filters/
│   └── ApiExceptionFilter.cs    InvalidUploadException → 400, UpstreamException → 502
├── App_Data/appsettings.json
├── Global.asax(.cs)             starts Web API; reports a startup error on every request
├── Web.config                   upload size, timeouts, IIS handlers, binding redirects
├── packages.config              NuGet: Web API 5.2.9, Newtonsoft.Json, Swashbuckle.Core, PdfSharp
└── index.html                   test page
```

A request goes Controller → `DocumentService` → `IOcrService` (IndicOcrClient), then
`ILlmService` (QwenClient). The services are created in `Common/ServiceResolver.cs`.

The OCR and LLM clients come from `CeG-Gemini-OCR/GrievanceApi`, where they were tested
against the real servers. Their logic is unchanged.

PdfSharp 1.50 (for page counts) cannot open a few PDFs; those are sent to the OCR server whole
instead of in page ranges, which only matters for long ones.

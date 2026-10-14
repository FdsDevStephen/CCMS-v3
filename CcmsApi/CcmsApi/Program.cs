using CcmsApi.Middleware;
using CcmsApi.Options;
using CcmsApi.Services;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.Extensions.Options;
using Microsoft.OpenApi;

// CCMS Document API: OCR uploaded documents on the IndicOCR server, then have the Qwen3 14B server
// do the task configured in appsettings.json ("Task") with the text, and return its fields.
//
//   GET  /health                  Controllers/HealthController.cs
//   POST /api/documents/process   Controllers/DocumentsController.cs
//
// Test page: /     Swagger: /swagger

const long MaxUploadBytes = 100 * 1024 * 1024;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(kestrel => kestrel.Limits.MaxRequestBodySize = MaxUploadBytes);
builder.Services.Configure<FormOptions>(form => form.MultipartBodyLengthLimit = MaxUploadBytes);

// Add services to the container.

// Settings (appsettings.json); the Task section is checked at startup.
builder.Services.Configure<OcrOptions>(builder.Configuration.GetSection("Ocr"));
builder.Services.Configure<LlmOptions>(builder.Configuration.GetSection("Llm"));
builder.Services.AddOptions<TaskOptions>().Bind(builder.Configuration.GetSection("Task")).ValidateOnStart();
builder.Services.AddSingleton<IValidateOptions<TaskOptions>, TaskOptionsValidator>();

// The OCR and LLM servers, and the service that uses them.
builder.Services.AddHttpClient<IOcrService, IndicOcrClient>()
    .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(30) });
builder.Services.AddHttpClient<ILlmService, QwenClient>()
    .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(30) });
builder.Services.AddHttpClient(ServerHealth.ClientName, http => http.Timeout = TimeSpan.FromSeconds(5));
builder.Services.AddTransient<IServerHealth, ServerHealth>();
builder.Services.AddTransient<DocumentService>();

builder.Services.AddControllers();
builder.Services.AddProblemDetails();
builder.Services.AddExceptionHandler<ApiExceptionHandler>();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(swagger => swagger.SwaggerDoc("v1", new OpenApiInfo
{
    Title = "CCMS Document API",
    Version = "v1",
    Description = "Upload documents; the API OCRs them and has the LLM fill in the fields of the configured task "
        + "(set in appsettings.json). OCR takes ~30-45 s per page, so a call can take several minutes.",
}));

var app = builder.Build();

// Configure the HTTP request pipeline.
app.UseExceptionHandler();
app.UseHttpsRedirection(); // http:// requests are sent on to https:// (when the app listens on https)
app.UseSwagger();
app.UseSwaggerUI(ui => ui.DocumentTitle = "CCMS Document API");
app.UseDefaultFiles(); // "/" -> wwwroot/index.html, the upload test page
app.UseStaticFiles();
app.MapControllers();

app.Run();

using CcmsApi.Services;
using Microsoft.AspNetCore.Mvc;

namespace CcmsApi.Controllers;

[ApiController]
[Route("health")]
public sealed class HealthController(IServerHealth health) : ControllerBase
{
    [HttpGet]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status503ServiceUnavailable)]
    [EndpointSummary("Is the API working, and can it reach the OCR and LLM servers?")]
    public async Task<IActionResult> Get(CancellationToken ct)
    {
        var report = await health.CheckAsync(ct);
        return StatusCode(
            report.Healthy ? StatusCodes.Status200OK : StatusCodes.Status503ServiceUnavailable,
            new { status = report.Healthy ? "Healthy" : "Unhealthy", api = "Up", ocrServer = report.OcrServer, llmServer = report.LlmServer });
    }
}

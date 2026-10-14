using CcmsApi.Common;
using Microsoft.AspNetCore.Diagnostics;
using Microsoft.AspNetCore.Mvc;

namespace CcmsApi.Middleware;

/// <summary>Turns the services' exceptions into HTTP answers: bad upload 400, OCR / LLM server failed 502.</summary>
public sealed class ApiExceptionHandler(IProblemDetailsService problems) : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(HttpContext http, Exception exception, CancellationToken ct)
    {
        switch (exception)
        {
            case InvalidUploadException invalid:
                http.Response.StatusCode = StatusCodes.Status400BadRequest;
                await http.Response.WriteAsJsonAsync(new { error = invalid.Message }, ct);
                return true;
            case UpstreamException upstream:
                http.Response.StatusCode = StatusCodes.Status502BadGateway;
                return await problems.TryWriteAsync(new ProblemDetailsContext
                {
                    HttpContext = http,
                    Exception = exception,
                    ProblemDetails = new ProblemDetails
                    {
                        Status = StatusCodes.Status502BadGateway,
                        Title = "The OCR or LLM server failed",
                        Detail = upstream.Message,
                    },
                });
            default:
                return false;
        }
    }
}

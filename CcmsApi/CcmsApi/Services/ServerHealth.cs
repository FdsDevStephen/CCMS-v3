using System.Diagnostics;
using CcmsApi.Models;
using CcmsApi.Options;
using Microsoft.Extensions.Options;

namespace CcmsApi.Services;

/// <summary>
/// These endpoints answer a GET with 405 when the server is up; a 5xx answer or no answer means it is down.
/// Nothing is OCR'd or generated, so the check takes well under a second.
/// </summary>
public sealed class ServerHealth(IHttpClientFactory factory, IOptions<OcrOptions> ocr, IOptions<LlmOptions> llm) : IServerHealth
{
    public const string ClientName = "health";

    public async Task<HealthReport> CheckAsync(CancellationToken ct)
    {
        var http = factory.CreateClient(ClientName);
        return new HealthReport(await Reachable(http, ocr.Value.Url, ct), await Reachable(http, llm.Value.Url, ct));
    }

    static async Task<ServerStatus> Reachable(HttpClient http, string url, CancellationToken ct)
    {
        var timer = Stopwatch.StartNew();
        try
        {
            using var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct);
            // The gateway answers 5xx (e.g. 503 "Service 'qwen13' is unreachable") when the server behind it is down.
            if ((int)response.StatusCode >= 500)
                return new ServerStatus(false, timer.ElapsedMilliseconds,
                    $"HTTP {(int)response.StatusCode}: {await response.Content.ReadAsStringAsync(ct)}");
            return new ServerStatus(true, timer.ElapsedMilliseconds, null);
        }
        catch (Exception exc) when (exc is HttpRequestException || exc is TaskCanceledException && !ct.IsCancellationRequested)
        {
            return new ServerStatus(false, timer.ElapsedMilliseconds, exc.Message);
        }
    }
}

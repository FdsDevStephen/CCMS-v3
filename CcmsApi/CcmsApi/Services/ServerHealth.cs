using System;
using System.Diagnostics;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using CcmsApi.Models;
using CcmsApi.Options;

namespace CcmsApi.Services
{
    /// <summary>
    /// These endpoints answer a GET with 405 when the server is up; a 5xx answer or no answer means it is down.
    /// Nothing is OCR'd or generated, so the check takes well under a second.
    /// </summary>
    public sealed class ServerHealth : IServerHealth
    {
        readonly HttpClient http; // 5 s timeout (Common/ServiceResolver.cs)
        readonly OcrOptions ocr;
        readonly LlmOptions llm;

        public ServerHealth(HttpClient http, OcrOptions ocr, LlmOptions llm)
        {
            this.http = http;
            this.ocr = ocr;
            this.llm = llm;
        }

        public async Task<HealthReport> CheckAsync(CancellationToken ct) =>
            new HealthReport(await Reachable(ocr.Url, ct), await Reachable(llm.Url, ct));

        async Task<ServerStatus> Reachable(string url, CancellationToken ct)
        {
            var timer = Stopwatch.StartNew();
            try
            {
                using (var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct))
                {
                    // The gateway answers 5xx (e.g. 503 "Service 'qwen13' is unreachable") when the server behind it is down.
                    if ((int)response.StatusCode >= 500)
                        return new ServerStatus(false, timer.ElapsedMilliseconds,
                            $"HTTP {(int)response.StatusCode}: {await response.Content.ReadAsStringAsync()}");
                    return new ServerStatus(true, timer.ElapsedMilliseconds, null);
                }
            }
            catch (Exception exc) when (exc is HttpRequestException || exc is TaskCanceledException && !ct.IsCancellationRequested)
            {
                var error = exc is TaskCanceledException ? "No answer within 5 s" : exc.InnerException?.Message ?? exc.Message;
                return new ServerStatus(false, timer.ElapsedMilliseconds, error);
            }
        }
    }
}

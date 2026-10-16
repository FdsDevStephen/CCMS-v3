using System.Net;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Http;
using CcmsApi.Services;

namespace CcmsApi.Controllers
{
    /// <summary>Is the API working, and can it reach the OCR and LLM servers? 200 if both are up, else 503.</summary>
    public sealed class HealthController : ApiController
    {
        readonly IServerHealth health;

        public HealthController(IServerHealth health)
        {
            this.health = health;
        }

        [HttpGet]
        [Route("health")]
        public async Task<IHttpActionResult> Get(CancellationToken ct)
        {
            var report = await health.CheckAsync(ct);
            return Content(
                report.Healthy ? HttpStatusCode.OK : HttpStatusCode.ServiceUnavailable,
                new
                {
                    status = report.Healthy ? "Healthy" : "Unhealthy",
                    api = "Up",
                    ocrServer = report.OcrServer,
                    llmServer = report.LlmServer,
                });
        }
    }
}

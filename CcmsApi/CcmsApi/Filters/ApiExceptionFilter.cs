using System.Net;
using System.Net.Http;
using System.Net.Http.Formatting;
using System.Web.Http.Filters;
using CcmsApi.Common;

namespace CcmsApi.Filters
{
    /// <summary>Turns the services' exceptions into HTTP answers: bad upload 400, OCR / LLM server failed 502.</summary>
    public sealed class ApiExceptionFilter : ExceptionFilterAttribute
    {
        public override void OnException(HttpActionExecutedContext context)
        {
            var request = context.Request;
            var exception = context.Exception;

            if (exception is InvalidUploadException)
            {
                context.Response = request.CreateResponse(HttpStatusCode.BadRequest, new { error = exception.Message });
            }
            else if (exception is UpstreamException)
            {
                // RFC 7807 problem details, as ASP.NET Core wrote them.
                var problem = new
                {
                    type = "https://tools.ietf.org/html/rfc9110#section-15.6.3",
                    title = "The OCR or LLM server failed",
                    status = 502,
                    detail = exception.Message,
                };
                var json = request.GetConfiguration().Formatters.JsonFormatter;
                context.Response = request.CreateResponse((HttpStatusCode)502, problem, json, "application/problem+json");
            }
        }
    }
}

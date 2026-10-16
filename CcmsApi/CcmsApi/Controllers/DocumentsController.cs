using System.Collections.Generic;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Http;
using System.Web.Http.Description;
using CcmsApi.Common;
using CcmsApi.Models;
using CcmsApi.Services;

namespace CcmsApi.Controllers
{
    /// <summary>Upload documents: OCR them and have the LLM do the configured task.</summary>
    [RoutePrefix("api/documents")]
    public sealed class DocumentsController : ApiController
    {
        readonly DocumentService documents;

        public DocumentsController(DocumentService documents)
        {
            this.documents = documents;
        }

        /// <summary>
        /// multipart/form-data: "files" (one or more PDF or image files, OCR'd in order and read as one
        /// document) and optional "includeText" (true also returns the OCR text). Swagger describes the
        /// form through App_Start/SwaggerConfig.cs (UploadOperationFilter).
        /// </summary>
        [HttpPost]
        [Route("process")]
        [ResponseType(typeof(DocumentResult))]
        public async Task<DocumentResult> Process(CancellationToken ct)
        {
            if (!Request.Content.IsMimeMultipartContent("form-data"))
                throw new InvalidUploadException("Send multipart/form-data with one or more PDF or image files as \"files\".");

            var form = await Request.Content.ReadAsMultipartAsync(new MultipartMemoryStreamProvider(), ct);
            var files = new List<UploadedFile>();
            var includeText = false;
            foreach (var part in form.Contents)
            {
                var disposition = part.Headers.ContentDisposition;
                var name = disposition?.Name?.Trim('"');
                var fileName = (disposition?.FileNameStar ?? disposition?.FileName)?.Trim('"');
                if (name == "files" && !string.IsNullOrEmpty(fileName))
                    files.Add(new UploadedFile(fileName, await part.ReadAsByteArrayAsync()));
                else if (name == "includeText")
                    includeText = bool.TryParse((await part.ReadAsStringAsync()).Trim(), out var value) && value;
            }
            return await documents.ProcessAsync(files, includeText, ct);
        }
    }
}

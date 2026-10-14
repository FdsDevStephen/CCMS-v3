using CcmsApi.Models;
using CcmsApi.Services;
using Microsoft.AspNetCore.Mvc;

namespace CcmsApi.Controllers;

/// <summary>Upload documents: OCR them and have the LLM do the configured task.</summary>
[ApiController]
[Route("api/documents")]
public sealed class DocumentsController(DocumentService documents) : ControllerBase
{
    [HttpPost("process")]
    [Consumes("multipart/form-data")]
    [ProducesResponseType<DocumentResult>(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status400BadRequest)]
    [ProducesResponseType<ProblemDetails>(StatusCodes.Status502BadGateway)]
    [EndpointSummary("OCR the documents and return the configured task's fields")]
    [EndpointDescription(
        "files: one or more PDF or image files, OCR'd in order and read as one document. "
        + "includeText: optional, true also returns the OCR text. "
        + "fields has one string per field of the configured task (the \"Task\" section of appsettings.json), in that order. "
        + "OCR takes ~30-45 s per page, so a call can take several minutes.")]
    public async Task<DocumentResult> Process([FromForm] DocumentUploadRequest request, CancellationToken ct) =>
        await documents.ProcessAsync(request.Files ?? [], request.IncludeText, ct);
}

using Microsoft.AspNetCore.Mvc;

namespace CcmsApi.Models;

/// <summary>multipart/form-data upload for POST /api/documents/process.</summary>
public sealed class DocumentUploadRequest
{
    /// <summary>One or more PDF or image files, read together as one document.</summary>
    [FromForm(Name = "files")]
    public List<IFormFile>? Files { get; set; }

    /// <summary>true also returns the OCR text.</summary>
    [FromForm(Name = "includeText")]
    public bool IncludeText { get; set; }
}

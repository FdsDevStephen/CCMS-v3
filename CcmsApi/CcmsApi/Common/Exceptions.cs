using System;

namespace CcmsApi.Common
{
    /// <summary>The OCR or LLM server failed; the API answers 502 (Filters/ApiExceptionFilter.cs).</summary>
    public sealed class UpstreamException : Exception
    {
        public UpstreamException(string message, Exception inner = null) : base(message, inner) { }
    }

    /// <summary>The upload cannot be processed (no files, or not PDF / image); the API answers 400.</summary>
    public sealed class InvalidUploadException : Exception
    {
        public InvalidUploadException(string message) : base(message) { }
    }
}

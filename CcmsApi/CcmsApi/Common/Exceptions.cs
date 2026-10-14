namespace CcmsApi.Common;

/// <summary>The OCR or LLM server failed; the API answers 502.</summary>
public sealed class UpstreamException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>The upload cannot be processed (no files, or not PDF / image); the API answers 400.</summary>
public sealed class InvalidUploadException(string message) : Exception(message);

using CcmsApi.Models;

namespace CcmsApi.Services;

/// <summary>Turns uploaded PDF / image files into plain text (IndicOcrClient).</summary>
public interface IOcrService
{
    /// <summary>OCR several files into one text, each headed by its file name when there are several.</summary>
    Task<string> OcrFilesAsync(IReadOnlyList<IFormFile> files, CancellationToken ct);
}

/// <summary>One field the model must fill in; Enum lists its allowed labels.</summary>
public sealed record Field(string Name, string[]? Enum = null);

/// <summary>Asks the LLM for one JSON object with exactly the schema's keys (QwenClient).</summary>
public interface ILlmService
{
    /// <summary>Every schema key is in the answer, and every Enum field holds one of its labels.</summary>
    Task<Dictionary<string, string>> ChatAsync(string system, string user, Field[] schema, CancellationToken ct);
}

/// <summary>Can the API reach the OCR and LLM servers? (ServerHealth)</summary>
public interface IServerHealth
{
    Task<HealthReport> CheckAsync(CancellationToken ct);
}

using CcmsApi.Common;
using CcmsApi.Models;
using CcmsApi.Options;
using Microsoft.Extensions.Options;

namespace CcmsApi.Services;

/// <summary>OCR the documents, then have the LLM fill in the configured task's fields.</summary>
public sealed class DocumentService(
    IOcrService ocr, ILlmService llm, IOptions<TaskOptions> task, IOptions<LlmOptions> llmOptions, ILogger<DocumentService> log)
{
    public async Task<DocumentResult> ProcessAsync(IReadOnlyList<IFormFile> files, bool includeText, CancellationToken ct)
    {
        Uploads.Validate(files);
        var text = await ocr.OcrFilesAsync(files, ct);
        var (fields, truncated) = await AnswerAsync(text, ct);
        return new DocumentResult(
            task.Value.Name, files.Select(f => Uploads.NameOf(f.FileName)).ToList(), fields,
            text.Length, truncated, includeText ? text : null);
    }

    /// <summary>Document text -> the configured task's prompt -> LLM -> the fields, in the configured order.</summary>
    async Task<(Dictionary<string, string> Fields, bool Truncated)> AnswerAsync(string text, CancellationToken ct)
    {
        var maxChars = llmOptions.Value.MaxInputChars;
        var truncated = maxChars > 0 && text.Length > maxChars;
        if (truncated)
        {
            log.LogWarning("Document text is {Length} characters; only the first {Max} are sent to the LLM", text.Length, maxChars);
            text = text[..maxChars];
        }
        var schema = task.Value.Fields.Select(f => new Field(f.Name, f.Enum is { Length: > 0 } ? f.Enum : null)).ToArray();
        var reply = await llm.ChatAsync(SystemPrompt(task.Value), UserPrompt(text, truncated), schema, ct);
        return (schema.ToDictionary(f => f.Name, f => reply[f.Name]), truncated);
    }

    static string SystemPrompt(TaskOptions task)
    {
        var fields = task.Fields.Select(f =>
            $"- \"{f.Name}\": {f.Description.Trim()}"
            + (f.Enum is { Length: > 0 } ? $" Exactly one of: {string.Join(", ", f.Enum)}." : ""));
        return $"{task.Instructions.Trim()}\n\nFields to fill in:\n{string.Join("\n", fields)}";
    }

    static string UserPrompt(string text, bool truncated) =>
        "Document text (OCR, may contain recognition errors"
        + (truncated ? "; the end of the document was cut off" : "")
        + $"):\n<<<\n{text}\n>>>";
}

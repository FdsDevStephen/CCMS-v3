using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CcmsApi.Common;
using CcmsApi.Models;
using CcmsApi.Options;

namespace CcmsApi.Services
{
    /// <summary>OCR the documents, then have the LLM fill in the configured task's fields.</summary>
    public sealed class DocumentService
    {
        readonly IOcrService ocr;
        readonly ILlmService llm;
        readonly TaskOptions task;
        readonly LlmOptions llmOptions;

        public DocumentService(IOcrService ocr, ILlmService llm, TaskOptions task, LlmOptions llmOptions)
        {
            this.ocr = ocr;
            this.llm = llm;
            this.task = task;
            this.llmOptions = llmOptions;
        }

        public async Task<DocumentResult> ProcessAsync(IReadOnlyList<UploadedFile> files, bool includeText, CancellationToken ct)
        {
            Uploads.Validate(files);
            var text = await ocr.OcrFilesAsync(files, ct);
            var (fields, truncated) = await AnswerAsync(text, ct);
            return new DocumentResult(
                task.Name, files.Select(f => Uploads.NameOf(f.FileName)).ToList(), fields,
                text.Length, truncated, includeText ? text : null);
        }

        /// <summary>Document text -> the configured task's prompt -> LLM -> the fields, in the configured order.</summary>
        async Task<(Dictionary<string, string> Fields, bool Truncated)> AnswerAsync(string text, CancellationToken ct)
        {
            var maxChars = llmOptions.MaxInputChars;
            var truncated = maxChars > 0 && text.Length > maxChars;
            if (truncated)
            {
                Trace.TraceWarning("Document text is {0} characters; only the first {1} are sent to the LLM", text.Length, maxChars);
                text = text.Substring(0, maxChars);
            }
            var schema = task.Fields
                .Select(f => new Field(f.Name, f.Enum != null && f.Enum.Length > 0 ? f.Enum : null)).ToArray();
            var reply = await llm.ChatAsync(SystemPrompt(task), UserPrompt(text, truncated), schema, ct);
            return (schema.ToDictionary(f => f.Name, f => reply[f.Name]), truncated);
        }

        static string SystemPrompt(TaskOptions task)
        {
            var fields = task.Fields.Select(f =>
                $"- \"{f.Name}\": {f.Description.Trim()}"
                + (f.Enum != null && f.Enum.Length > 0 ? $" Exactly one of: {string.Join(", ", f.Enum)}." : ""));
            return $"{task.Instructions.Trim()}\n\nFields to fill in:\n{string.Join("\n", fields)}";
        }

        static string UserPrompt(string text, bool truncated) =>
            "Document text (OCR, may contain recognition errors"
            + (truncated ? "; the end of the document was cut off" : "")
            + $"):\n<<<\n{text}\n>>>";
    }
}

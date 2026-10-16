using System.Collections.Generic;
using Newtonsoft.Json;

namespace CcmsApi.Models
{
    /// <summary>The LLM's answer for the uploaded documents.</summary>
    public sealed class DocumentResult
    {
        public DocumentResult(string task, IReadOnlyList<string> files, IReadOnlyDictionary<string, string> fields,
            int textChars, bool truncated, string text)
        {
            Task = task;
            Files = files;
            Fields = fields;
            TextChars = textChars;
            Truncated = truncated;
            Text = text;
        }

        [JsonProperty("task")]
        public string Task { get; }

        [JsonProperty("files")]
        public IReadOnlyList<string> Files { get; }

        /// <summary>One string per field of the configured task, in that order.</summary>
        [JsonProperty("fields")]
        public IReadOnlyDictionary<string, string> Fields { get; }

        [JsonProperty("text_chars")]
        public int TextChars { get; }

        [JsonProperty("truncated")]
        public bool Truncated { get; }

        /// <summary>The OCR text; only with includeText=true.</summary>
        [JsonProperty("text", NullValueHandling = NullValueHandling.Ignore)]
        public string Text { get; }
    }
}

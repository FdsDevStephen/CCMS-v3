using System.Text.Json.Serialization;

namespace CcmsApi.Models;

/// <summary>The LLM's answer for the uploaded documents.</summary>
public sealed record DocumentResult(
    [property: JsonPropertyName("task")] string Task,
    [property: JsonPropertyName("files")] IReadOnlyList<string> Files,
    [property: JsonPropertyName("fields")] IReadOnlyDictionary<string, string> Fields,
    [property: JsonPropertyName("text_chars")] int TextChars,
    [property: JsonPropertyName("truncated")] bool Truncated,
    [property: JsonPropertyName("text"), JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] string? Text);

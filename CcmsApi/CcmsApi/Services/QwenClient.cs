using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using CcmsApi.Common;
using CcmsApi.Options;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace CcmsApi.Services
{
    /// <summary>
    /// The remote Qwen3 14B server (port of qwen_chat / parse_qwen_reply in app/observation.py).
    /// One plain prompt and no JSON mode; the reply's JSON object is cut out and checked.
    /// </summary>
    public sealed class QwenClient : ILlmService
    {
        static readonly Regex JsonObject = new Regex(@"\{.*\}", RegexOptions.Singleline | RegexOptions.Compiled);

        readonly HttpClient http;
        readonly LlmOptions options;

        public QwenClient(HttpClient http, LlmOptions options)
        {
            this.http = http;
            this.options = options;
            http.Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds);
        }

        public async Task<Dictionary<string, string>> ChatAsync(string system, string user, Field[] schema, CancellationToken ct)
        {
            var keys = string.Join(", ", schema.Select(f =>
                $"\"{f.Name}\"" + (f.Enum == null ? "" : $" (one of: {string.Join(", ", f.Enum)})")));
            // Keep the prompts' line breaks "\n", as in Python, whatever the settings file's line endings.
            var prompt = $"{Text.NormalizeLineEndings(system)}\n\n{user}\n\n"
                + $"Reply with ONLY one JSON object, with exactly these string keys in this order: {keys}.";
            var body = JsonConvert.SerializeObject(new { prompt, max_tokens = options.MaxTokens });

            // The server drops connections and returns 500/504 now and then, and a reply can come
            // back without its JSON; a retry after a short wait usually works.
            for (var attempt = 1; ; attempt++)
            {
                try
                {
                    using (var content = new StringContent(body, Encoding.UTF8, "application/json"))
                    using (var response = await http.PostAsync(options.Url, content, ct))
                    {
                        response.EnsureSuccessStatusCode();
                        var json = Json.Parse(await response.Content.ReadAsStringAsync());
                        var text = json["text"]?.Value<string>() ?? "";
                        return ParseReply(text, schema);
                    }
                }
                catch (Exception exc) when (exc is HttpRequestException || exc is JsonException || exc is InvalidDataException
                                            || exc is TaskCanceledException && !ct.IsCancellationRequested)
                {
                    if (attempt >= options.Attempts)
                        throw new UpstreamException($"Qwen server at {options.Url} failed {attempt} times ({exc.Message})", exc);
                    Trace.TraceWarning("Qwen server attempt {0} failed ({1}); retrying", attempt, exc.Message);
                    await Task.Delay(TimeSpan.FromSeconds(15 * attempt), ct);
                }
            }
        }

        static Dictionary<string, string> ParseReply(string text, Field[] schema)
        {
            var match = JsonObject.Match(text);
            if (!match.Success)
                throw new InvalidDataException(
                    $"No JSON in the model's reply (thinking may have used up max_tokens): {Text.Tail(text, 200)}");

            var reply = Json.Parse(EscapeControlCharacters(match.Value)) as JObject;
            if (reply == null)
                throw new InvalidDataException("The model's reply is not a JSON object");
            var result = new Dictionary<string, string>();
            foreach (var property in reply.Properties())
            {
                switch (property.Value.Type)
                {
                    case JTokenType.String:
                        result[property.Name] = property.Value.Value<string>() ?? "";
                        break;
                    case JTokenType.Null:
                        result[property.Name] = "";
                        break;
                    default:
                        result[property.Name] = property.Value.ToString(Formatting.None);
                        break;
                }
            }

            var missing = schema.Where(f => !result.ContainsKey(f.Name)).Select(f => f.Name).ToList();
            if (missing.Count > 0)
                throw new InvalidDataException($"Reply is missing {string.Join(", ", missing)}");
            foreach (var field in schema.Where(f => f.Enum != null))
                if (!field.Enum.Contains(result[field.Name]))
                    throw new InvalidDataException($"{field.Name} is not one of the labels: {result[field.Name]}");
            return result;
        }

        /// <summary>Raw line breaks and tabs inside JSON strings (Python's json.loads(strict=False) accepts them).</summary>
        static string EscapeControlCharacters(string json)
        {
            var output = new StringBuilder(json.Length);
            bool inString = false, escaped = false;
            foreach (var c in json)
            {
                if (inString && !escaped && c < ' ')
                {
                    switch (c)
                    {
                        case '\n': output.Append("\\n"); break;
                        case '\r': output.Append("\\r"); break;
                        case '\t': output.Append("\\t"); break;
                        default: output.Append($"\\u{(int)c:x4}"); break;
                    }
                    continue;
                }
                output.Append(c);
                if (escaped) escaped = false;
                else if (inString && c == '\\') escaped = true;
                else if (c == '"') inString = !inString;
            }
            return output.ToString();
        }
    }
}

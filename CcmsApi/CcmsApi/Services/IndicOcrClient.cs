using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using CcmsApi.Common;
using CcmsApi.Models;
using CcmsApi.Options;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using PdfSharp.Pdf.IO;

namespace CcmsApi.Services
{
    /// <summary>
    /// OCR through the hosted IndicOCR server (port of app/ocr_api.py, ocr_file_indic):
    ///     POST Url   multipart field "file"  ->  {"pages": [{"page_number", "markdown"}]}
    /// Its markdown has "▁" where spaces belong, HTML tables and &lt;img&gt; placeholders;
    /// CleanMarkdown turns that into plain text.
    /// </summary>
    public sealed class IndicOcrClient : IOcrService
    {
        static readonly int[] RetryWaits = { 10, 30, 60 }; // seconds to wait before each retry when the server is down or busy

        readonly HttpClient http;
        readonly OcrOptions options;

        public IndicOcrClient(HttpClient http, OcrOptions options)
        {
            this.http = http;
            this.options = options;
            http.Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds);
        }

        /// <summary>OCR several files into one text, each headed by its file name when there are several.</summary>
        public async Task<string> OcrFilesAsync(IReadOnlyList<UploadedFile> files, CancellationToken ct)
        {
            var texts = new List<string>();
            foreach (var file in files)
            {
                var name = Uploads.NameOf(file.FileName); // a folder's files arrive named with their path
                var text = await OcrFileAsync(name, file.Content, ct);
                texts.Add(files.Count > 1 ? $"===== {name} =====\n\n{text}" : text);
            }
            return string.Join("\n\n", texts);
        }

        public async Task<string> OcrFileAsync(string fileName, byte[] content, CancellationToken ct)
        {
            var pages = await PagesAsync(fileName, content, ct);
            var texts = pages.Select(page =>
            {
                var text = CleanMarkdown(page.Markdown);
                if (text.Length == 0)
                    text = $"[OCR FAILED FOR PAGE {page.Number}]";
                return pages.Count > 1 ? $"===== Source page {page.Number} =====\n{text}" : text;
            });
            return string.Join("\n\n", texts);
        }

        /// <summary>Long PDFs go in small page ranges: each request stays short, and a failed range is retried on its own.</summary>
        async Task<List<Page>> PagesAsync(string fileName, byte[] content, CancellationToken ct)
        {
            int? total = null;
            if (Path.GetExtension(fileName).Equals(".pdf", StringComparison.OrdinalIgnoreCase) && options.ChunkPages > 0)
            {
                try
                {
                    using (var stream = new MemoryStream(content))
                    using (var document = PdfReader.Open(stream, PdfDocumentOpenMode.Import))
                        total = document.PageCount;
                }
                catch (Exception)
                {
                    total = null; // unreadable here -> let the server decide, whole file in one request
                }
            }
            if (total == null || total <= options.ChunkPages)
                return await RequestAsync(fileName, content, null, ct);

            var ranges = new List<string>();
            for (var start = 1; start <= total; start += options.ChunkPages)
                ranges.Add($"{start}-{Math.Min(start + options.ChunkPages - 1, total.Value)}");

            // At most Parallel ranges at the server at a time.
            var chunks = new List<Page>[ranges.Count];
            using (var slots = new SemaphoreSlim(Math.Max(1, options.Parallel)))
            {
                await Task.WhenAll(ranges.Select(async (range, i) =>
                {
                    await slots.WaitAsync(ct);
                    try
                    {
                        chunks[i] = await RequestAsync(fileName, content, range, ct);
                    }
                    finally
                    {
                        slots.Release();
                    }
                }));
            }
            return chunks.SelectMany(chunk => chunk).ToList();
        }

        /// <summary>One IndicOCR call (whole file, or only ?pages=&lt;range&gt;).</summary>
        async Task<List<Page>> RequestAsync(string fileName, byte[] content, string pageRange, CancellationToken ct)
        {
            var url = pageRange == null ? options.Url : $"{options.Url}?pages={Uri.EscapeDataString(pageRange)}";
            for (var attempt = 0; ; attempt++)
            {
                int? wait = attempt < RetryWaits.Length ? RetryWaits[attempt] : (int?)null;
                string error;
                var timedOut = false;
                try
                {
                    using (var form = new MultipartFormDataContent())
                    {
                        form.Add(new ByteArrayContent(content), "file", fileName);
                        using (var response = await http.PostAsync(url, form, ct))
                        {
                            var body = await response.Content.ReadAsStringAsync();
                            if ((int)response.StatusCode < 500)
                            {
                                if (response.StatusCode != HttpStatusCode.OK)
                                    throw new UpstreamException(
                                        $"IndicOCR server rejected {fileName}: HTTP {(int)response.StatusCode} {Text.Head(body, 200)}");
                                return ParsePages(body);
                            }
                            error = $"HTTP {(int)response.StatusCode}: {Text.Head(body, 100)}";
                            timedOut = response.StatusCode == HttpStatusCode.GatewayTimeout;
                        }
                    }
                }
                catch (TaskCanceledException exc) when (!ct.IsCancellationRequested)
                {
                    error = exc.Message;
                    timedOut = true;
                }
                catch (HttpRequestException exc)
                {
                    error = exc.InnerException != null ? $"{exc.Message} {exc.InnerException.Message}" : exc.Message;
                }
                // A timeout means the document is too long for the server; retrying only wastes minutes.
                if (wait == null || timedOut)
                    throw new UpstreamException($"OCR server failed on {fileName}: {error}");
                Trace.TraceWarning("OCR server busy or unreachable ({0}); retrying in {1}s", Text.Head(error, 60), wait);
                await Task.Delay(TimeSpan.FromSeconds(wait.Value), ct);
            }
        }

        static List<Page> ParsePages(string body)
        {
            JToken json;
            try
            {
                json = Json.Parse(body);
            }
            catch (JsonException exc)
            {
                throw new UpstreamException($"IndicOCR server answered something that is not JSON: {exc.Message}", exc);
            }
            var pages = json["pages"] as JArray;
            if (pages == null)
                throw new UpstreamException($"IndicOCR server answer has no \"pages\": {Text.Head(body, 200)}");
            return pages
                .Select(page => new Page(
                    page["page_number"]?.ToString() ?? "",
                    page["markdown"]?.Value<string>() ?? ""))
                .ToList();
        }

        /// <summary>IndicOCR markdown -> plain text: real spaces, one table row per line, no tags or photo labels.</summary>
        public static string CleanMarkdown(string markdown)
        {
            var text = markdown.Replace('▁', ' ');
            text = ImgTag.Replace(text, "");
            text = BrTag.Replace(text, " / ");
            text = CellBoundary.Replace(text, " | ");
            text = RowEnd.Replace(text, "\n");
            text = AnyTag.Replace(text, ""); // remaining table tags
            text = WebUtility.HtmlDecode(text);
            var lines = Text.SplitLines(text)
                .Select(line => Pipes.Replace(SpacesOrTabs.Replace(line, " "), " | ").Trim(' ', '|'));
            return ManyNewlines.Replace(string.Join("\n", lines), "\n\n").Trim();
        }

        sealed class Page
        {
            public Page(string number, string markdown)
            {
                Number = number;
                Markdown = markdown;
            }

            public string Number { get; }
            public string Markdown { get; }
        }

        static readonly Regex ImgTag = new Regex(@"<img[^>]*>", RegexOptions.Compiled);
        static readonly Regex BrTag = new Regex(@"<br\s*/?>", RegexOptions.Compiled);
        static readonly Regex CellBoundary = new Regex(@"</t[hd]>\s*<t[hd][^>]*>", RegexOptions.Compiled);
        static readonly Regex RowEnd = new Regex(@"</tr>", RegexOptions.Compiled);
        static readonly Regex AnyTag = new Regex(@"<[^>]+>", RegexOptions.Compiled);
        static readonly Regex SpacesOrTabs = new Regex(@"[ \t]+", RegexOptions.Compiled);
        static readonly Regex Pipes = new Regex(@"(\s*\|)+\s*", RegexOptions.Compiled);
        static readonly Regex ManyNewlines = new Regex(@"\n{3,}", RegexOptions.Compiled);
    }
}

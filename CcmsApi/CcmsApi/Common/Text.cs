using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace CcmsApi.Common
{
    static class Text
    {
        // Every line break Python's str.splitlines() knows; the last two are the Unicode line and paragraph separators.
        static readonly Regex LineBreak = new Regex(
            "\r\n|[\n\r\v\f\x1c\x1d\x1e\x85" + (char)0x2028 + (char)0x2029 + "]", RegexOptions.Compiled);

        // What .NET's string.ReplaceLineEndings() treats as a line break.
        static readonly Regex LineEnding = new Regex(
            "\r\n|[\r\n\f\x85" + (char)0x2028 + (char)0x2029 + "]", RegexOptions.Compiled);

        /// <summary>Like Python's str.splitlines() (no empty last line after a final line break).</summary>
        public static List<string> SplitLines(string text)
        {
            var lines = LineBreak.Split(text).ToList();
            if (lines.Count > 0 && lines[lines.Count - 1].Length == 0)
                lines.RemoveAt(lines.Count - 1);
            return lines;
        }

        /// <summary>Every line break as "\n".</summary>
        public static string NormalizeLineEndings(string text) => LineEnding.Replace(text, "\n");

        public static string Head(string text, int length) => text.Length <= length ? text : text.Substring(0, length);

        public static string Tail(string text, int length) => text.Length <= length ? text : text.Substring(text.Length - length);
    }
}

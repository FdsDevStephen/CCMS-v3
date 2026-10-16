using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace CcmsApi.Common
{
    static class Json
    {
        /// <summary>
        /// Parse without Newtonsoft's guessing: date-like strings stay strings exactly as written,
        /// and numbers keep their digits (as System.Text.Json's JsonDocument did).
        /// </summary>
        public static JToken Parse(string json)
        {
            using (var reader = new JsonTextReader(new StringReader(json))
            {
                DateParseHandling = DateParseHandling.None,
                FloatParseHandling = FloatParseHandling.Decimal,
            })
            {
                var token = JToken.ReadFrom(reader);
                // Like JsonDocument: anything after the value is an error.
                if (reader.Read() && reader.TokenType != JsonToken.Comment)
                    throw new JsonReaderException($"Unexpected content after the JSON value at position {reader.LinePosition}.");
                return token;
            }
        }
    }
}

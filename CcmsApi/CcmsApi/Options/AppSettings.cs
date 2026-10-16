using System.Configuration;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace CcmsApi.Options
{
    /// <summary>
    /// App_Data/appsettings.json: the "Ocr", "Llm" and "Task" sections. Read once at startup
    /// (App_Start/WebApiConfig.cs); a missing section keeps the defaults in its class.
    /// App_Data is never served by IIS, so the file cannot be downloaded.
    /// </summary>
    public sealed class AppSettings
    {
        public OcrOptions Ocr { get; private set; }
        public LlmOptions Llm { get; private set; }
        public TaskOptions Task { get; private set; }

        /// <summary>Throws ConfigurationErrorsException when the file is missing or the task is not usable.</summary>
        public static AppSettings Load(string path)
        {
            if (!File.Exists(path))
                throw new ConfigurationErrorsException($"Settings file not found: {path}");

            JObject json;
            try
            {
                json = JObject.Parse(File.ReadAllText(path));
            }
            catch (JsonException exc)
            {
                throw new ConfigurationErrorsException($"{path} is not valid JSON: {exc.Message}", exc);
            }

            var settings = new AppSettings
            {
                Ocr = json["Ocr"]?.ToObject<OcrOptions>() ?? new OcrOptions(),
                Llm = json["Llm"]?.ToObject<LlmOptions>() ?? new LlmOptions(),
                Task = json["Task"]?.ToObject<TaskOptions>() ?? new TaskOptions(),
            };

            var errors = settings.Task.Validate();
            if (errors.Count > 0)
                throw new ConfigurationErrorsException($"Fix the \"Task\" section of {path}: {string.Join(" ", errors)}");
            return settings;
        }
    }
}

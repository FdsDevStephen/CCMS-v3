using System.Web.Hosting;
using System.Web.Http;
using CcmsApi.Common;
using CcmsApi.Filters;
using CcmsApi.Options;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;

namespace CcmsApi
{
    // CCMS Document API: OCR uploaded documents on the IndicOCR server, then have the Qwen3 14B server
    // do the task configured in App_Data/appsettings.json ("Task") with the text, and return its fields.
    //
    //   GET  /health                  Controllers/HealthController.cs
    //   POST /api/documents/process   Controllers/DocumentsController.cs
    //
    // Test page: /     Swagger: /swagger
    public static class WebApiConfig
    {
        public static void Register(HttpConfiguration config)
        {
            // Settings (App_Data/appsettings.json); the Task section is checked here, at startup.
            var settings = AppSettings.Load(HostingEnvironment.MapPath("~/App_Data/appsettings.json"));
            config.DependencyResolver = new ServiceResolver(settings);

            config.MapHttpAttributeRoutes();
            config.Filters.Add(new ApiExceptionFilter());

            // JSON only. Property names in camelCase ("ocrServer", "up"), except where a model names
            // them ("text_chars"); the task's field names are dictionary keys and are kept as configured.
            config.Formatters.Remove(config.Formatters.XmlFormatter);
            var json = config.Formatters.JsonFormatter.SerializerSettings;
            json.ContractResolver = new DefaultContractResolver
            {
                NamingStrategy = new CamelCaseNamingStrategy { ProcessDictionaryKeys = false, OverrideSpecifiedNames = false },
            };
            json.DateParseHandling = DateParseHandling.None;

            SwaggerConfig.Register(config);
        }
    }
}

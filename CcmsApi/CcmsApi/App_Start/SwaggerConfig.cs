using System.Collections.Generic;
using System.Web.Http;
using System.Web.Http.Description;
using Swashbuckle.Application;
using Swashbuckle.Swagger;

namespace CcmsApi
{
    /// <summary>Swagger UI at /swagger (Swashbuckle 5: Swagger 2.0 and the swagger-ui 2 page).</summary>
    public static class SwaggerConfig
    {
        public static void Register(HttpConfiguration config)
        {
            config
                .EnableSwagger(swagger =>
                {
                    swagger.SingleApiVersion("v1", "CCMS Document API").Description(
                        "Upload documents; the API OCRs them and has the LLM fill in the fields of the configured task "
                        + "(set in App_Data/appsettings.json). OCR takes ~30-45 s per page, so a call can take several minutes.");
                    swagger.OperationFilter<UploadOperationFilter>();
                })
                .EnableSwaggerUi(ui => ui.DocumentTitle("CCMS Document API"));
        }
    }

    /// <summary>
    /// POST /api/documents/process reads its multipart form itself, so Swagger cannot see the form
    /// fields; this describes them, and gives the Swagger page a file picker.
    /// </summary>
    public sealed class UploadOperationFilter : IOperationFilter
    {
        public void Apply(Operation operation, SchemaRegistry schemaRegistry, ApiDescription apiDescription)
        {
            if (apiDescription.RelativePath != "api/documents/process")
                return;

            operation.summary = "OCR the documents and return the configured task's fields";
            operation.description =
                "files: one or more PDF or image files, OCR'd in order and read as one document. "
                + "includeText: optional, true also returns the OCR text. "
                + "fields has one string per field of the configured task (the \"Task\" section of App_Data/appsettings.json), in that order. "
                + "OCR takes ~30-45 s per page, so a call can take several minutes.";
            operation.consumes = new List<string> { "multipart/form-data" };
            operation.parameters = new List<Parameter>
            {
                new Parameter
                {
                    name = "files", @in = "formData", type = "file", required = true,
                    description = "A PDF or image file. Swagger sends one; the test page and other clients can send several \"files\" parts.",
                },
                new Parameter
                {
                    name = "includeText", @in = "formData", type = "boolean", required = false,
                    description = "true also returns the OCR text.",
                },
            };
            operation.responses["400"] = new Response { description = "Bad upload: no files, or not PDF / image." };
            operation.responses["502"] = new Response { description = "The OCR or LLM server failed." };
        }
    }
}

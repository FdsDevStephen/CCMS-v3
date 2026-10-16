using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Web.Http.Dependencies;
using CcmsApi.Controllers;
using CcmsApi.Options;
using CcmsApi.Services;

namespace CcmsApi.Common
{
    /// <summary>
    /// Builds the controllers with their services (Web API 2 has no service container of its own).
    /// The OCR, LLM and health clients are made once and shared: each keeps one HttpClient, as
    /// HttpClient is meant to be reused. Everything else Web API asks for gets its own default (null).
    /// </summary>
    public sealed class ServiceResolver : IDependencyResolver
    {
        readonly AppSettings settings;
        readonly IOcrService ocr;
        readonly ILlmService llm;
        readonly IServerHealth health;

        public ServiceResolver(AppSettings settings)
        {
            this.settings = settings;
            ocr = new IndicOcrClient(new HttpClient(), settings.Ocr);
            llm = new QwenClient(new HttpClient(), settings.Llm);
            health = new ServerHealth(new HttpClient { Timeout = TimeSpan.FromSeconds(5) }, settings.Ocr, settings.Llm);
        }

        public object GetService(Type serviceType)
        {
            if (serviceType == typeof(DocumentsController))
                return new DocumentsController(new DocumentService(ocr, llm, settings.Task, settings.Llm));
            if (serviceType == typeof(HealthController))
                return new HealthController(health);
            return null;
        }

        public IEnumerable<object> GetServices(Type serviceType) => Enumerable.Empty<object>();

        public IDependencyScope BeginScope() => this;

        public void Dispose() { }
    }
}

using System;
using System.Diagnostics;
using System.Web;
using System.Web.Http;

namespace CcmsApi
{
    public class WebApiApplication : HttpApplication
    {
        // Why the API did not start (e.g. the Task settings are not usable); every request is told.
        static Exception startupError;

        protected void Application_Start()
        {
            try
            {
                GlobalConfiguration.Configure(WebApiConfig.Register);
            }
            catch (Exception exc)
            {
                startupError = exc;
                Trace.TraceError("CcmsApi did not start: {0}", exc);
            }
        }

        protected void Application_BeginRequest()
        {
            if (startupError == null)
                return;
            Response.StatusCode = 500;
            Response.ContentType = "text/plain";
            Response.Write($"CcmsApi did not start: {startupError.Message}\nFix it, then restart the site (or touch Web.config).");
            CompleteRequest();
        }
    }
}

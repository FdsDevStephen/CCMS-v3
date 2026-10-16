namespace CcmsApi.Models
{
    /// <summary>One server's reachability.</summary>
    public sealed class ServerStatus
    {
        public ServerStatus(bool up, long milliseconds, string error)
        {
            Up = up;
            Milliseconds = milliseconds;
            Error = error;
        }

        public bool Up { get; }
        public long Milliseconds { get; }
        public string Error { get; }
    }

    public sealed class HealthReport
    {
        public HealthReport(ServerStatus ocrServer, ServerStatus llmServer)
        {
            OcrServer = ocrServer;
            LlmServer = llmServer;
        }

        public ServerStatus OcrServer { get; }
        public ServerStatus LlmServer { get; }
        public bool Healthy => OcrServer.Up && LlmServer.Up;
    }
}

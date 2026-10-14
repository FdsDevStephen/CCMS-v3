namespace CcmsApi.Models;

/// <summary>One server's reachability.</summary>
public sealed record ServerStatus(bool Up, long Milliseconds, string? Error);

public sealed record HealthReport(ServerStatus OcrServer, ServerStatus LlmServer)
{
    public bool Healthy => OcrServer.Up && LlmServer.Up;
}

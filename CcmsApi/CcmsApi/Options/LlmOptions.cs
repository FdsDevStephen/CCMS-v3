namespace CcmsApi.Options
{
    /// <summary>The Qwen3 14B server (the "Llm" section of App_Data/appsettings.json).</summary>
    public sealed class LlmOptions
    {
        // generate_greedy: temperature 0 and no thinking, so the same text always gets the same answer.
        public string Url { get; set; } = "http://101.53.137.13:8004/qwen13/generate_greedy";
        public int Attempts { get; set; } = 3;
        public int MaxTokens { get; set; } = 4096;
        public int TimeoutSeconds { get; set; } = 900;
        // Qwen3 14B has a 32k-token context; longer document text is cut to this many characters.
        public int MaxInputChars { get; set; } = 60000;
    }
}

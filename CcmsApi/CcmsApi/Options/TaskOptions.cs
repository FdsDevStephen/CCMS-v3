using Microsoft.Extensions.Options;

namespace CcmsApi.Options;

/// <summary>What the LLM must do with a document's text (the "Task" section of appsettings.json).</summary>
public sealed class TaskOptions
{
    public string Name { get; set; } = "";
    public string Instructions { get; set; } = "";
    public List<TaskField> Fields { get; set; } = [];
}

/// <summary>One answer field: its JSON key, what goes in it, and optionally the only values allowed.</summary>
public sealed class TaskField
{
    public string Name { get; set; } = "";
    public string Description { get; set; } = "";
    public string[]? Enum { get; set; }
}

/// <summary>Checked at startup: the API does not start with a task the LLM cannot answer.</summary>
public sealed class TaskOptionsValidator : IValidateOptions<TaskOptions>
{
    public ValidateOptionsResult Validate(string? name, TaskOptions task)
    {
        var errors = new List<string>();
        if (string.IsNullOrWhiteSpace(task.Instructions))
            errors.Add("Task:Instructions is empty.");
        if (task.Fields.Count == 0)
            errors.Add("Task:Fields has no fields.");
        if (task.Fields.Any(f => string.IsNullOrWhiteSpace(f.Name)))
            errors.Add("Every field in Task:Fields needs a Name.");
        var duplicates = task.Fields.Where(f => !string.IsNullOrWhiteSpace(f.Name))
            .GroupBy(f => f.Name.Trim(), StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
        if (duplicates.Count > 0)
            errors.Add($"Task:Fields has duplicate names: {string.Join(", ", duplicates)}.");
        return errors.Count > 0 ? ValidateOptionsResult.Fail(errors) : ValidateOptionsResult.Success;
    }
}

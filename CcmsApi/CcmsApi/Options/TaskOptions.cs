using System;
using System.Collections.Generic;
using System.Linq;

namespace CcmsApi.Options
{
    /// <summary>What the LLM must do with a document's text (the "Task" section of App_Data/appsettings.json).</summary>
    public sealed class TaskOptions
    {
        public string Name { get; set; } = "";
        public string Instructions { get; set; } = "";
        public List<TaskField> Fields { get; set; } = new List<TaskField>();

        /// <summary>Checked at startup: the API does not start with a task the LLM cannot answer.</summary>
        public List<string> Validate()
        {
            var errors = new List<string>();
            if (string.IsNullOrWhiteSpace(Instructions))
                errors.Add("Task:Instructions is empty.");
            if (Fields == null || Fields.Count == 0)
            {
                errors.Add("Task:Fields has no fields.");
                return errors;
            }
            if (Fields.Any(f => f == null || string.IsNullOrWhiteSpace(f.Name)))
                errors.Add("Every field in Task:Fields needs a Name.");
            var duplicates = Fields.Where(f => f != null && !string.IsNullOrWhiteSpace(f.Name))
                .GroupBy(f => f.Name.Trim(), StringComparer.OrdinalIgnoreCase).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
            if (duplicates.Count > 0)
                errors.Add($"Task:Fields has duplicate names: {string.Join(", ", duplicates)}.");
            return errors;
        }
    }

    /// <summary>One answer field: its JSON key, what goes in it, and optionally the only values allowed.</summary>
    public sealed class TaskField
    {
        public string Name { get; set; } = "";
        public string Description { get; set; } = "";
        public string[] Enum { get; set; }
    }
}

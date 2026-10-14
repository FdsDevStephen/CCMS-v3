namespace CcmsApi.Common;

/// <summary>
/// Upload file names. A folder arrives as its files, each named with its path inside the
/// folder (as a browser's folder picker sends them), e.g. "case-12/letters/report.pdf".
/// </summary>
public static class Uploads
{
    public static readonly HashSet<string> SupportedExtensions =
        [".pdf", ".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".tif", ".tiff", ".webp"];

    static string[] PathParts(string fileName) =>
        fileName.Split(['/', '\\'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    /// <summary>The file's own name, without the folders in front of it.</summary>
    public static string NameOf(string fileName) => PathParts(fileName).LastOrDefault() ?? fileName;

    public static bool IsSupported(string fileName) =>
        SupportedExtensions.Contains(Path.GetExtension(NameOf(fileName)).ToLowerInvariant());

    /// <summary>Throws InvalidUploadException unless there are files and all are PDF or image files.</summary>
    public static void Validate(IReadOnlyList<IFormFile> files)
    {
        if (files.Count == 0)
            throw new InvalidUploadException("Upload one or more PDF or image files as \"files\".");
        var unsupported = files.Where(f => !IsSupported(f.FileName)).Select(f => NameOf(f.FileName)).ToList();
        if (unsupported.Count > 0)
            throw new InvalidUploadException($"Only PDF and image files are supported: {string.Join(", ", unsupported)}");
    }
}

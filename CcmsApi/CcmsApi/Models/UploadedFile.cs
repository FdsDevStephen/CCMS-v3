namespace CcmsApi.Models
{
    /// <summary>One "files" part of the multipart upload, read into memory.</summary>
    public sealed class UploadedFile
    {
        public UploadedFile(string fileName, byte[] content)
        {
            FileName = fileName;
            Content = content;
        }

        /// <summary>As the browser sent it; a folder's files carry their path, e.g. "case-12/report.pdf".</summary>
        public string FileName { get; }
        public byte[] Content { get; }
    }
}

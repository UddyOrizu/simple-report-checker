import { useRef, useState } from "react";
import { UploadTooLargeError, uploadDocument } from "../api/client";

interface DocumentUploadProps {
  onUploaded: (documentId: string) => void;
  onClose: () => void;
}

export function DocumentUpload({ onUploaded, onClose }: DocumentUploadProps) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = async (file: File) => {
    setError(null);
    setUploading(true);
    try {
      const result = await uploadDocument(file);
      onUploaded(result.id);
    } catch (err) {
      if (err instanceof UploadTooLargeError) {
        setError(`File too large: ${err.message}`);
      } else {
        setError(err instanceof Error ? err.message : "Upload failed.");
      }
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-full max-w-md">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold">Upload document</h2>
          <button className="text-gray-400 hover:text-gray-600" onClick={onClose}>
            ✕
          </button>
        </div>

        <div
          className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
            dragging ? "border-blue-500 bg-blue-50" : "border-gray-300"
          }`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) submit(file);
          }}
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? (
            <p className="text-gray-500">Uploading…</p>
          ) : (
            <p className="text-gray-500">Drag & drop a .docx or .pdf here, or click to browse</p>
          )}
          <input
            ref={inputRef}
            type="file"
            accept=".docx,.pdf"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) submit(file);
            }}
          />
        </div>

        {error && <p className="text-red-600 text-sm mt-3">{error}</p>}
      </div>
    </div>
  );
}

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { listDocuments } from "../api/client";
import { DocumentUpload } from "../components/DocumentUpload";
import { STATUS_STYLES, VERDICT_STYLES } from "../components/verdictColors";
import type { Verdict } from "../api/types";

const PAGE_SIZE = 20;

export function DocumentHistory() {
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<string>("");
  const [uploadOpen, setUploadOpen] = useState(false);

  const { data: documents, isLoading, refetch } = useQuery({
    queryKey: ["documents", offset, status],
    queryFn: () => listDocuments({ limit: PAGE_SIZE, offset, status: status || undefined }),
  });

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-semibold">Documents</h1>
        <button className="bg-blue-600 text-white rounded px-4 py-2 text-sm" onClick={() => setUploadOpen(true)}>
          Upload new document
        </button>
      </div>

      <div className="flex gap-2 mb-4 text-sm">
        <select
          className="border rounded px-2 py-1"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
          <option value="ingested">Ingested</option>
          <option value="complete">Complete</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}
      {!isLoading && documents?.length === 0 && (
        <p className="text-gray-500">No documents yet. Upload one to get started.</p>
      )}

      {documents && documents.length > 0 && (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-2">Filename</th>
              <th>Uploaded</th>
              <th>Status</th>
              <th>Pages</th>
              <th>Claims</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr
                key={doc.id}
                className="border-b hover:bg-gray-50 cursor-pointer"
                onClick={() => navigate(`/documents/${doc.id}`)}
              >
                <td className="py-2">{doc.filename}</td>
                <td>{new Date(doc.created_at).toLocaleString()}</td>
                <td>
                  <span className={`text-xs rounded px-2 py-0.5 ${STATUS_STYLES[doc.status] ?? "bg-gray-100"}`}>
                    {doc.status}
                  </span>
                </td>
                <td>{doc.page_count ?? "—"}</td>
                <td>
                  <div className="flex gap-1 flex-wrap">
                    {Object.entries(doc.claim_summary).map(([verdict, count]) => (
                      <span
                        key={verdict}
                        className={`text-xs rounded px-1.5 py-0.5 border ${VERDICT_STYLES[verdict as Verdict] ?? "bg-gray-100"}`}
                      >
                        {verdict}: {count}
                      </span>
                    ))}
                    {Object.keys(doc.claim_summary).length === 0 && <span className="text-xs text-gray-400">—</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex justify-between mt-4 text-sm">
        <button
          className="border rounded px-3 py-1 disabled:opacity-40"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </button>
        <button
          className="border rounded px-3 py-1 disabled:opacity-40"
          disabled={!documents || documents.length < PAGE_SIZE}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>

      {uploadOpen && (
        <DocumentUpload
          onClose={() => setUploadOpen(false)}
          onUploaded={(documentId) => {
            setUploadOpen(false);
            refetch();
            navigate(`/documents/${documentId}`);
          }}
        />
      )}
    </div>
  );
}

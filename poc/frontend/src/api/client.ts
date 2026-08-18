import type { ClaimDetail, ClaimListItem, DocumentDetail, DocumentSummary } from "./types";

const BASE = "/api";

export class UploadTooLargeError extends Error {}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function listDocuments(params: { limit?: number; offset?: number; status?: string } = {}): Promise<DocumentSummary[]> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  if (params.status) query.set("status", params.status);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return getJson<DocumentSummary[]>(`/documents${suffix}`);
}

export async function getDocument(documentId: string): Promise<DocumentDetail> {
  return getJson<DocumentDetail>(`/documents/${documentId}`);
}

export async function listClaims(documentId: string): Promise<ClaimListItem[]> {
  return getJson<ClaimListItem[]>(`/documents/${documentId}/claims`);
}

export async function getClaim(claimId: string): Promise<ClaimDetail> {
  return getJson<ClaimDetail>(`/claims/${claimId}`);
}

export async function reverifyClaim(claimId: string): Promise<{ claim_id: string; reconciled: Record<string, unknown> }> {
  const response = await fetch(`${BASE}/claims/${claimId}/reverify`, { method: "POST" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function uploadDocument(file: File): Promise<{ id: string; status: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${BASE}/documents`, { method: "POST", body: formData });

  if (response.status === 413) {
    const body = await response.json().catch(() => ({ detail: "File too large." }));
    throw new UploadTooLargeError(body.detail ?? "File too large.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json();
}

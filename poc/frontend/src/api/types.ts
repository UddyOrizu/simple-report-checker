export type DocumentStatus = "queued" | "processing" | "ingested" | "complete" | "failed";

export type Verdict = "supported" | "contradicted" | "insufficient" | "disputed";

export type Severity = "critical" | "major" | "minor" | "info";

export type Scope = "internal" | "external" | "both";

export interface ClaimSummary {
  [verdict: string]: number;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  file_type: string;
  status: DocumentStatus;
  page_count: number | null;
  file_size_bytes: number | null;
  has_structural_index: boolean;
  created_at: string;
  claim_summary: ClaimSummary;
  severity_summary: Partial<Record<Severity, number>>;
}

export interface DocumentDetail {
  id: string;
  filename: string;
  file_type: string;
  status: DocumentStatus;
  page_count: number | null;
  file_size_bytes: number | null;
  has_structural_index: boolean;
  created_at: string;
}

export interface ClaimListItem {
  id: string;
  claim_text: string;
  claim_type: string;
  scope: Scope;
  domain: string | null;
  status: string;
  final_verdict: Verdict | null;
  final_confidence: number | null;
  severity: Severity | null;
}

export interface VerdictDetail {
  verifier_verdict: Verdict | null;
  verifier_confidence: number | null;
  verifier_reasoning: string | null;
  challenger_verdict: Verdict | null;
  challenger_confidence: number | null;
  challenger_reasoning: string | null;
  agreement: boolean | null;
  final_verdict: Verdict;
  final_confidence: number;
  severity: Severity;
  resolved_by: "deterministic" | "agent";
  resolved_at: string;
}

export interface EvidenceItem {
  id: string;
  source_type: string;
  source_ref: string;
  content_snippet: string | null;
  authority_score: number | null;
  retrieved_at: string;
}

export interface AgentTraceItem {
  id: string;
  agent_name: string;
  prompt_sent: string;
  raw_response: string;
  tool_calls: { tool_name: string; tool_args: unknown; result: string }[] | null;
  config_hash: string;
  created_at: string;
}

export interface ClaimEntity {
  text: string;
  label: string;
}

export interface ClaimDetail {
  id: string;
  document_id: string;
  claim_text: string;
  source_span: string;
  claim_type: string;
  scope: Scope;
  requires: string[];
  domain: string | null;
  domain_confidence: number | null;
  domain_source: string | null;
  entities: ClaimEntity[];
  routing_decision: string | null;
  suggested_search_queries: string[];
  cites_external_source: boolean;
  is_opinion_or_unverifiable: boolean;
  verdict: VerdictDetail | null;
  evidence: EvidenceItem[];
  traces: AgentTraceItem[];
}

import type { Severity, Verdict } from "../api/types";

/** Shared color coding: red-leaning for contradicted/disputed, neutral for supported/insufficient
 * — used consistently across the history badges and the claim review panel. */
export const VERDICT_STYLES: Record<Verdict, string> = {
  contradicted: "bg-red-100 text-red-800 border-red-300",
  disputed: "bg-red-50 text-red-700 border-red-200",
  supported: "bg-green-100 text-green-800 border-green-300",
  insufficient: "bg-gray-100 text-gray-700 border-gray-300",
};

export const SEVERITY_STYLES: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  major: "bg-orange-500 text-white",
  minor: "bg-yellow-400 text-black",
  info: "bg-gray-300 text-black",
};

export const STATUS_STYLES: Record<string, string> = {
  queued: "bg-gray-100 text-gray-700",
  processing: "bg-blue-100 text-blue-800",
  ingested: "bg-blue-100 text-blue-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export type DocumentView = {
  id: string;
  source_id: string;
  source_version: number;
  display_name: string;
  kind?: string;
  state: string;
  stage?: string | null;
  progress?: number | null;
  extractor_version?: string | null;
  chunker_version?: string | null;
  index_generation_id?: string | null;
  details: Record<string, unknown>;
};

export type DocumentBlock = {
  id: string;
  ordinal: number;
  kind: string;
  text: string;
  heading?: string | null;
  location: Record<string, unknown>;
  language?: string | null;
  scripts?: string[];
};

export type DocumentBlocks = {
  document_id: string;
  offset: number;
  limit: number;
  total: number;
  blocks: DocumentBlock[];
};

export type EvidenceView = {
  id: string;
  kind?: string;
  source_ids: string[];
  details: Record<string, unknown>;
  document_id?: string | null;
  document_version?: number | null;
  display_name?: string | null;
  excerpt?: string | null;
  location?: Record<string, unknown> | null;
  context?: string | null;
  score?: number | null;
  rank?: number | null;
  retrieval_mode?: string | null;
  trace?: unknown;
};

export type DocumentUpload = {
  source: {
    id: string;
    display_name: string;
    kind: string;
    state: string;
    version: number;
  };
  document: DocumentView;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const detail = body.detail;
        if (typeof detail === "string") message = detail;
        else if (
          detail &&
          typeof detail === "object" &&
          "message" in detail &&
          typeof detail.message === "string"
        ) {
          message = detail.message;
        }
      }
    } catch {
      // Keep the HTTP status when the API response is not JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const documentApi = {
  upload: (workspaceId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentUpload>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/documents`,
      { method: "POST", body },
    );
  },
  list: (workspaceId: string) =>
    request<DocumentView[]>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/documents`,
    ),
  blocks: (documentId: string, offset = 0, limit = 100) =>
    request<DocumentBlocks>(
      `/api/documents/${encodeURIComponent(documentId)}/blocks?offset=${offset}&limit=${limit}`,
    ),
  evidence: (evidenceId: string) =>
    request<EvidenceView>(`/api/evidence/${encodeURIComponent(evidenceId)}`),
};

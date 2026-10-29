import axios from "axios";

export interface KnowledgeSource {
  title?: string;
  url: string;
  relevance?: number;
}

export interface KnowledgeResponse {
  query: string;
  answer: string;
  status: "answered" | "insufficient_evidence";
  sources: KnowledgeSource[];
  confidence?: number;
  needs_human: boolean;
}

export async function askKnowledgeBrain(query: string, userId?: number): Promise<KnowledgeResponse> {
  const baseUrl = process.env.KNOWLEDGE_BRAIN_URL ?? "http://localhost:8010";
  const serviceToken = process.env.KNOWLEDGE_SERVICE_TOKEN ?? "internal-knowledge-secret-token";
  const params: any = { q: query };
  if (userId !== undefined) params.user_id = userId;
  try {
    const resp = await axios.get<KnowledgeResponse>(`${baseUrl}/internal/ask`, {
      params,
      headers: { "X-Knowledge-Service-Key": serviceToken },
      timeout: 5000,
    });
    return resp.data;
  } catch {
    const fallback = await axios.get<KnowledgeResponse>(`${baseUrl}/ask`, {
      params: { q: query },
      headers: { "X-Knowledge-Service-Key": serviceToken },
      timeout: 5000,
    });
    return fallback.data;
  }
}


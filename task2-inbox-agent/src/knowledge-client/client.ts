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

export async function askKnowledgeBrain(query: string): Promise<KnowledgeResponse> {
  const baseUrl = process.env.KNOWLEDGE_BRAIN_URL ?? "http://localhost:8010";
  const resp = await axios.get<KnowledgeResponse>(`${baseUrl}/ask`, {
    params: { q: query },
    timeout: 5000,
  });
  return resp.data;
}

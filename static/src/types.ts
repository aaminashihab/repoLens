// ─── API Types ────────────────────────────────────────────────────────────────

interface EvidenceItem {
  file_path: string;
  line_range: string;
  symbol_name: string;
  snippet: string;
  relevance: string;
}

interface RecommendedTest {
  test_type: string;
  description: string;
}

interface AtomicHypothesis {
  hypothesis_id: string;
  statement: string;
  status: string;
}

interface VerificationReport {
  claim: string;
  verification_status: "Likely True" | "Likely False" | "Uncertain";
  confidence_score: number;
  atomic_hypotheses: AtomicHypothesis[];
  supporting_evidence: EvidenceItem[];
  contradicting_evidence: EvidenceItem[];
  potential_risks: string[];
  missing_information: string[];
  recommended_tests: RecommendedTest[];
}

interface IndexEntry {
  index_id: string;
  repo_url: string;
  vector_count: number;
  created_at: string;
}

interface IndexJobStatus {
  status: "processing" | "completed" | "failed";
  error?: string;
  index_id?: string;
}

interface IndexStartResponse {
  index_id: string;
  status: string;
}

interface HealthResponse {
  status: "ok" | "error";
}

interface StreamSource {
  file_path: string;
  symbol_name: string;
}

interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
}
